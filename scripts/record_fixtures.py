"""Rekam fixture asli dari portal Satu Data Kota Bogor (Langkah 3).

Bawaan DRY-RUN: hanya mencetak daftar permintaan yang akan dikirim beserta jumlahnya.
Permintaan baru dikirim dengan flag --execute, dan hanya dijalankan oleh Akhdan.

    uv run python scripts/record_fixtures.py            # dry-run
    uv run python scripts/record_fixtures.py --execute  # merekam (atas izin)

Aturan akses dikunci di sini (AK-01..06), tidak bisa dilonggarkan lewat YAML:
- hanya GET ke /datasets, /datasets/{slug}, /bigdata/{schema}/{table}, /organisasi, /topik;
- path yang memuat "/view" selalu ditolak (AK-02);
- jeda 0,3 detik antarpermintaan; anggaran keras 60 permintaan (termasuk retry);
- 429 → langsung berhenti; 5xx → retry sekali setelah 5 detik, lalu berhenti;
- timeout 20 detik; User-Agent dari MCP_USER_AGENT dan tidak boleh placeholder.

Semua respons disanitasi sebelum ditulis: kunci akun petugas dibuang, nilai metadata
"Kontak Produsen" diganti "<redacted>", dan sisa pola email/telepon dilaporkan.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

from mcp_bogor.config import WIB, Settings
from mcp_bogor.services.normalize import parse_id_number

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGETS = ROOT / "scripts" / "fixture_targets.yaml"
DEFAULT_OUT_ROOT = ROOT / "tests" / "fixtures" / "recorded"
DEFAULT_REPORT = ROOT / "docs" / "FIXTURE_REPORT.md"

MAX_REQUESTS = 60
REQUEST_DELAY_S = 0.3
TIMEOUT_S = 20.0
RETRY_5XX_DELAY_S = 5.0
UA_PLACEHOLDER = "<kontak-tim>"
FORBIDDEN_FRAGMENT = "/view"
ALLOWED_PATHS = (
    re.compile(r"^/datasets$"),
    re.compile(r"^/datasets/[^/]+$"),
    re.compile(r"^/bigdata/[^/]+/[^/]+$"),
    re.compile(r"^/organisasi$"),
    re.compile(r"^/topik$"),
)

DROP_KEYS = frozenset({"username", "user_name", "cuid"})
CONTACT_LABEL = "kontak produsen"
VALUE_KEYS = ("value", "nilai", "isi", "content")
REDACTED = "<redacted>"
KEPT_HEADERS = ("content-type", "retry-after")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<!\d)(?:\+62|62|0)[\s.-]?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,5}(?!\d)")
LAST_PAGE_KEYS = ("total_page", "total_pages", "last_page", "page_count", "totalPage")


# ---------------------------------------------------------------- galat


class ConfigProblem(RuntimeError):
    """Skrip menolak jalan (User-Agent placeholder, folder sudah ada, target salah)."""


class AccessRuleViolation(RuntimeError):
    """Permintaan melanggar AK-01/AK-02. Ini bug skrip, bukan kondisi portal."""


class RecorderStop(RuntimeError):
    """Perekaman dihentikan; respons terakhir (bila ada) tetap disimpan."""

    def __init__(self, message: str, exchange: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.exchange = exchange


class BudgetExhausted(RecorderStop):
    pass


class RateLimitedStop(RecorderStop):
    pass


class ServerErrorStop(RecorderStop):
    pass


class NetworkStop(RecorderStop):
    pass


# ---------------------------------------------------------------- aturan akses


def check_path(path: str) -> None:
    """AK-01/AK-02: hanya path yang diizinkan; "/view" ditolak keras."""
    if FORBIDDEN_FRAGMENT in path:
        raise AccessRuleViolation(f"path memuat {FORBIDDEN_FRAGMENT!r} (AK-02): {path}")
    if not any(pattern.match(path) for pattern in ALLOWED_PATHS):
        raise AccessRuleViolation(f"path tidak diizinkan (AK-01): {path}")


def validate_user_agent(user_agent: str) -> None:
    if not user_agent.strip() or UA_PLACEHOLDER in user_agent:
        raise ConfigProblem(
            "MCP_USER_AGENT masih placeholder; isi dengan nama aplikasi dan kontak tim (AK-05)."
        )


def _now() -> datetime:
    return datetime.now(WIB)


def to_exchange(
    response: httpx.Response, path: str, params: dict[str, Any], recorded_at: datetime
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "status": response.status_code,
        "headers": {k: response.headers[k] for k in KEPT_HEADERS if k in response.headers},
    }
    try:
        body["json"] = response.json()
    except ValueError:
        body["json"] = None
        body["text"] = response.text[:2000]
    return {
        "request": {"method": "GET", "path": path, "params": params},
        "response": body,
        "recorded_at": recorded_at.isoformat(timespec="seconds"),
    }


@dataclass
class Recorder:
    """Satu-satunya jalan ke portal: GET saja, jeda, anggaran keras, aturan 429/5xx."""

    client: httpx.Client
    budget: int = MAX_REQUESTS
    delay_s: float = REQUEST_DELAY_S
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], datetime] = _now
    sent: int = 0
    incidents: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not 0 < self.budget <= MAX_REQUESTS:
            raise ConfigProblem(f"anggaran harus 1..{MAX_REQUESTS}")
        if self.delay_s < REQUEST_DELAY_S:
            raise ConfigProblem(f"jeda minimal {REQUEST_DELAY_S} detik")

    def _send(self, path: str, params: dict[str, Any]) -> httpx.Response:
        if self.sent >= self.budget:
            raise BudgetExhausted(f"anggaran {self.budget} permintaan habis sebelum {path}")
        if self.sent:
            self.sleep(self.delay_s)
        self.sent += 1
        try:
            return self.client.get(path, params=params)
        except httpx.TimeoutException as exc:
            raise NetworkStop(f"timeout pada {path}") from exc
        except httpx.HTTPError as exc:
            raise NetworkStop(f"galat jaringan pada {path}: {type(exc).__name__}") from exc

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        check_path(path)
        params = dict(params or {})
        response = self._send(path, params)
        if 500 <= response.status_code < 600:
            self.incidents.append(f"{response.status_code} pada {path}; retry sekali")
            self.sleep(RETRY_5XX_DELAY_S)
            response = self._send(path, params)
        exchange = to_exchange(response, path, params, self.clock())
        if response.status_code == 429:
            retry_after = response.headers.get("retry-after", "-")
            raise RateLimitedStop(f"429 pada {path} (Retry-After={retry_after})", exchange)
        if 500 <= response.status_code < 600:
            raise ServerErrorStop(f"{response.status_code} lagi pada {path}", exchange)
        return exchange


# ---------------------------------------------------------------- sanitasi


@dataclass
class SanitizeStats:
    removed_keys: int = 0
    redacted_contacts: int = 0


def _is_contact_label(value: Any) -> bool:
    return isinstance(value, str) and value.strip().casefold() == CONTACT_LABEL


def sanitize(obj: Any, stats: SanitizeStats) -> Any:
    """Salinan bersih: buang DROP_KEYS rekursif; redaksi nilai "Kontak Produsen".

    Bentuk metadata yang dikenali: kunci bernama "Kontak Produsen", atau objek yang salah
    satu nilainya "Kontak Produsen" (mis. {key: "Kontak Produsen", value: ...}). Pada bentuk
    kedua, kunci VALUE_KEYS diredaksi; bila tidak ada, semua nilai lain diredaksi.
    """
    if isinstance(obj, list):
        return [sanitize(item, stats) for item in obj]
    if not isinstance(obj, dict):
        return obj
    contact_entry = any(_is_contact_label(v) for v in obj.values())
    has_value_key = any(k in obj for k in VALUE_KEYS)
    out: dict[str, Any] = {}
    for key, value in obj.items():
        if key in DROP_KEYS:
            stats.removed_keys += 1
            continue
        if _is_contact_label(key) or (
            contact_entry
            and not _is_contact_label(value)
            and (key in VALUE_KEYS or not has_value_key)
        ):
            out[key] = REDACTED
            stats.redacted_contacts += 1
            continue
        out[key] = sanitize(value, stats)
    return out


@dataclass(frozen=True)
class PiiFinding:
    file: str
    json_path: str
    kind: str


def scan_pii(obj: Any, file: str, path: str = "$") -> list[PiiFinding]:
    """Pola email/telepon di nilai teks. Hanya lokasi yang dilaporkan, bukan nilainya."""
    found: list[PiiFinding] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            found.extend(scan_pii(value, file, f"{path}.{key}"))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            found.extend(scan_pii(value, file, f"{path}[{index}]"))
    elif isinstance(obj, str) and obj != REDACTED:
        if EMAIL_RE.search(obj):
            found.append(PiiFinding(file, path, "email"))
        if PHONE_RE.search(obj):
            found.append(PiiFinding(file, path, "telepon"))
    return found


# ---------------------------------------------------------------- target dan rencana


def load_targets(path: Path = DEFAULT_TARGETS) -> dict[str, Any]:
    targets = yaml.safe_load(path.read_text(encoding="utf-8"))
    for endpoint in targets.get("extra_endpoints") or []:
        check_path(endpoint)
    return targets


def golden_slugs(golden_path: Path) -> list[str]:
    """Slug unik dari expected.dataset, urut kemunculan."""
    slugs: list[str] = []
    for line in golden_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        slug = (json.loads(line).get("expected") or {}).get("dataset")
        if slug and slug not in slugs:
            slugs.append(slug)
    return slugs


@dataclass(frozen=True)
class PlannedRequest:
    group: str
    path: str
    params: dict[str, Any]
    note: str = ""
    conditional: bool = False


def build_plan(targets: dict[str, Any], slugs: list[str]) -> list[PlannedRequest]:
    catalog = targets["catalog"]
    bigdata_params = {"page": 1, "per_page": targets["bigdata"]["per_page"]}
    plan = [
        PlannedRequest(
            "katalog",
            catalog["path"],
            {"page": page, "per_page": catalog["per_page"]},
            f"perkiraan; mengikuti pagination, maks {catalog['max_pages']} halaman",
        )
        for page in range(1, catalog["expected_pages"] + 1)
    ]
    plan += [PlannedRequest("referensi", ep, {}) for ep in targets.get("extra_endpoints") or []]
    for slug in slugs:
        plan.append(PlannedRequest("golden", f"/datasets/{slug}", {}))
        plan.append(
            PlannedRequest(
                "golden",
                "/bigdata/{schema}/{table}",
                bigdata_params,
                f"schema/table dari detail {slug}",
            )
        )
    for card in targets.get("metric_cards") or []:
        note = f"{card['id']}: hanya bila kandidat di katalog tepat satu"
        plan.append(PlannedRequest("kartu", "/datasets/{kandidat}", {}, note, conditional=True))
        plan.append(
            PlannedRequest("kartu", "/bigdata/{schema}/{table}", bigdata_params, note, True)
        )
    plan.append(PlannedRequest("uji-404", f"/datasets/{targets['invalid_slug']}", {}))
    for item in plan:
        check_path(item.path.replace("{schema}/{table}", "s/t").replace("{kandidat}", "k"))
    return plan


def upper_bound(targets: dict[str, Any], plan: list[PlannedRequest]) -> int:
    """Batas atas bila katalog/bigdata lebih panjang dari perkiraan (tetap dipotong di 60)."""
    catalog = targets["catalog"]
    extra_catalog = catalog["max_pages"] - catalog["expected_pages"]
    bigdata_calls = sum(1 for p in plan if p.path.startswith("/bigdata"))
    extra_bigdata = bigdata_calls * (targets["bigdata"]["max_pages_per_dataset"] - 1)
    return len(plan) + extra_catalog + extra_bigdata


def format_dry_run(
    plan: list[PlannedRequest],
    targets: dict[str, Any],
    *,
    base_url: str,
    user_agent: str,
    out_dir: Path,
) -> str:
    lines = [
        "DRY-RUN: tidak ada permintaan yang dikirim. Tambahkan --execute untuk merekam.",
        f"Base URL   : {base_url}",
        f"User-Agent : {user_agent}"
        + (
            "   [DITOLAK saat --execute: masih placeholder]" if UA_PLACEHOLDER in user_agent else ""
        ),
        f"Keluaran   : {out_dir}"
        + ("   [SUDAH ADA: --execute akan menolak]" if out_dir.exists() else ""),
        f"Aturan     : GET saja, jeda {REQUEST_DELAY_S} dtk, anggaran keras {MAX_REQUESTS}, "
        "429 berhenti, 5xx retry sekali (5 dtk), timeout 20 dtk, '/view' ditolak",
        "",
        f"{'No':>3}  {'Kelompok':<9} {'Path':<60} Params / catatan",
    ]
    for number, item in enumerate(plan, 1):
        params = json.dumps(item.params, ensure_ascii=False) if item.params else ""
        extra = "; ".join(x for x in (params, item.note) if x)
        path = item.path if len(item.path) <= 60 else item.path[:57] + "..."
        lines.append(f"{number:>3}  {item.group:<9} {path:<60} {extra}")
    fixed = sum(1 for p in plan if not p.conditional)
    conditional = len(plan) - fixed
    lines += [
        "",
        f"Jumlah terencana: {len(plan)} permintaan ({fixed} pasti + {conditional} bersyarat).",
        f"Batas atas bila pagination lebih panjang: {upper_bound(targets, plan)}; "
        f"skrip berhenti keras sebelum permintaan ke-{MAX_REQUESTS + 1}.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- eksekusi


def _safe_name(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:80] or "respons"


class Session:
    """Merekam lewat Recorder, menyanitasi, lalu menulis satu JSON per permintaan."""

    def __init__(self, recorder: Recorder, out_dir: Path, base_url: str) -> None:
        self.recorder = recorder
        self.out_dir = out_dir
        self.base_url = base_url
        self.files: list[tuple[str, dict[str, Any]]] = []
        self.manifest: list[dict[str, Any]] = []
        self.stats = SanitizeStats()
        self.pii: list[PiiFinding] = []

    def record(self, label: str, path: str, params: dict[str, Any] | None = None) -> dict:
        try:
            exchange = self.recorder.get(path, params)
        except RecorderStop as stop:
            if stop.exchange is not None:
                self._write(label, stop.exchange)
            raise
        return self._write(label, exchange)

    def _write(self, label: str, exchange: dict[str, Any]) -> dict[str, Any]:
        clean = sanitize(exchange, self.stats)
        name = f"{len(self.files) + 1:03d}_{_safe_name(label)}.json"
        data = (json.dumps(clean, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        (self.out_dir / name).write_bytes(data)
        self.files.append((name, clean))
        self.pii.extend(scan_pii(clean, name))
        self.manifest.append(
            {
                "file": name,
                "url": self.base_url + clean["request"]["path"],
                "params": clean["request"]["params"],
                "status": clean["response"]["status"],
                "recorded_at": clean["recorded_at"],
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
        return clean


def _json_body(exchange: dict[str, Any]) -> dict[str, Any]:
    body = exchange["response"].get("json")
    return body if isinstance(body, dict) else {}


def _is_last_page(body: dict[str, Any], page: int, count: int, per_page: int) -> bool:
    if count == 0 or count < per_page:
        return True
    pagination = body.get("pagination")
    if isinstance(pagination, dict):
        for key in LAST_PAGE_KEYS:
            try:
                if key in pagination and int(pagination[key]) <= page:
                    return True
            except (TypeError, ValueError):
                continue
    return False


def record_catalog(session: Session, catalog: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for page in range(1, catalog["max_pages"] + 1):
        exchange = session.record(
            f"katalog_hal-{page}", catalog["path"], {"page": page, "per_page": catalog["per_page"]}
        )
        body = _json_body(exchange)
        data = body.get("data")
        page_items = [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []
        items.extend(page_items)
        if exchange["response"]["status"] != 200:
            break
        if _is_last_page(body, page, len(page_items), catalog["per_page"]):
            break
    return items


def record_dataset(session: Session, slug: str, bigdata: dict[str, Any], notes: list[str]) -> None:
    detail = session.record(f"detail_{slug}", f"/datasets/{slug}")
    if detail["response"]["status"] != 200:
        notes.append(f"`{slug}`: detail berstatus {detail['response']['status']}")
        return
    record = _json_body(detail).get("data")
    record = record if isinstance(record, dict) else {}
    schema, table = record.get("schema"), record.get("table")
    if not schema or not table:
        notes.append(f"`{slug}`: field schema/table tidak ditemukan di record detail")
        return
    per_page = bigdata["per_page"]
    for page in range(1, bigdata["max_pages_per_dataset"] + 1):
        exchange = session.record(
            f"bigdata_{slug}_hal-{page}",
            f"/bigdata/{schema}/{table}",
            {"page": page, "per_page": per_page},
        )
        body = _json_body(exchange)
        rows = body.get("data") if isinstance(body.get("data"), list) else []
        if exchange["response"]["status"] != 200 or _is_last_page(body, page, len(rows), per_page):
            return
    notes.append(
        f"`{slug}`: /bigdata masih berlanjut setelah {bigdata['max_pages_per_dataset']} hal"
    )


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.casefold())


def _has_phrase(tokens: list[str], phrase: str) -> bool:
    words = _tokens(phrase)
    return any(tokens[i : i + len(words)] == words for i in range(len(tokens) - len(words) + 1))


def find_candidates(
    items: Iterable[dict[str, Any]], cards: Iterable[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    """Cari kandidat slug kartu metrik secara lokal di katalog (tanpa permintaan)."""
    items = list(items)
    result: dict[str, list[dict[str, Any]]] = {}
    for card in cards:
        found = []
        for item in items:
            tokens = _tokens(str(item.get("name") or item.get("title") or ""))
            if not all(_has_phrase(tokens, k) for k in card.get("all_keywords") or []):
                continue
            any_keywords = card.get("any_keywords") or []
            if any_keywords and not any(_has_phrase(tokens, k) for k in any_keywords):
                continue
            found.append(
                {
                    "slug": item.get("slug"),
                    "name": item.get("name") or item.get("title"),
                    "opd": item.get("organisasi_name"),
                }
            )
        result[card["id"]] = found
    return result


@dataclass
class RunResult:
    out_dir: Path
    files: list[tuple[str, dict[str, Any]]]
    sent: int
    stop_reason: str | None
    candidates: dict[str, list[dict[str, Any]]]
    pii: list[PiiFinding]
    stats: SanitizeStats
    incidents: list[str]
    notes: list[str]


def execute(
    targets: dict[str, Any],
    slugs: list[str],
    *,
    base_url: str,
    user_agent: str,
    out_root: Path,
    report_path: Path,
    today: date,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], datetime] = _now,
) -> RunResult:
    validate_user_agent(user_agent)
    out_dir = out_root / today.isoformat()
    if out_dir.exists():
        raise ConfigProblem(f"folder {out_dir} sudah ada; tidak ditimpa")
    own_client = client is None
    if client is None:
        client = httpx.Client(
            base_url=base_url,
            headers={"User-Agent": user_agent},
            timeout=TIMEOUT_S,
            follow_redirects=False,
        )
    out_dir.mkdir(parents=True)
    recorder = Recorder(client, sleep=sleep, clock=clock)
    session = Session(recorder, out_dir, base_url)
    started = clock()
    stop_reason: str | None = None
    candidates: dict[str, list[dict[str, Any]]] = {}
    notes: list[str] = []
    try:
        items = record_catalog(session, targets["catalog"])
        candidates = find_candidates(items, targets.get("metric_cards") or [])
        for endpoint in targets.get("extra_endpoints") or []:
            session.record(f"referensi{endpoint}", endpoint)
        for slug in slugs:
            record_dataset(session, slug, targets["bigdata"], notes)
        for found in candidates.values():
            if len(found) == 1 and found[0]["slug"]:
                record_dataset(session, found[0]["slug"], targets["bigdata"], notes)
        session.record("uji-404", f"/datasets/{targets['invalid_slug']}")
    except RecorderStop as stop:
        stop_reason = f"{type(stop).__name__}: {stop}"
    finally:
        if own_client:
            client.close()

    manifest = {
        "started_at": started.isoformat(timespec="seconds"),
        "base_url": base_url,
        "budget": MAX_REQUESTS,
        "request_count": recorder.sent,
        "file_count": len(session.files),
        "stop_reason": stop_reason,
        "incidents": recorder.incidents,
        "files": session.manifest,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    result = RunResult(
        out_dir=out_dir,
        files=session.files,
        sent=recorder.sent,
        stop_reason=stop_reason,
        candidates=candidates,
        pii=session.pii,
        stats=session.stats,
        incidents=recorder.incidents,
        notes=notes,
    )
    report_path.write_text(build_report(result, today), encoding="utf-8", newline="\n")
    return result


# ---------------------------------------------------------------- laporan


def describe(value: Any, depth: int = 0) -> str:
    """Ringkasan bentuk JSON tanpa nilainya."""
    if value is None:
        return "null"
    if isinstance(value, dict):
        keys = sorted(value)
        shown = ", ".join(keys[:12]) + (", ..." if len(keys) > 12 else "")
        return f"object{{{shown}}}"
    if isinstance(value, list):
        inner = describe(value[0], depth + 1) if value and depth < 2 else "?"
        return f"list[{inner}] (n={len(value)})"
    return type(value).__name__


def _kind(path: str) -> str:
    if path == "/datasets":
        return "katalog"
    if path.startswith("/datasets/"):
        return "detail"
    if path.startswith("/bigdata/"):
        return "bigdata"
    return "referensi"


_DOTTED_DECIMAL = re.compile(r"^-?\d+\.\d+$")
_OFFSET = re.compile(r"([+-]\d{2}:\d{2}|Z)$")
_ID_LIKE = re.compile(r"^(id|_id|.*_id|id_.*)$")


def analyze(files: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    shapes: dict[str, set[str]] = defaultdict(set)
    history_types: Counter[str] = Counter()
    time_formats: Counter[str] = Counter()
    columns_per_table: dict[str, set[str]] = {}
    dotted: Counter[tuple[str, str]] = Counter()
    parsed_text: Counter[tuple[str, str]] = Counter()
    not_found: list[str] = []

    def visit_record(record: dict[str, Any]) -> None:
        for entry in record.get("history_draft") or []:
            if isinstance(entry, dict):
                history_types[str(entry.get("type"))] += 1
                _time_format(entry.get("cdate"), "history_draft[].cdate")
        for key in ("cdate", "mdate"):
            _time_format(record.get(key), key)
        if "metadata" in record:
            shapes["detail.metadata"].add(describe(record["metadata"]))

    def _time_format(value: Any, field_name: str) -> None:
        if isinstance(value, str) and value:
            match = _OFFSET.search(value)
            time_formats[f"{field_name}: {match.group(1) if match else 'tanpa offset'}"] += 1

    for _, exchange in files:
        path = exchange["request"]["path"]
        kind = _kind(path)
        status = exchange["response"]["status"]
        body = _json_body(exchange)
        shapes[f"{kind}.top_level (status {status})"].add(describe(body or None))
        if status == 404:
            not_found.append(f"{path} → {describe(body or None)}")
        if status != 200:
            continue
        data = body.get("data")
        if kind == "katalog":
            if "pagination" in body:
                shapes["katalog.pagination"].add(describe(body["pagination"]))
            for item in data if isinstance(data, list) else []:
                if isinstance(item, dict):
                    visit_record(item)
        elif kind == "detail" and isinstance(data, dict):
            visit_record(data)
        elif kind == "bigdata":
            table = path.removeprefix("/bigdata/")
            for key in ("metadata", "metadata_filter", "pagination"):
                if key in body:
                    shapes[f"bigdata.{key}"].add(describe(body[key]))
            for row in data if isinstance(data, list) else []:
                if not isinstance(row, dict):
                    continue
                columns_per_table.setdefault(table, set()).update(row)
                for column, value in row.items():
                    if not isinstance(value, str):
                        continue
                    text = value.strip()
                    if _DOTTED_DECIMAL.match(text) and parse_id_number(text) is None:
                        dotted[(table, column)] += 1
                    elif parse_id_number(text) is not None and not text.lstrip("-").isdigit():
                        parsed_text[(table, column)] += 1

    common = set.intersection(*columns_per_table.values()) if columns_per_table else set()
    id_like = sorted({c for cols in columns_per_table.values() for c in cols if _ID_LIKE.match(c)})
    return {
        "shapes": {k: sorted(v) for k, v in sorted(shapes.items())},
        "history_types": history_types,
        "time_formats": time_formats,
        "columns_per_table": columns_per_table,
        "common_columns": sorted(common),
        "id_like_columns": id_like,
        "dotted_text": dotted,
        "parsed_text": parsed_text,
        "not_found": not_found,
    }


def _display_path(path: Path) -> str:
    return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)


def build_report(result: RunResult, today: date) -> str:
    info = analyze(result.files)
    out = [
        f"# Laporan Fixture Asli ({today.isoformat()})",
        "",
        "Dibuat otomatis oleh `scripts/record_fixtures.py`. Menjawab TODO Langkah 3 di "
        "`docs/RENCANA_FASE1.md`. Laporan ini tidak memuat nilai data, email, atau telepon.",
        "",
        "## Ringkasan",
        "",
        f"- Folder: `{_display_path(result.out_dir)}`",
        f"- Permintaan terkirim: {result.sent} dari anggaran {MAX_REQUESTS}",
        f"- Berkas respons: {len(result.files)}",
        f"- Berhenti lebih awal: {result.stop_reason or 'tidak'}",
        f"- Insiden 5xx: {'; '.join(result.incidents) or 'tidak ada'}",
        f"- Sanitasi: {result.stats.removed_keys} kunci akun dibuang "
        f"({', '.join(sorted(DROP_KEYS))}); {result.stats.redacted_contacts} nilai "
        "Kontak Produsen diredaksi",
    ]
    out += [f"- Catatan: {note}" for note in result.notes]

    out += ["", "## KB-17: kolom id internal /bigdata", ""]
    if info["columns_per_table"]:
        out.append(f"- Kolom yang ada di semua tabel: {', '.join(info['common_columns']) or '-'}")
        out.append(f"- Kolom berpola id: {', '.join(info['id_like_columns']) or '-'}")
        out.append(
            '- Dugaan KB-17 (`{"id"}`): '
            + ("**terkonfirmasi ada**" if "id" in info["common_columns"] else "**perlu ditinjau**")
        )
        for table, columns in sorted(info["columns_per_table"].items()):
            out.append(f"- `{table}`: {', '.join(sorted(columns))}")
    else:
        out.append("- Tidak ada respons /bigdata yang terekam.")

    out += ["", "## KB-20: bentuk respons", ""]
    for key, shapes in info["shapes"].items():
        out.append(f"- `{key}`: " + " | ".join(f"`{s}`" for s in shapes))
    out += [f"- 404: `{entry}`" for entry in info["not_found"]]

    out += ["", "## Nilai `type` di history_draft", ""]
    out += [f"- `{k}`: {v}" for k, v in info["history_types"].most_common()] or ["- tidak ada"]

    out += ["", "## Format waktu (NB-01)", ""]
    out += [f"- {k}: {v}" for k, v in sorted(info["time_formats"].items())] or ["- tidak ada"]

    out += ["", "## Angka bertipe teks (risiko KB-16)", ""]
    out.append("Desimal bertitik bertipe teks (menjadi None menurut KB-16):")
    out += [f"- `{t}`.`{c}`: {n} sel" for (t, c), n in sorted(info["dotted_text"].items())] or [
        "- tidak ditemukan"
    ]
    out.append("")
    out.append("Teks angka berformat Indonesia yang berhasil diurai NB-07:")
    out += [f"- `{t}`.`{c}`: {n} sel" for (t, c), n in sorted(info["parsed_text"].items())] or [
        "- tidak ditemukan"
    ]

    out += ["", "## Kandidat slug kartu metrik", ""]
    for card_id, found in result.candidates.items():
        verdict = "direkam otomatis" if len(found) == 1 else "pilih manual"
        out.append(f"### {card_id} ({len(found)} kandidat, {verdict})")
        out += [f"- `{c['slug']}`: {c['name']} ({c['opd'] or '-'})" for c in found] or [
            "- tidak ada"
        ]
        out.append("")

    out += ["## Temuan email/telepon setelah sanitasi", ""]
    out += [f"- {p.kind}: `{p.file}` di `{p.json_path}`" for p in result.pii] or ["- tidak ada"]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--execute", action="store_true", help="kirim permintaan sungguhan")
    parser.add_argument("--targets", type=Path, default=DEFAULT_TARGETS)
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)

    settings = Settings()
    targets = load_targets(args.targets)
    slugs = golden_slugs(ROOT / targets["golden_set"]["path"])
    expected = targets["golden_set"]["expected_count"]
    if len(slugs) != expected:
        print(f"PERINGATAN: golden set memberi {len(slugs)} slug, bukan {expected}.")
    plan = build_plan(targets, slugs)
    today = datetime.now(WIB).date()

    if not args.execute:
        print(
            format_dry_run(
                plan,
                targets,
                base_url=settings.source_base_url,
                user_agent=settings.user_agent,
                out_dir=args.out_root / today.isoformat(),
            )
        )
        return 0

    try:
        result = execute(
            targets,
            slugs,
            base_url=settings.source_base_url,
            user_agent=settings.user_agent,
            out_root=args.out_root,
            report_path=args.report,
            today=today,
        )
    except ConfigProblem as exc:
        print(f"DITOLAK: {exc}")
        return 2
    print(f"Selesai: {result.sent} permintaan, {len(result.files)} berkas di {result.out_dir}")
    print(f"Laporan: {args.report}")
    if result.stop_reason:
        print(f"BERHENTI LEBIH AWAL: {result.stop_reason}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
