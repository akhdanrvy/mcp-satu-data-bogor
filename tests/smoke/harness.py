"""Harness smoke test ST-01..05 (docs/RENCANA_LANGKAH11.md, disetujui Akhdan 17 Nov 2026).

Logika ada di sini agar bisa diuji offline (tests/unit/test_smoke_harness.py, portal tiruan
respx). Yang memanggil portal asli hanya `tests/smoke/test_smoke.py`, atas izin Akhdan.

Keputusan Akhdan (17 Nov 2026):
1. Cache smoke terpisah: var/smoke/<tanggal>/cache (cache bersih → setiap kartu lewat jalur live).
2. ST-01 memakai `OpenDataJabarAdapter.catalog_total()` (satu GET, per_page=1).
3. MCP_MAX_RETRIES=2 selama smoke (satu retry 5xx; 429 menghentikan sesi).
4. F-08 dan O-06 kriteria lulus. F-07/F-08 dihitung RELATIF terhadap `periods_available`
   dari get_dataset_info di sesi yang sama; O-06 mutlak (LEVEL_NOT_AVAILABLE).
5. Nama laporan: tanggal WIB saat eksekusi; bila sudah ada → akhiran -2, -3, dst. (tidak menimpa).

Aturan akses (AK-01..06) dikunci di `GuardedTransport`, di bawah adaptor asli:
- hanya GET ke `/datasets` (per_page=1) dan `/bigdata/{schema}/{table}`; `/view` selalu ditolak;
- anggaran keras 60 permintaan (retry ikut dihitung), ditolak sebelum dikirim;
- setelah 429 semua permintaan berikutnya ditolak dan sesi dihentikan.
Smoke tidak pernah menulis katalog dan tidak menjalankan sinkron (AK-06).

Laporan di docs/ tidak memuat nilai data; artefak mentah (respons tool, berkas data_ref)
disimpan di var/smoke/<run>/ (di-gitignore).
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import secrets
import subprocess
import threading
import time
import traceback
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import cache
from itertools import pairwise
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

from mcp_bogor.config import CONTRACT_VERSION, USER_AGENT_PLACEHOLDER, WIB, Settings
from mcp_bogor.health import catalog_status
from mcp_bogor.models.common import contains_formatted_number
from mcp_bogor.services import catalog as catalog_service
from mcp_bogor.services.data import SERIES_PER_PAGE, label_key
from mcp_bogor.services.metric_cards import MetricCard, load_cards
from mcp_bogor.storage import db

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "golden_set" / "golden_set_v0.2.jsonl"
DOCS_DIR = ROOT / "docs"
SMOKE_ROOT = ROOT / "var" / "smoke"

MAX_REQUESTS = 60
SMOKE_DELAY_S = 0.3
SMOKE_MAX_RETRIES = 2
MIN_GAP_S = 0.2
ROUNDS = 3
SEARCH_P95_S = 0.5
DATA_P95_S = 1.0
VALUE_TOLERANCE = 1e-9

# Path relatif terhadap base URL portal yang boleh dikirim smoke.
ALLOWED_PATHS = (
    re.compile(r"/datasets"),
    re.compile(r"/bigdata/[a-z0-9_-]+/[a-z0-9_-]+"),
)

# Kata kunci search golden set (plan bagian 4, ST-02).
SEARCH_N03 = "pendapatan asli daerah"
SEARCH_F08 = "kemiskinan"
SEARCH_O01 = "aduan warga"
# O-06: filter wilayah di bawah kota pada dataset tingkat kota.
O06_FILTER = {"kecamatan": ["BOGOR BARAT"]}

CHECK_COMMAND = '$env:MCP_CACHE_DIR="{cache}"; uv run python -m mcp_bogor.metric_cards --check'


class SmokePrerequisiteError(RuntimeError):
    """Prasyarat gagal; tidak ada permintaan yang dikirim."""


class SmokeAborted(RuntimeError):
    """Sesi dihentikan (429 dari portal); laporan tetap ditulis."""


class GuardRejected(RuntimeError):
    """Permintaan ditolak GuardedTransport sebelum dikirim.

    Sengaja bukan httpx.TransportError: adaptor tidak mengulang, tool mengembalikan INTERNAL,
    dan harness menghentikan sesi.
    """


class ReportRefused(RuntimeError):
    """Teks laporan memuat angka berformat atau bentuk khas nilai measure (KT-03)."""


# ---------------------------------------------------------------- GuardedTransport


@dataclass(frozen=True)
class RequestRecord:
    path: str  # relatif terhadap base URL, tanpa query
    status: int | None
    sent_at: float  # monotonic
    duration_s: float
    error: str | None = None


class GuardedTransport(httpx.AsyncBaseTransport):
    """Transport pembungkus di bawah adaptor smoke: allowlist, anggaran, berhenti pada 429."""

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport,
        *,
        base_path: str = "",
        budget: int = MAX_REQUESTS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 0 < budget <= MAX_REQUESTS:
            raise ValueError(f"anggaran harus 1..{MAX_REQUESTS}")
        self._inner = inner
        self._base_path = base_path.rstrip("/")
        self.budget = budget
        self._monotonic = monotonic
        self.records: list[RequestRecord] = []
        self.rejections: list[tuple[str, str]] = []  # (path, alasan)
        self.rate_limited = False

    @property
    def sent(self) -> int:
        return len(self.records)

    def _relative(self, path: str) -> str | None:
        if not path.startswith(self._base_path + "/"):
            return None
        return path[len(self._base_path) :]

    def rejection_reason(self, request: httpx.Request) -> str | None:
        path = request.url.path
        if request.method != "GET":
            return "metode selain GET (AK-01)"
        if "/view" in path:
            return "path memuat /view (AK-02)"
        relative = self._relative(path)
        if relative is None or not any(p.fullmatch(relative) for p in ALLOWED_PATHS):
            return "path di luar allowlist smoke"
        if relative == "/datasets" and request.url.params.get("per_page") != "1":
            return "/datasets hanya boleh per_page=1 (tanpa sinkron, AK-06)"
        if self.rate_limited:
            return "sesi dihentikan setelah 429"
        if self.sent >= self.budget:
            return f"anggaran {self.budget} permintaan habis"
        return None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        reason = self.rejection_reason(request)
        relative = self._relative(request.url.path) or request.url.path
        if reason is not None:
            self.rejections.append((relative, reason))
            raise GuardRejected(reason)
        started = self._monotonic()
        try:
            response = await self._inner.handle_async_request(request)
        except Exception as exc:
            elapsed = self._monotonic() - started
            self.records.append(RequestRecord(relative, None, started, elapsed, type(exc).__name__))
            raise
        self.records.append(
            RequestRecord(relative, response.status_code, started, self._monotonic() - started)
        )
        if response.status_code == 429:
            self.rate_limited = True
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()

    def min_gap_s(self) -> float | None:
        starts = sorted(r.sent_at for r in self.records)
        gaps = [b - a for a, b in pairwise(starts)]
        return min(gaps) if gaps else None

    def statuses(self) -> Counter[str]:
        return Counter(
            str(r.status) if r.status is not None else (r.error or "-") for r in self.records
        )


# ---------------------------------------------------------------- rencana dan prasyarat


@dataclass(frozen=True)
class PlannedRequest:
    st: str
    label: str
    path: str
    params: Mapping[str, Any]
    note: str


@dataclass
class Prerequisites:
    problems: list[str] = field(default_factory=list)
    cards: list[MetricCard] = field(default_factory=list)
    plan: list[PlannedRequest] = field(default_factory=list)
    catalog: dict[str, Any] = field(default_factory=dict)
    user_agent_ok: bool = False

    @property
    def ok(self) -> bool:
        return not self.problems


def build_plan(
    settings: Settings, cards: Sequence[MetricCard], conn: Any
) -> tuple[list[PlannedRequest], list[str]]:
    """Daftar GET yang direncanakan (path lengkap dari katalog lokal) + masalah."""
    problems: list[str] = []
    plan = [
        PlannedRequest(
            "ST-01", "katalog", "/datasets", {"page": 1, "per_page": 1}, "pagination.total_data"
        )
    ]
    for card in cards:
        dataset = catalog_service.get_dataset(conn, card.slug)
        if dataset is None:
            problems.append(f"{card.id}: slug tidak ada di katalog lokal")
            continue
        if not dataset.schema or not dataset.table:
            problems.append(f"{card.id}: schema/table kosong di katalog lokal")
            continue
        path = f"/bigdata/{dataset.schema}/{dataset.table}"
        plan.append(
            PlannedRequest(
                "ST-02",
                card.id,
                path,
                {"page": 1, "per_page": settings.dictionary_sample_rows},
                "probe kamus (get_dataset_info)",
            )
        )
        plan.append(
            PlannedRequest(
                "ST-02",
                card.id,
                path,
                {"page": 1, "per_page": SERIES_PER_PAGE},
                "seri (get_dataset_data pertama; halaman tambahan bila > 1.000 baris)",
            )
        )
    return plan, problems


def check_prerequisites(settings: Settings) -> Prerequisites:
    """Tanpa jaringan: User-Agent, katalog lokal, kartu, golden set, rencana ≤ 60."""
    pre = Prerequisites()
    ua = settings.user_agent
    pre.user_agent_ok = bool(ua.strip()) and USER_AGENT_PLACEHOLDER not in ua
    if not pre.user_agent_ok:
        pre.problems.append("MCP_USER_AGENT kosong atau masih placeholder (AK-05)")
    if not GOLDEN.is_file():
        pre.problems.append(f"golden set tidak ada: {GOLDEN.name}")
    try:
        pre.cards = load_cards(settings.metric_cards_dir)
    except Exception as exc:  # MetricCardError / MetricCardFileError
        pre.problems.append(f"kartu metrik tidak sah: {type(exc).__name__}")
    pre.catalog = catalog_status(settings.db_path)
    if not pre.catalog["ready"]:
        pre.problems.append("katalog lokal belum siap (jalankan sinkron manual dulu)")
        return pre
    conn = db.connect_read(settings.db_path)
    try:
        pre.plan, problems = build_plan(settings, pre.cards, conn)
    finally:
        conn.close()
    pre.problems.extend(problems)
    if len(pre.plan) > MAX_REQUESTS:
        pre.problems.append(f"rencana {len(pre.plan)} permintaan > {MAX_REQUESTS}")
    return pre


def run_slot(docs_dir: Path, smoke_root: Path, day: date) -> tuple[Path, Path, str]:
    """Nama laporan dan folder run yang belum dipakai: <tanggal>, lalu -2, -3, ... (keputusan 5)."""
    n = 1
    while True:
        suffix = day.isoformat() if n == 1 else f"{day.isoformat()}-{n}"
        report = docs_dir / f"SMOKE_REPORT_{suffix}.md"
        run_dir = smoke_root / suffix
        if not report.exists() and not run_dir.exists():
            return report, run_dir, suffix
        n += 1


def check_command(run_dir: Path) -> str:
    try:
        cache = (run_dir / "cache").relative_to(ROOT).as_posix()
    except ValueError:
        cache = (run_dir / "cache").as_posix()
    return CHECK_COMMAND.format(cache=cache)


def smoke_settings(base: Settings, cache_dir: Path, secret: str, public_url: str) -> Settings:
    """Penimpaan khusus smoke (plan bagian 1); sisanya dari .env laptop."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    return base.model_copy(
        update={
            "request_delay_s": max(SMOKE_DELAY_S, base.request_delay_s),
            "max_retries": SMOKE_MAX_RETRIES,
            "cache_dir": cache_dir.resolve(),
            "dataref_secret": SecretStr(secret),
            "dataref_secret_previous": SecretStr(""),
            "fileserver_public_url": public_url.rstrip("/"),
        }
    )


# ---------------------------------------------------------------- golden set


@cache
def golden() -> dict[str, dict[str, Any]]:
    cases: dict[str, dict[str, Any]] = {}
    for line in GOLDEN.read_text(encoding="utf-8").splitlines():
        if line.strip():
            case = json.loads(line)
            cases[case["id"]] = case
    return cases


def expected_flags(case_id: str) -> set[tuple[str, int]]:
    flags = set()
    for flag in golden()[case_id]["expected"].get("quality_flags", []):
        for period in flag.get("periods", [flag.get("period")]):
            flags.add((flag["code"], period))
    return flags


def golden_filters(case_id: str) -> dict[str, list[Any]]:
    raw = golden()[case_id]["expected"].get("filters") or {}
    return {k: v if isinstance(v, list) else [v] for k, v in raw.items()}


def expected_periods(
    requested: Sequence[int], available: Iterable[int]
) -> tuple[str, list[int], list[int]]:
    """Keputusan 4: status/periods_found/missing_periods relatif terhadap periods_available."""
    avail = set(available)
    found = sorted(y for y in requested if y in avail)
    missing = sorted(y for y in requested if y not in avail)
    if not missing:
        return "ok", found, []
    if found:
        return "partial", found, missing
    return "error", [], missing


PIVOT_KEYS = {
    "hari_kerja": ("hari_operasional", "HARI KERJA"),
    "hari_libur": ("hari_operasional", "HARI LIBUR"),
}


def snapshot_points(
    values: Sequence[Mapping[str, Any]], measure: str, filters: Mapping[str, Any] | None
) -> list[tuple[dict[str, Any], Any]]:
    """Snapshot golden → [(dimensi, nilai)]. Membuka bentuk pivot N-02 (hari_kerja/hari_libur)
    dan N-08 (kolom tahun); filter bernilai tunggal ikut sebagai dimensi tetap."""
    fixed: dict[str, Any] = {}
    for column, raw in (filters or {}).items():
        vals = raw if isinstance(raw, list) else [raw]
        if len(vals) == 1:
            fixed[column] = vals[0]
    points: list[tuple[dict[str, Any], Any]] = []
    for row in values:
        dims = dict(fixed)
        pivots: list[tuple[dict[str, Any], Any]] = []
        for key, value in row.items():
            if key == measure:
                pivots.append(({}, value))
            elif re.fullmatch(r"\d{4}", key):
                pivots.append(({"tahun": int(key)}, value))
            elif key in PIVOT_KEYS:
                column, label = PIVOT_KEYS[key]
                pivots.append(({column: label}, value))
            else:
                dims[key] = value
        points.extend(({**dims, **extra}, value) for extra, value in pivots)
    return points


def _same_dim(left: Any, right: Any) -> bool:
    if isinstance(left, int | float) or isinstance(right, int | float):
        try:
            return int(left) == int(right)
        except (TypeError, ValueError):
            return False
    return label_key(left) == label_key(right)


def _same_value(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return False
    if not isinstance(left, int | float) or not isinstance(right, int | float):
        return False
    return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=VALUE_TOLERANCE)


def compare_snapshot(
    points: Sequence[tuple[dict[str, Any], Any]],
    rows: Sequence[Mapping[str, Any]],
    measure: str,
) -> tuple[int, int, int]:
    """(cocok, berbeda/tidak ditemukan, ambigu). Tidak mengembalikan nilai."""
    same = differ = ambiguous = 0
    for dims, value in points:
        matches = [r for r in rows if all(_same_dim(r.get(k), v) for k, v in dims.items())]
        if len(matches) > 1:
            ambiguous += 1
        elif not matches or not _same_value(matches[0].get(measure), value):
            differ += 1
        else:
            same += 1
    return same, differ, ambiguous


# ---------------------------------------------------------------- hasil


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    warning: bool = False


@dataclass
class StResult:
    st: str
    title: str
    checks: list[Check] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)  # bagian laporan (markdown, tanpa nilai)
    ran: bool = False

    @property
    def passed(self) -> bool:
        return self.ran and all(c.ok for c in self.checks)

    def add(self, name: str, ok: bool, detail: str = "", warning: bool = False) -> Check:
        check = Check(name, bool(ok), detail, warning)
        self.checks.append(check)
        return check

    def warn(self, name: str, detail: str) -> None:
        self.add(name, True, detail, warning=True)


@dataclass
class ToolCall:
    label: str  # id kartu (atau "ST-05")
    variant: str
    tool: str
    args: dict[str, Any]
    out: dict[str, Any]
    elapsed_s: float


@dataclass
class SmokeResult:
    started_at: datetime
    finished_at: datetime | None = None
    report_path: Path | None = None
    run_dir: Path | None = None
    sts: dict[str, StResult] = field(default_factory=dict)
    aborted: str | None = None
    planned: int = 0
    requests_sent: int = 0
    check_command: str = ""
    commit: str | None = None
    server_version: str | None = None
    user_agent_ok: bool = False

    @property
    def passed(self) -> bool:
        return self.aborted is None and all(s.passed for s in self.sts.values())


ST_TITLES = {
    "ST-01": "Katalog lokal vs portal",
    "ST-02": "12 kartu lewat tool sungguhan",
    "ST-03": "data_ref lewat HTTP, KT-03 atas respons asli, pembanding golden",
    "ST-04": "Akses sopan",
    "ST-05": "Kinerja (NFR-03, p95)",
}


# ---------------------------------------------------------------- konteks sesi


def _codes(items: Iterable[Mapping[str, Any]]) -> list[str]:
    return [str(i.get("code")) for i in items]


def _years(values: Iterable[int]) -> str:
    """Daftar tahun dipisah ", " (dengan spasi: tidak berbentuk angka berformat)."""
    return ", ".join(str(v) for v in values) or "-"


def p95(samples: Sequence[float]) -> float:
    """Nearest-rank."""
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def p50(samples: Sequence[float]) -> float:
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(0.5 * len(ordered)) - 1)]


def ms(seconds: float | None) -> str:
    return "-" if seconds is None else f"{round(seconds * 1000)} ms"


class SmokeContext:
    def __init__(
        self,
        settings: Settings,
        session: Any,
        guard: GuardedTransport,
        adapter: Any,
        cards: Sequence[MetricCard],
        card_index: Any,
        run_dir: Path,
    ) -> None:
        from mcp_bogor.tools.descriptions import tool_name

        self.settings = settings
        self.session = session
        self.guard = guard
        self.adapter = adapter
        self.cards = list(cards)
        self.by_id = {c.id: c for c in cards}
        self.card_index = card_index
        self.run_dir = run_dir
        self.calls: list[ToolCall] = []
        self.info: dict[str, dict[str, Any]] = {}
        self.files: dict[tuple[str, str], dict[str, Any]] = {}  # (kartu, varian) → berkas
        self._name = lambda base: tool_name(settings.tool_prefix, base)

    async def call(
        self, base: str, args: dict[str, Any], label: str, variant: str, *, keep: bool = True
    ) -> dict[str, Any]:
        started = time.perf_counter()
        result = await self.session.call_tool(self._name(base), args)
        elapsed = time.perf_counter() - started
        out = result.structuredContent
        if out is None:
            out = json.loads(result.content[0].text)
        if keep:
            self.calls.append(ToolCall(label, variant, base, dict(args), out, elapsed))
        if self.guard.rate_limited:
            raise SmokeAborted("429 dari portal; sesi dihentikan (ST-04)")
        if self.guard.rejections:
            path, reason = self.guard.rejections[0]
            raise SmokeAborted(f"permintaan ditolak guard: {reason} ({path})")
        return out

    async def data(self, card: MetricCard, variant: str, **args: Any) -> dict[str, Any]:
        clean = {k: v for k, v in args.items() if v is not None}
        return await self.call("get_dataset_data", {"slug": card.slug, **clean}, card.id, variant)


# ---------------------------------------------------------------- ST-01


async def st01(ctx: SmokeContext, st: StResult) -> None:
    status = catalog_status(ctx.settings.db_path)
    local = status["records"]
    try:
        total = await ctx.adapter.catalog_total()
    except Exception as exc:
        st.add("total_data portal", False, f"gagal: {type(exc).__name__}")
        total = None
    if ctx.guard.rate_limited:
        raise SmokeAborted("429 dari portal pada ST-01; sesi dihentikan (ST-04)")
    if total is not None:
        st.add(
            "jumlah dataset lokal = total_data portal",
            local == total,
            "" if local == total else "katalog lokal tertinggal; sinkron ulang manual",
        )
    last = status.get("last_sync") or {}
    st.lines += [
        f"- Dataset di katalog lokal: {local}",
        f"- `pagination.total_data` portal: {total if total is not None else '-'}",
        f"- Sinkron katalog terakhir: {status.get('synced_at') or '-'}"
        f" (status `{last.get('status', '-')}`, selesai {last.get('finished_at') or '-'})",
        "- Smoke tidak menjalankan sinkron (AK-06).",
    ]


# ---------------------------------------------------------------- ST-02


@dataclass
class CardRow:
    card: str
    search: str = "-"
    info: str = "-"
    data: str = "-"
    validated: str = "-"
    notices: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    special: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ok: bool = True


def _status_text(out: Mapping[str, Any]) -> str:
    error = out.get("error")
    return f"{out['status']} ({error['code']})" if error else str(out["status"])


def _flag_text(out: Mapping[str, Any]) -> list[str]:
    by_code: dict[str, list[int]] = {}
    for flag in out.get("quality_flags") or []:
        by_code.setdefault(flag["code"], []).append(flag.get("period"))
    return [
        f"{code} ({_years(sorted(p for p in periods if p is not None))})"
        for code, periods in sorted(by_code.items())
    ]


def _record(st: StResult, row: CardRow, name: str, ok: bool, detail: str = "") -> None:
    st.add(f"{row.card} {name}", ok, detail)
    if not ok:
        row.ok = False
        row.special.append(f"GAGAL {name}" + (f": {detail}" if detail else ""))


def _periods_variant_check(
    st: StResult,
    row: CardRow,
    name: str,
    out: Mapping[str, Any],
    requested: Sequence[int],
    available: Sequence[int],
    *,
    require_ok: bool,
) -> None:
    status, found, missing = expected_periods(requested, available)
    if require_ok and status != "ok":
        _record(st, row, name, False, "periode golden tidak lagi ada di periods_available")
        return
    if status == "error":
        error = out.get("error") or {}
        ok = out["status"] == "error" and error.get("code") == "PERIOD_NOT_AVAILABLE"
    else:
        result = out.get("result") or {}
        ok = (
            out["status"] == status
            and out.get("error") is None
            and result.get("periods_found") == found
            and result.get("missing_periods") == missing
        )
    detail = (
        f"harapan {status}, found [{_years(found)}], missing [{_years(missing)}];"
        f" hasil {_status_text(out)}"
    )
    _record(st, row, name, ok, "" if ok else detail)
    if ok:
        row.special.append(f"{name}: {status}, missing [{_years(missing)}]")


def _flags_check(st: StResult, row: CardRow, case_id: str, out: Mapping[str, Any]) -> None:
    expected = expected_flags(case_id)
    actual = {(f["code"], f.get("period")) for f in out.get("quality_flags") or []}
    missing = expected - actual
    ok = out["status"] == "ok" and not missing
    detail = (
        ""
        if ok
        else "tanda mutu golden hilang: "
        + ", ".join(f"{c} {p}" for c, p in sorted(missing, key=lambda f: (f[1], f[0])))
    )
    _record(st, row, f"{case_id} quality_flags", ok, detail)
    extra = actual - expected
    if extra:
        text = ", ".join(f"{c} {p}" for c, p in sorted(extra, key=lambda f: (f[1] or 0, f[0])))
        row.warnings.append(f"{case_id}: tanda mutu tambahan {text}")
        st.warn(f"{row.card} {case_id} tanda mutu tambahan", text)


async def _search_first_is(
    ctx: SmokeContext, st: StResult, row: CardRow, case_id: str, query: str
) -> None:
    card = ctx.by_id[row.card]
    out = await ctx.call("search_datasets", {"query": query}, card.id, case_id)
    items = (out.get("result") or {}).get("items") or []
    ok = bool(items) and items[0]["slug"] == card.slug and items[0].get("metric_card") == card.id
    _record(st, row, f"{case_id} search peringkat 1", ok)


async def special_km01(ctx: SmokeContext, st: StResult, row: CardRow, info: Any, data: Any) -> None:
    await _search_first_is(ctx, st, row, "N-03", SEARCH_N03)


def _quality_case(case_id: str) -> Callable[..., Awaitable[None]]:
    async def run(ctx: SmokeContext, st: StResult, row: CardRow, info: Any, data: Any) -> None:
        filters = golden_filters(case_id)
        if filters:
            out = await ctx.data(ctx.by_id[row.card], case_id, filters=filters)
        else:
            out = data  # "data semua tahun" = panggilan dasar (0 GET tambahan)
        _flags_check(st, row, case_id, out)

    return run


def _ok_case(case_id: str) -> Callable[..., Awaitable[None]]:
    """Kasus N-*: status ok; periode relatif terhadap periods_available (wajib semua ada)."""

    async def run(ctx: SmokeContext, st: StResult, row: CardRow, info: Any, data: Any) -> None:
        exp = golden()[case_id]["expected"]
        filters = golden_filters(case_id)
        periods = exp.get("periods_requested")
        if periods is None and "tahun" in filters:
            periods = filters.pop("tahun")
        available = (info.get("result") or {}).get("periods_available") or []
        if periods is None:
            out = data
            result = out.get("result") or {}
            ok = out["status"] == "ok" and set(exp.get("periods_found", [])) <= set(
                result.get("periods_found") or []
            )
            _record(st, row, f"{case_id} ok", ok, "" if ok else _status_text(out))
            if ok:
                row.special.append(f"{case_id}: ok")
            return
        out = await ctx.data(ctx.by_id[row.card], case_id, periods=periods, filters=filters or None)
        _periods_variant_check(st, row, case_id, out, periods, available, require_ok=True)
        if "hari_operasional" in filters and out["status"] == "ok":
            applied = (out.get("result") or {}).get("filters_applied", {})
            _record(
                st,
                row,
                f"{case_id} filters_applied",
                applied.get("hari_operasional") == filters["hari_operasional"],
            )

    return run


async def special_km06(ctx: SmokeContext, st: StResult, row: CardRow, info: Any, data: Any) -> None:
    card = ctx.by_id[row.card]
    available = (info.get("result") or {}).get("periods_available") or []
    f07 = golden()["F-07"]["expected"]["periods_requested"]
    out = await ctx.data(card, "F-07", periods=f07)
    _periods_variant_check(st, row, "F-07", out, f07, available, require_ok=False)
    f08 = golden()["F-08"]["expected"]["periods_requested"]
    out = await ctx.data(card, "F-08", periods=f08)
    _periods_variant_check(st, row, "F-08", out, f08, available, require_ok=False)
    out = await ctx.data(card, "O-06", filters=O06_FILTER)
    error = out.get("error") or {}
    ok = (
        out["status"] == "error"
        and error.get("code") == "LEVEL_NOT_AVAILABLE"
        and out.get("result") is None
        and not contains_formatted_number(json.dumps(out, ensure_ascii=False))
    )
    _record(st, row, "O-06 LEVEL_NOT_AVAILABLE", ok, "" if ok else _status_text(out))
    if ok:
        row.special.append("O-06: LEVEL_NOT_AVAILABLE")


async def special_km07(ctx: SmokeContext, st: StResult, row: CardRow, info: Any, data: Any) -> None:
    out = await ctx.call("search_datasets", {"query": SEARCH_F08}, row.card, "F-08")
    pair = {ctx.by_id["KM-06"].slug, ctx.by_id["KM-07"].slug} if "KM-06" in ctx.by_id else set()
    ok = any(
        n.get("code") == "DIFFERENT_DEFINITION" and set(n.get("datasets") or []) == pair
        for n in out.get("notices") or []
    )
    _record(st, row, "F-08 notice DIFFERENT_DEFINITION", ok and bool(pair))


async def special_km10(ctx: SmokeContext, st: StResult, row: CardRow, info: Any, data: Any) -> None:
    await _ok_case("N-02")(ctx, st, row, info, data)
    await _quality_case("Q-01")(ctx, st, row, info, data)


async def special_km12(ctx: SmokeContext, st: StResult, row: CardRow, info: Any, data: Any) -> None:
    out = await ctx.call("search_datasets", {"query": SEARCH_O01}, row.card, "O-01")
    pinned = ctx.card_index.pinned_slug(catalog_service.query_tokens(SEARCH_O01))
    _record(st, row, "O-01 tanpa pemasangan kartu", pinned is None)
    items = (out.get("result") or {}).get("items") or []
    slugs = [i["slug"] for i in items]
    card = ctx.by_id[row.card]
    rank = slugs.index(card.slug) + 1 if card.slug in slugs else None
    row.special.append(f"O-01: tanpa pin; peringkat {card.id} {rank or 'tidak muncul'}")


SPECIAL: dict[str, Callable[..., Awaitable[None]]] = {
    "KM-01": special_km01,
    "KM-02": _quality_case("Q-02"),
    "KM-03": _quality_case("Q-03"),
    "KM-04": _ok_case("N-04"),
    "KM-05": _ok_case("N-05"),
    "KM-06": special_km06,
    "KM-07": special_km07,
    "KM-08": _ok_case("N-06"),
    "KM-09": _ok_case("N-07"),
    "KM-10": special_km10,
    "KM-11": _ok_case("N-08"),
    "KM-12": special_km12,
}


async def st02(ctx: SmokeContext, st: StResult) -> list[CardRow]:
    rows: list[CardRow] = []
    for card in ctx.cards:
        row = CardRow(card.id)
        rows.append(row)
        usable = card.status != "perlu_konfirmasi"

        # 1. search dengan sinonim pertama
        out = await ctx.call("search_datasets", {"query": card.sinonim[0]}, card.id, "search")
        items = (out.get("result") or {}).get("items") or []
        first = items[0] if items else {}
        expected_card = card.id if usable else None
        ok = first.get("slug") == card.slug and first.get("metric_card") == expected_card
        row.search = "peringkat 1" if ok else _status_text(out)
        _record(st, row, "search sinonim pertama", ok)

        # 2. info
        info = await ctx.call("get_dataset_info", {"slug": card.slug}, card.id, "info")
        ctx.info[card.id] = info
        row.info = _status_text(info)
        result = info.get("result") or {}
        _record(st, row, "info status ok", info["status"] == "ok")
        roles = {c["name"]: c["role"] for c in result.get("columns") or []}
        _record(st, row, "kolom_nilai measure", roles.get(card.kolom_nilai) == "measure")
        bad_dims = [d for d in card.kolom_dimensi if roles.get(d) != "dimension"]
        _record(st, row, "kolom dimensi dimension", not bad_dims, ", ".join(bad_dims))
        satuan = (result.get("satuan") or "").strip().casefold()
        _record(st, row, "satuan cocok", satuan == card.satuan.strip().casefold())
        validated = result.get("dictionary_validated")
        row.validated = str(validated).lower()
        info_notices = _codes(info.get("notices") or [])
        if card.status == "kamus_belum_tervalidasi":
            ok = validated is False and "DICTIONARY_NOT_VALIDATED" in info_notices
        else:
            ok = validated is True
        _record(st, row, f"dictionary_validated sesuai status {card.status}", ok)
        available = result.get("periods_available") or []
        expected = list(range(card.periode_tersedia.awal, card.periode_tersedia.akhir + 1))
        if available != expected:
            text = (
                f"periods_available [{_years(available)}] ≠ periode_tersedia [{_years(expected)}]"
            )
            row.warnings.append(text)
            st.warn(f"{card.id} periode", text)

        # 3. data tanpa periode
        data = await ctx.data(card, "dasar")
        row.data = _status_text(data)
        row.notices = sorted(set(_codes(data.get("notices") or [])) | set(info_notices))
        row.flags = _flag_text(data)
        truncated_only = (
            data["status"] == "partial" and data.get("error") is None and data.get("truncated")
        )
        if truncated_only:
            row.warnings.append("partial karena truncated")
            st.warn(f"{card.id} data truncated", "partial karena truncated")
        ok = data["status"] == "ok" or bool(truncated_only)
        if card.status == "kamus_belum_tervalidasi":
            ok = ok and "DICTIONARY_NOT_VALIDATED" in _codes(data.get("notices") or [])
        _record(st, row, "data dasar ok", ok, "" if ok else _status_text(data))

        # 4. kasus golden khusus kartu
        special = SPECIAL.get(card.id)
        if special is not None:
            await special(ctx, st, row, info, data)
    st.lines += [
        "| Kartu | search | info | data | dictionary_validated | notice | quality flag (tahun) "
        "| kasus golden | peringatan |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        st.lines.append(
            f"| {row.card}{'' if row.ok else ' ✗'} | {row.search} | {row.info} | {row.data} "
            f"| {row.validated} | {', '.join(row.notices) or '-'} | {'; '.join(row.flags) or '-'} "
            f"| {'; '.join(row.special) or '-'} | {'; '.join(row.warnings) or '-'} |"
        )
    missing = sorted(set(SPECIAL) - set(ctx.by_id))
    if missing:
        st.add("kartu golden ada", False, ", ".join(missing))
    return rows


# ---------------------------------------------------------------- ST-03


def leak_paths(payload: Any, values: Sequence[Any], prefix: str = "$") -> list[str]:
    """Path field yang memuat bentuk khas nilai measure (tanpa nilainya)."""
    from tests.contract.test_no_values import find_leaks

    found: list[str] = []
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            found += leak_paths(value, values, f"{prefix}.{key}")
    elif isinstance(payload, list):
        for i, value in enumerate(payload):
            found += leak_paths(value, values, f"{prefix}[{i}]")
    elif find_leaks({"v": payload}, list(values)):
        found.append(prefix)
    return found


def measure_values(ctx: SmokeContext, card: MetricCard) -> list[Any]:
    from tests.contract.test_no_values import MEASURES

    columns = {card.kolom_nilai}
    if card.slug in MEASURES:
        columns.add(MEASURES[card.slug])
    values: list[Any] = []
    for (label, _variant), body in ctx.files.items():
        if label != card.id:
            continue
        for row in body.get("rows") or []:
            values += [row[c] for c in columns if c in row and row[c] is not None]
    return values


async def st03(ctx: SmokeContext, st: StResult) -> None:
    from tests.contract.test_data_ref_content import assert_matches_tool
    from tests.contract.test_no_values import _search_text, find_leaks

    files_dir = ctx.run_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    base = ctx.settings.fileserver_public_url
    downloaded = kt06_ok = 0
    data_calls = [
        c
        for c in ctx.calls
        if c.tool == "get_dataset_data" and (c.out.get("result") or {}).get("data_ref")
    ]
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as http:
        for n, call in enumerate(data_calls, start=1):
            url = call.out["result"]["data_ref"]["url"]
            name = f"{call.label} {call.variant}"
            if not url.startswith(base + "/d/"):
                st.add(
                    f"{name} data_ref lokal", False, "URL bukan file server lokal; tidak diunduh"
                )
                continue
            response = await http.get(url)
            if not st.add(
                f"{name} unduh 200", response.status_code == 200, str(response.status_code)
            ).ok:
                continue
            body = response.json()
            downloaded += 1
            ctx.files[(call.label, call.variant)] = body
            (files_dir / f"{n:02d}_{call.label}_{call.variant}.json").write_text(
                json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            try:
                assert_matches_tool(call.out, body)
                kt06_ok += 1
            except AssertionError:
                detail_path = ctx.run_dir / f"kt06_{n:02d}_{call.label}_{call.variant}.txt"
                detail_path.write_text(traceback.format_exc(), encoding="utf-8")
                st.add(f"{name} KT-06", False, f"tidak cocok; rincian di {detail_path.name}")
    st.add("data_ref terunduh", downloaded == len(data_calls) and downloaded > 0)

    # KT-03 atas respons asli: nilai dari berkas live, dicari di semua respons kartu itu.
    leak_rows: list[str] = []
    total_leaks = 0
    for card in ctx.cards:
        values = measure_values(ctx, card)
        if not values:
            st.add(f"{card.id} KT-03 nilai acuan", False, "tidak ada nilai measure dari berkas")
            continue
        for call in (c for c in ctx.calls if c.label == card.id):
            payload = _search_text(call.out)
            if find_leaks(payload, values):
                paths = sorted(set(leak_paths(payload, values)))
                total_leaks += 1
                leak_rows.append(f"  - {card.id} {call.tool} ({call.variant}): {', '.join(paths)}")
    st.add("KT-03 nol temuan", total_leaks == 0, f"{total_leaks} respons bocor")

    # Pembanding golden set (peringatan saja).
    golden_lines = [
        "",
        "Pembanding golden set (selisih = peringatan; nilai tidak ditulis):",
        "",
        "| Kasus | Kartu | Hasil | Titik cocok/beda/ambigu | data_updated_at snapshot | portal |",
        "|---|---|---|---|---|---|",
    ]
    by_slug = {c.slug: c for c in ctx.cards}
    for case_id, case in golden().items():
        snapshot = case.get("snapshot")
        exp = case["expected"]
        card = by_slug.get(exp.get("dataset", ""))
        if not snapshot or card is None:
            continue
        body = ctx.files.get((card.id, "dasar"))
        base_out = next(
            (c.out for c in ctx.calls if c.label == card.id and c.variant == "dasar"), {}
        )
        portal_date = ((base_out.get("provenance") or {}).get("data_updated_at") or "-")[:10]
        if body is None:
            golden_lines.append(f"| {case_id} | {card.id} | berkas dasar tidak ada | - | - | - |")
            st.warn(f"golden {case_id}", "berkas dasar tidak ada")
            continue
        measure = exp.get("measure") or card.kolom_nilai
        points = snapshot_points(snapshot["values"], measure, exp.get("filters"))
        same, differ, ambiguous = compare_snapshot(points, body.get("rows") or [], measure)
        if differ == 0 and ambiguous == 0:
            verdict = "cocok"
        elif snapshot.get("data_updated_at") == portal_date or ambiguous:
            verdict = "perlu diperiksa"
        else:
            verdict = "berbeda (perubahan data portal)"
        if verdict != "cocok":
            st.warn(f"golden {case_id}", verdict)
        golden_lines.append(
            f"| {case_id} | {card.id} | {verdict} | {same}/{differ}/{ambiguous} "
            f"| {snapshot.get('data_updated_at', '-')} | {portal_date} |"
        )
    st.lines += [
        f"- Berkas data_ref terunduh: {downloaded} dari {len(data_calls)}"
        f" (HTTP ke file server lokal, sebelum `exp`)",
        f"- KT-06 (isi berkas = respons tool): {kt06_ok} cocok dari {downloaded}",
        f"- KT-03 atas respons asli: {total_leaks} respons dengan temuan",
        *leak_rows,
        *golden_lines,
    ]


# ---------------------------------------------------------------- ST-04


def st04(ctx: SmokeContext | None, guard: GuardedTransport, planned: int, st: StResult) -> None:
    statuses = guard.statuses()
    gap = guard.min_gap_s()
    st.add("tanpa 429", statuses.get("429", 0) == 0 and not guard.rate_limited)
    st.add("jeda terkecil ≥ 200 ms", gap is None or gap >= MIN_GAP_S - 1e-6, ms(gap))
    view = [p for p, reason in guard.rejections if "/view" in reason]
    st.add("tanpa permintaan /view", not view and not any("/view" in r.path for r in guard.records))
    non_get = [p for p, reason in guard.rejections if "GET" in reason]
    st.add("hanya GET", not non_get)
    st.add(f"total ≤ {MAX_REQUESTS}", guard.sent <= MAX_REQUESTS, str(guard.sent))
    st.add("tanpa permintaan ditolak guard", not guard.rejections)
    if guard.sent != planned:
        st.warn("jumlah nyata ≠ rencana", f"{guard.sent} vs {planned}")
    st.lines += [
        f"- Permintaan ke portal: {guard.sent} (rencana {planned}, batas {MAX_REQUESTS})",
        f"- Jeda terkecil antarpermintaan: {ms(gap)} (disetel {ms(SMOKE_DELAY_S)},"
        f" syarat ≥ {ms(MIN_GAP_S)})",
        "- Status HTTP: " + (", ".join(f"`{k}` x {v}" for k, v in sorted(statuses.items())) or "-"),
        f"- Ditolak guard sebelum dikirim: {len(guard.rejections)}"
        + "".join(f"\n  - `{p}`: {reason}" for p, reason in guard.rejections),
    ]


# ---------------------------------------------------------------- ST-05


async def st05(ctx: SmokeContext, st: StResult) -> None:
    queries = [s for card in ctx.cards for s in card.sinonim]
    search_times: list[float] = []
    for _ in range(ROUNDS):
        for query in queries:
            started = time.perf_counter()
            await ctx.call("search_datasets", {"query": query}, "ST-05", "search", keep=False)
            search_times.append(time.perf_counter() - started)
    sent_before = ctx.guard.sent
    data_times: list[float] = []
    for _ in range(ROUNDS):
        for card in ctx.cards:
            started = time.perf_counter()
            out = await ctx.call(
                "get_dataset_data", {"slug": card.slug}, "ST-05", "data", keep=False
            )
            data_times.append(time.perf_counter() - started)
            if out["status"] == "error":
                st.add(f"{card.id} data dari cache", False, _status_text(out))
    st.add(
        f"search p95 < {ms(SEARCH_P95_S)}", p95(search_times) < SEARCH_P95_S, ms(p95(search_times))
    )
    st.add(f"data cache p95 < {ms(DATA_P95_S)}", p95(data_times) < DATA_P95_S, ms(p95(data_times)))
    st.add("data cache tanpa GET", ctx.guard.sent == sent_before, str(ctx.guard.sent - sent_before))
    st.lines += [
        f"- search_datasets: semua sinonim kartu x {ROUNDS} putaran;"
        f" p50 {ms(p50(search_times))}, p95 {ms(p95(search_times))}"
        f" (syarat < {ms(SEARCH_P95_S)})",
        f"- get_dataset_data dari cache: semua kartu x {ROUNDS} putaran;"
        f" p50 {ms(p50(data_times))}, p95 {ms(p95(data_times))}"
        f" (syarat < {ms(DATA_P95_S)}); GET tambahan {ctx.guard.sent - sent_before}",
    ]


# ---------------------------------------------------------------- file server lokal


class LocalFileServer:
    """uvicorn file server data_ref di 127.0.0.1:<port acak> dalam proses yang sama."""

    def __init__(self, settings: Settings) -> None:
        import uvicorn

        from mcp_bogor.fileserver.app import create_app

        config = uvicorn.Config(
            create_app(settings),
            host="127.0.0.1",
            port=0,
            log_config=None,
            access_log=False,
            lifespan="off",
            server_header=False,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self) -> str:
        self._thread.start()
        deadline = time.monotonic() + 15
        while not self._server.started:
            if time.monotonic() > deadline or not self._thread.is_alive():
                raise SmokePrerequisiteError("file server lokal gagal start")
            time.sleep(0.02)
        port = self._server.servers[0].sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(15)


# ---------------------------------------------------------------- laporan


def report_violations(text: str, values: Sequence[Any]) -> list[str]:
    """Pelindung isi laporan (plan bagian 5): angka berformat atau bentuk khas nilai measure,
    dengan aturan yang sama dengan KT-03. Mengembalikan jenis pelanggaran + nomor baris saja,
    tanpa nilainya, agar positif palsu (mis. durasi yang kebetulan sama dengan sebuah nilai)
    bisa dinilai Akhdan tanpa membuka data."""
    from tests.contract.test_no_values import find_leaks

    formatted: list[int] = []
    leaked: list[int] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if contains_formatted_number(line):
            formatted.append(number)
        if values and find_leaks({"baris": line}, list(values)):
            leaked.append(number)
    problems = []
    if formatted:
        problems.append(f"angka berformat Indonesia di baris {_years(formatted)}")
    if leaked:
        problems.append(f"bentuk khas nilai measure di baris {_years(leaked)}")
    return problems


def render_report(result: SmokeResult) -> str:
    def mark(st: StResult) -> str:
        if not st.ran:
            return "TIDAK DIJALANKAN"
        return "LULUS" if st.passed else "GAGAL"

    started = result.started_at.isoformat(timespec="seconds")
    finished = result.finished_at.isoformat(timespec="seconds") if result.finished_at else "-"
    name = result.report_path.stem.removeprefix("SMOKE_REPORT_") if result.report_path else ""
    lines = [
        f"# Laporan smoke test {name}",
        "",
        "Dibuat otomatis oleh `tests/smoke/harness.py` (RENCANA_LANGKAH11). Laporan ini tidak "
        "memuat nilai data; artefak mentah ada di folder run di bawah `var/smoke/` (di-gitignore).",
        "",
        "## Ringkasan",
        "",
        f"- Hasil keseluruhan: **{'LULUS' if result.passed else 'GAGAL'}**",
        f"- Waktu (WIB): {started} s.d. {finished}",
        f"- Versi: `contract_version` {CONTRACT_VERSION}, paket {result.server_version or '-'},"
        f" commit `{result.commit or '-'}`",
        f"- User-Agent: {'terisi' if result.user_agent_ok else 'TIDAK SAH'}",
        f"- Folder run: `{_rel(result.run_dir)}`",
        f"- Permintaan ke portal: {result.requests_sent} (rencana {result.planned})",
    ]
    if result.aborted:
        lines.append(f"- **Sesi dihentikan:** {result.aborted}")
    lines += ["", "| ST | Judul | Hasil |", "|---|---|---|"]
    for key in sorted(result.sts):
        st = result.sts[key]
        lines.append(f"| {key} | {st.title} | {mark(st)} |")
    for key in sorted(result.sts):
        st = result.sts[key]
        lines += ["", f"## {key}: {st.title} ({mark(st)})", ""]
        lines += st.lines
        failed = [c for c in st.checks if not c.ok]
        warnings = [c for c in st.checks if c.warning]
        if failed:
            lines += ["", "Gagal:", ""]
            lines += [f"- {c.name}" + (f": {c.detail}" if c.detail else "") for c in failed]
        if warnings:
            lines += ["", "Peringatan:", ""]
            lines += [f"- {c.name}: {c.detail}" for c in warnings]
        if st.ran and not failed:
            lines += ["", "Semua pemeriksaan lulus."]
    lines += [
        "",
        "## Setelah smoke: pemeriksa kartu metrik",
        "",
        "Jalankan di PowerShell dari akar repo, lalu salin hasilnya ke bawah ini:",
        "",
        "```powershell",
        result.check_command,
        "```",
        "",
        'Target: kode keluar 0, hanya peringatan "belum diperiksa".',
        "",
    ]
    return "\n".join(lines)


def _rel(path: Path | None) -> str:
    if path is None:
        return "-"
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def write_report(result: SmokeResult, all_values: Sequence[Any]) -> Path:
    """Tulis laporan ke docs/ setelah lolos pelindung isi; bila tidak, simpan di folder run."""
    assert result.report_path is not None and result.run_dir is not None
    text = render_report(result)
    problems = report_violations(text, all_values)
    if problems:
        refused = result.run_dir / "SMOKE_REPORT_DITOLAK.md"
        refused.write_text(text, encoding="utf-8")
        raise ReportRefused(
            f"laporan ditolak pelindung isi ({'; '.join(problems)}); draf di {_rel(refused)}"
        )
    result.report_path.parent.mkdir(parents=True, exist_ok=True)
    result.report_path.write_text(text, encoding="utf-8")
    return result.report_path


def _commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


# ---------------------------------------------------------------- sesi


async def run_smoke(
    base: Settings,
    *,
    inner_transport: httpx.AsyncBaseTransport | None = None,
    docs_dir: Path = DOCS_DIR,
    smoke_root: Path = SMOKE_ROOT,
    now: Callable[[], datetime] = lambda: datetime.now(WIB),
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Any] = asyncio.sleep,
) -> SmokeResult:
    """Jalankan ST-01..05 dan tulis laporan. inner_transport=None → portal asli."""
    from mcp.shared.memory import create_connected_server_and_client_session

    from mcp_bogor.adapters.opendata_jabar import OpenDataJabarAdapter
    from mcp_bogor.health import server_version
    from mcp_bogor.runtime import build_deps
    from mcp_bogor.server import build_server

    pre = check_prerequisites(base)
    if not pre.ok:
        raise SmokePrerequisiteError("; ".join(pre.problems))

    started = now()
    report_path, run_dir, _suffix = run_slot(docs_dir, smoke_root, started.date())
    run_dir.mkdir(parents=True)
    result = SmokeResult(
        started_at=started,
        report_path=report_path,
        run_dir=run_dir,
        planned=len(pre.plan),
        check_command=check_command(run_dir),
        commit=_commit(),
        server_version=server_version(),
        user_agent_ok=pre.user_agent_ok,
    )
    result.sts = {key: StResult(key, title) for key, title in ST_TITLES.items()}

    secret = secrets.token_urlsafe(48)
    settings = smoke_settings(base, run_dir / "cache", secret, "http://127.0.0.1")
    fileserver = LocalFileServer(settings)
    settings = settings.model_copy(update={"fileserver_public_url": fileserver.start()})
    guard = GuardedTransport(
        inner_transport or httpx.AsyncHTTPTransport(),
        base_path=urlsplit(settings.source_base_url).path,
        monotonic=monotonic,
    )
    adapter = OpenDataJabarAdapter(settings, transport=guard, monotonic=monotonic, sleep=sleep)
    ctx: SmokeContext | None = None
    try:
        deps, _report = build_deps(settings, strict=True, adapter=adapter)
        async with create_connected_server_and_client_session(build_server(deps)) as session:
            ctx = SmokeContext(
                settings, session, guard, adapter, pre.cards, deps.metric_cards, run_dir
            )
            try:
                for key, step in (("ST-01", st01), ("ST-02", st02), ("ST-03", st03)):
                    st = result.sts[key]
                    st.ran = True
                    await step(ctx, st)
                st = result.sts["ST-05"]
                st.ran = True
                await st05(ctx, st)
            except SmokeAborted as exc:
                result.aborted = str(exc)
                for st in result.sts.values():
                    if st.ran and not st.checks:
                        st.add("selesai", False, "sesi dihentikan")
    finally:
        await adapter.aclose()
        fileserver.stop()
    st = result.sts["ST-04"]
    st.ran = True
    st04(ctx, guard, len(pre.plan), st)
    result.requests_sent = guard.sent
    result.finished_at = now()

    values: list[Any] = []
    if ctx is not None:
        with (run_dir / "responses.jsonl").open("w", encoding="utf-8") as fh:
            for call in ctx.calls:
                fh.write(json.dumps(call.__dict__, ensure_ascii=False, default=str) + "\n")
        for card in ctx.cards:
            values += measure_values(ctx, card)
    write_report(result, values)
    return result


def run_smoke_sync(base: Settings, **kwargs: Any) -> SmokeResult:
    return asyncio.run(run_smoke(base, **kwargs))


# ---------------------------------------------------------------- dry-run


def render_dry_run(settings: Settings, pre: Prerequisites, day: date) -> str:
    report, run_dir, _ = run_slot(DOCS_DIR, SMOKE_ROOT, day)
    by_st = Counter(p.st for p in pre.plan)
    lines = [
        "SMOKE TEST ST-01..05: DRY-RUN (tidak ada permintaan yang dikirim)",
        "",
        "Prasyarat:",
        f"  [{'x' if pre.user_agent_ok else ' '}] User-Agent: "
        f"{'terisi' if pre.user_agent_ok else 'KOSONG/PLACEHOLDER'}",
        f"  [{'x' if pre.catalog.get('ready') else ' '}] Katalog lokal: "
        f"{pre.catalog.get('records', 0)} dataset, sinkron {pre.catalog.get('synced_at') or '-'}",
        f"  [{'x' if pre.cards else ' '}] Kartu metrik: {len(pre.cards)}",
        f"  [{'x' if GOLDEN.is_file() else ' '}] Golden set: {GOLDEN.name}",
    ]
    if pre.problems:
        lines += ["", "MASALAH (eksekusi akan ditolak):"]
        lines += [f"  - {p}" for p in pre.problems]
    lines += ["", "Daftar permintaan ke portal (path relatif terhadap MCP_SOURCE_BASE_URL):"]
    for i, req in enumerate(pre.plan, start=1):
        params = "&".join(f"{k}={v}" for k, v in req.params.items())
        lines.append(f"  {i:2d}. [{req.st}] {req.label:<8} GET {req.path}?{params}  ({req.note})")
    total = len(pre.plan)
    lines += [
        "",
        "Jumlah per ST: "
        + ", ".join(
            f"{st} {by_st.get(st, 0)}" for st in ("ST-01", "ST-02", "ST-03", "ST-04", "ST-05")
        ),
        f"Total direncanakan: {total}",
        f"Batas terburuk (satu retry 5xx per permintaan): {total * SMOKE_MAX_RETRIES}"
        f" (anggaran keras {MAX_REQUESTS})",
        "",
        "Aturan akses (GuardedTransport + adaptor):",
        "  - hanya GET ke /datasets (per_page=1) dan /bigdata/{schema}/{table}; /view ditolak",
        f"  - jeda {SMOKE_DELAY_S} dtk; MCP_MAX_RETRIES={SMOKE_MAX_RETRIES}; 429 -> sesi berhenti",
        f"  - anggaran keras {MAX_REQUESTS} permintaan (retry ikut dihitung)",
        "  - tanpa sinkron katalog, katalog dibaca saja (AK-06)",
        "",
        "Lingkungan eksekusi:",
        f"  - cache bersih: {_rel(run_dir / 'cache')}",
        "  - MCP_DATAREF_SECRET acak per jalan; file server 127.0.0.1:<port acak>",
        f"  - laporan: {_rel(report)}",
        "",
        "Eksekusi (atas izin Akhdan):",
        "  uv run pytest tests/smoke -m smoke",
        "Sesudahnya:",
        f"  {check_command(run_dir)}",
    ]
    return "\n".join(lines)
