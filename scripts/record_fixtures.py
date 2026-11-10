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

from mcp_bogor.adapters.sanitize import (
    ACCOUNT_KEYS,
    CONTACT_FIELDS,
    PiiFinding,
    SanitizeStats,
    sanitize,
    scan_pii,
)
from mcp_bogor.config import USER_AGENT_PLACEHOLDER, WIB, ConfigError, Settings, require_user_agent
from mcp_bogor.services.normalize import parse_id_number

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGETS = ROOT / "scripts" / "fixture_targets.yaml"
DEFAULT_OUT_ROOT = ROOT / "tests" / "fixtures" / "recorded"
DEFAULT_REPORT = ROOT / "docs" / "FIXTURE_REPORT.md"

MAX_REQUESTS = 60
REQUEST_DELAY_S = 0.3
TIMEOUT_S = 20.0
RETRY_5XX_DELAY_S = 5.0
FORBIDDEN_FRAGMENT = "/view"
ALLOWED_PATHS = (
    re.compile(r"^/datasets$"),
    re.compile(r"^/datasets/[^/]+$"),
    re.compile(r"^/bigdata/[^/]+/[^/]+$"),
    re.compile(r"^/organisasi$"),
    re.compile(r"^/topik$"),
)

KEPT_HEADERS = ("content-type", "retry-after")
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
    """Cek yang sama dengan adaptor (config.require_user_agent)."""
    try:
        require_user_agent(user_agent)
    except ConfigError as exc:
        raise ConfigProblem(str(exc)) from exc


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
# Logika sanitasi ada di mcp_bogor.adapters.sanitize (satu sumber dengan adaptor).


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
    has_catalog = any(p.group == "katalog" for p in plan)
    extra_catalog = catalog["max_pages"] - catalog["expected_pages"] if has_catalog else 0
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
    existing_required: bool = False,
) -> str:
    if existing_required:
        folder_note = (
            "   [ada; berkas lama tidak ditimpa]"
            if out_dir.is_dir()
            else "   [BELUM ADA: --execute akan menolak]"
        )
    else:
        folder_note = "   [SUDAH ADA: --execute akan menolak]" if out_dir.exists() else ""
    lines = [
        "DRY-RUN: tidak ada permintaan yang dikirim. Tambahkan --execute untuk merekam.",
        f"Base URL   : {base_url}",
        f"User-Agent : {user_agent}"
        + (
            "   [DITOLAK saat --execute: masih placeholder]"
            if USER_AGENT_PLACEHOLDER in user_agent
            else ""
        ),
        f"Keluaran   : {out_dir}" + folder_note,
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


def _dump(obj: Any) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


_NUMBERED = re.compile(r"^(\d{3})_.+\.json$")


def numbered_files(folder: Path) -> list[Path]:
    """Berkas rekaman bernomor (NNN_*.json) terurut; manifest dan turunan tidak termasuk."""
    return sorted(p for p in folder.glob("*.json") if _NUMBERED.match(p.name))


def last_index(folder: Path) -> int:
    files = numbered_files(folder)
    return int(_NUMBERED.match(files[-1].name).group(1)) if files else 0


class Session:
    """Merekam lewat Recorder, menyanitasi, lalu menulis satu JSON per permintaan."""

    def __init__(
        self, recorder: Recorder, out_dir: Path, base_url: str, start_index: int = 0
    ) -> None:
        self.recorder = recorder
        self.out_dir = out_dir
        self.base_url = base_url
        self.start_index = start_index
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
        name = f"{self.start_index + len(self.files) + 1:03d}_{_safe_name(label)}.json"
        target = self.out_dir / name
        if target.exists():
            raise ConfigProblem(f"berkas {name} sudah ada; tidak ditimpa")
        data = _dump(clean)
        target.write_bytes(data)
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


# ---------------------------------------------------------------- mode --only


def latest_date_folder(out_root: Path) -> Path | None:
    folders = sorted(
        p for p in out_root.glob("*") if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name)
    )
    return folders[-1] if folders else None


def plan_only(targets: dict[str, Any], slug: str) -> list[PlannedRequest]:
    bigdata = targets["bigdata"]
    plan = [
        PlannedRequest("only", f"/datasets/{slug}", {}),
        PlannedRequest(
            "only",
            "/bigdata/{schema}/{table}",
            {"page": 1, "per_page": bigdata["per_page"]},
            f"schema/table dari detail; maks {bigdata['max_pages_per_dataset']} halaman",
        ),
    ]
    check_path(plan[0].path)
    return plan


def only_manifest_path(out_dir: Path, slug: str) -> Path:
    return out_dir / f"manifest_only_{_safe_name(slug)}.json"


def execute_only(
    targets: dict[str, Any],
    slug: str,
    *,
    base_url: str,
    user_agent: str,
    out_dir: Path,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], datetime] = _now,
) -> RunResult:
    """Rekam detail + /bigdata satu slug ke folder tanggal yang sudah ada.

    Semua aturan akses tetap berlaku. Berkas baru dinomori setelah berkas terakhir;
    tidak ada berkas yang ditimpa, termasuk manifest.json (manifest terpisah per slug).
    """
    validate_user_agent(user_agent)
    check_path(f"/datasets/{slug}")
    if not out_dir.is_dir():
        raise ConfigProblem(f"folder {out_dir} belum ada; --only menulis ke folder rekaman lama")
    manifest_path = only_manifest_path(out_dir, slug)
    if manifest_path.exists():
        raise ConfigProblem(f"{manifest_path.name} sudah ada; tidak ditimpa")
    own_client = client is None
    if client is None:
        client = httpx.Client(
            base_url=base_url,
            headers={"User-Agent": user_agent},
            timeout=TIMEOUT_S,
            follow_redirects=False,
        )
    recorder = Recorder(client, sleep=sleep, clock=clock)
    session = Session(recorder, out_dir, base_url, start_index=last_index(out_dir))
    started = clock()
    stop_reason: str | None = None
    notes: list[str] = []
    try:
        record_dataset(session, slug, targets["bigdata"], notes)
    except RecorderStop as stop:
        stop_reason = f"{type(stop).__name__}: {stop}"
    finally:
        if own_client:
            client.close()
    manifest = {
        "started_at": started.isoformat(timespec="seconds"),
        "mode": "only",
        "slug": slug,
        "base_url": base_url,
        "budget": MAX_REQUESTS,
        "request_count": recorder.sent,
        "file_count": len(session.files),
        "stop_reason": stop_reason,
        "incidents": recorder.incidents,
        "notes": notes,
        "files": session.manifest,
    }
    manifest_path.write_bytes(_dump(manifest))
    return RunResult(
        out_dir=out_dir,
        files=session.files,
        sent=recorder.sent,
        stop_reason=stop_reason,
        candidates={},
        pii=session.pii,
        stats=session.stats,
        incidents=recorder.incidents,
        notes=notes,
    )


# ---------------------------------------------------------------- sanitasi ulang (lokal)


def resanitize(folder: Path, report_path: Path, targets: dict[str, Any]) -> RunResult:
    """Terapkan sanitasi terbaru ke rekaman yang sudah ada, tanpa permintaan jaringan.

    Berkas bernomor yang berubah ditulis ulang; sha256 di semua manifest*.json
    diperbarui; laporan dibuat ulang dari berkas yang ada di disk.
    """
    if not folder.is_dir():
        raise ConfigProblem(f"folder {folder} tidak ada")
    stats = SanitizeStats()
    files: list[tuple[str, dict[str, Any]]] = []
    pii: list[PiiFinding] = []
    new_hashes: dict[str, str] = {}
    for path in numbered_files(folder):
        original = path.read_bytes()
        clean = sanitize(json.loads(original), stats)
        data = _dump(clean)
        if data != original:
            path.write_bytes(data)
        new_hashes[path.name] = hashlib.sha256(data).hexdigest()
        files.append((path.name, clean))
        pii.extend(scan_pii(clean, path.name))

    main_manifest: dict[str, Any] = {}
    for manifest_path in sorted(folder.glob("manifest*.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in manifest.get("files", []):
            if entry["file"] in new_hashes:
                entry["sha256"] = new_hashes[entry["file"]]
        manifest_path.write_bytes(_dump(manifest))
        if manifest_path.name == "manifest.json":
            main_manifest = manifest

    catalog_items = [
        item
        for _, exchange in files
        if exchange["request"]["path"] == "/datasets"
        for item in (_json_body(exchange).get("data") or [])
        if isinstance(item, dict)
    ]
    result = RunResult(
        out_dir=folder,
        files=files,
        sent=int(main_manifest.get("request_count", len(files))),
        stop_reason=main_manifest.get("stop_reason"),
        candidates=find_candidates(catalog_items, targets.get("metric_cards") or []),
        pii=pii,
        stats=stats,
        incidents=list(main_manifest.get("incidents", [])),
        notes=[],
    )
    report_path.write_text(
        build_report(result, date.fromisoformat(folder.name)), encoding="utf-8", newline="\n"
    )
    return result


# ---------------------------------------------------------------- katalog ramping (lokal)

CATALOG_SLIM_NAME = "catalog_slim.json"
# Dok 2 bagian 2: field katalog yang dipakai MCP. Kontak OPD, hitungan view/download/share,
# path gambar, tags, dan field akun tidak ikut (KK-J).
CATALOG_SLIM_FIELDS = (
    "id",
    "slug",
    "name",
    "deskripsi",
    "organisasi_id",
    "organisasi_name",
    "organisasi_slug",
    "topik_id",
    "topik_name",
    "topik_slug",
    "kategori",
    "klasifikasi",
    "validate",
    "priority_level",
    "data_score_status",
    "kode_wilayah",
    "schema",
    "table",
    "mdate",
)
CATALOG_SLIM_METADATA_KEYS = (
    "Satuan Dataset",
    "Frekuensi Dataset",
    "Dimensi Dataset Awal",
    "Dimensi Dataset Akhir",
    "Tingkat Penyajian Dataset",
    "Cakupan Dataset",
)
CATALOG_SLIM_HISTORY_KEYS = ("type", "cdate", "notes")


def slim_record(record: dict[str, Any]) -> dict[str, Any]:
    out = {key: record.get(key) for key in CATALOG_SLIM_FIELDS}
    out["metadata"] = [
        {"key": m.get("key"), "value": m.get("value")}
        for m in record.get("metadata") or []
        if isinstance(m, dict) and m.get("key") in CATALOG_SLIM_METADATA_KEYS
    ]
    out["history_draft"] = [
        {key: entry.get(key) for key in CATALOG_SLIM_HISTORY_KEYS}
        for entry in record.get("history_draft") or []
        if isinstance(entry, dict)
    ]
    return out


def build_catalog_slim(folder: Path) -> dict[str, Any]:
    """Gabungkan semua halaman katalog di folder menjadi satu berkas ramping (lokal)."""
    pages: list[tuple[int, str, dict[str, Any]]] = []
    for path in numbered_files(folder):
        exchange = json.loads(path.read_text(encoding="utf-8"))
        if exchange["request"]["path"] == "/datasets":
            pages.append((int(exchange["request"]["params"]["page"]), path.name, exchange))
    if not pages:
        raise ConfigProblem(f"tidak ada halaman katalog di {folder}")
    pages.sort()
    numbers = [page for page, _, _ in pages]
    total_page = _json_body(pages[0][2]).get("pagination", {}).get("total_page")
    total_data = _json_body(pages[0][2]).get("pagination", {}).get("total_data")
    if numbers != list(range(1, len(numbers) + 1)) or (total_page and len(numbers) != total_page):
        raise ConfigProblem(f"halaman katalog tidak lengkap: {numbers}, total_page={total_page}")
    records = [
        slim_record(item)
        for _, _, exchange in pages
        for item in _json_body(exchange).get("data") or []
        if isinstance(item, dict)
    ]
    slugs = [r["slug"] for r in records]
    if len(set(slugs)) != len(slugs):
        raise ConfigProblem("slug ganda di katalog")
    if total_data is not None and len(records) != total_data:
        raise ConfigProblem(f"jumlah record {len(records)} ≠ pagination.total_data {total_data}")
    return {
        "_keterangan": (
            "Katalog ramping turunan halaman /datasets (Dok 5: halaman 1 dan terakhir di-commit "
            "mentah). Hanya field yang dipakai MCP (Dok 2 bagian 2); metadata hanya key/value "
            "yang dipakai; history_draft hanya type/cdate/notes. Dibuat oleh "
            "scripts/record_fixtures.py --slim-catalog tanpa permintaan jaringan."
        ),
        "source_folder": _display_path(folder),
        "source_files": [name for _, name, _ in pages],
        "recorded_at": pages[0][2].get("recorded_at"),
        "pagination_total_data": total_data,
        "record_count": len(records),
        "fields": [*CATALOG_SLIM_FIELDS, "metadata", "history_draft"],
        "metadata_keys": list(CATALOG_SLIM_METADATA_KEYS),
        "records": records,
    }


def dump_catalog_slim(slim: dict[str, Any]) -> bytes:
    """JSON sah dengan header berindentasi dan satu record per baris (ringkas, enak di-diff)."""
    header = {k: v for k, v in slim.items() if k != "records"}
    head = json.dumps(header, ensure_ascii=False, indent=2)[:-2]  # buang "\n}" penutup
    records = ",\n".join(
        "    " + json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in slim["records"]
    )
    return f'{head},\n  "records": [\n{records}\n  ]\n}}\n'.encode()


def write_catalog_slim(folder: Path) -> Path:
    target = folder / CATALOG_SLIM_NAME
    target.write_bytes(dump_catalog_slim(build_catalog_slim(folder)))
    return target


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
        f"({', '.join(sorted(ACCOUNT_KEYS))}); {result.stats.redacted_contacts} nilai kontak "
        f"diredaksi (Kontak Produsen, {', '.join(sorted(CONTACT_FIELDS))})",
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
    out.append("Dikelompokkan per berkas dan pola path (indeks list diringkas menjadi `[]`).")
    out.append("")
    out += [
        f"- {kind}: `{file}` di `{path}` ({count} kali)"
        for (kind, file, path), count in sorted(group_pii(result.pii).items())
    ] or ["- tidak ada"]
    return "\n".join(out) + "\n"


def group_pii(findings: Iterable[PiiFinding]) -> Counter[tuple[str, str, str]]:
    return Counter((p.kind, p.file, re.sub(r"\[\d+\]", "[]", p.json_path)) for p in findings)


# ---------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--execute", action="store_true", help="kirim permintaan sungguhan")
    parser.add_argument("--targets", type=Path, default=DEFAULT_TARGETS)
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--date", help="folder tanggal (YYYY-MM-DD); bawaan: --only/lokal = folder terbaru"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--only", metavar="SLUG", help="rekam detail + /bigdata satu slug saja")
    mode.add_argument(
        "--resanitize", action="store_true", help="sanitasi ulang rekaman (lokal, tanpa jaringan)"
    )
    mode.add_argument("--slim-catalog", action="store_true", help="buat catalog_slim.json (lokal)")
    args = parser.parse_args(argv)

    settings = Settings()
    targets = load_targets(args.targets)

    if args.only or args.resanitize or args.slim_catalog:
        folder = args.out_root / args.date if args.date else latest_date_folder(args.out_root)
        if folder is None:
            print(f"DITOLAK: belum ada folder rekaman di {args.out_root}")
            return 2
        try:
            if args.resanitize:
                result = resanitize(folder, args.report, targets)
                print(
                    f"Sanitasi ulang {len(result.files)} berkas di {folder}: "
                    f"{result.stats.removed_keys} kunci akun dibuang, "
                    f"{result.stats.redacted_contacts} nilai kontak diredaksi, "
                    f"{len(result.pii)} temuan email/telepon tersisa. Laporan: {args.report}"
                )
                return 0
            if args.slim_catalog:
                target = write_catalog_slim(folder)
                count = json.loads(target.read_text(encoding="utf-8"))["record_count"]
                print(f"{target} ditulis: {count} record, {target.stat().st_size:,} byte")
                return 0
            return _run_only(args, targets, settings, folder)
        except ConfigProblem as exc:
            print(f"DITOLAK: {exc}")
            return 2
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


def _run_only(
    args: argparse.Namespace, targets: dict[str, Any], settings: Settings, folder: Path
) -> int:
    plan = plan_only(targets, args.only)
    if not args.execute:
        next_index = last_index(folder) + 1 if folder.is_dir() else 1
        manifest = only_manifest_path(folder, args.only)
        print(
            format_dry_run(
                plan,
                targets,
                base_url=settings.source_base_url,
                user_agent=settings.user_agent,
                out_dir=folder,
                existing_required=True,
            )
        )
        print(
            f"Berkas baru mulai dari nomor {next_index:03d}; manifest terpisah: {manifest.name}"
            + ("   [SUDAH ADA: --execute akan menolak]" if manifest.exists() else "")
        )
        return 0
    result = execute_only(
        targets,
        args.only,
        base_url=settings.source_base_url,
        user_agent=settings.user_agent,
        out_dir=folder,
    )
    print(f"Selesai: {result.sent} permintaan, {len(result.files)} berkas baru di {folder}")
    for name, _ in result.files:
        print(f"  {name}")
    print(f"Temuan email/telepon: {len(result.pii)}")
    for note in result.notes:
        print(f"Catatan: {note}")
    if result.stop_reason:
        print(f"BERHENTI LEBIH AWAL: {result.stop_reason}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
