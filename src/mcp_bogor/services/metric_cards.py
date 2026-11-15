"""Kartu metrik prioritas (Dok 4 bagian 8, KB-49): skema, muat, dan pemeriksaan.

Skema (`MetricCard`, extra=forbid): field Dok 4 bagian 8 (id, nama, definisi, slug,
kolom_nilai, kolom_dimensi, satuan, opd, walidata, periode_tersedia, catatan_mutu, sinonim,
dataset_berbeda_definisi), ditambah walidata_catatan, status, kasus_uji, reviewed_by,
reviewed_at (KB-49). Validasi antarkartu: nama berkas = id, id dan slug unik, sinonim unik di
seluruh kartu setelah sanitasi kueri yang sama dengan pencarian (`catalog.query_tokens`).

Pemeriksaan (`check_cards`) memakai katalog lokal dan kamus dari cache lokal saja; tidak
pernah memanggil portal:
- katalog: slug dan dataset_berbeda_definisi ada; opd sama; periode_tersedia dibandingkan
  dengan tahun_awal..tahun_akhir (beda → peringatan, karena portal bisa bertambah data);
- kamus: kolom_nilai berperan measure, kolom_dimensi berperan dimension, satuan cocok
  dengan unit kolom nilai atau "Satuan Dataset", status cocok dua arah dengan
  dictionary_validated (aktif ↔ tervalidasi; kamus_belum_tervalidasi ↔ belum);
- kamus belum ada di cache, atau dibuat dengan override lain → "belum dicek".

`catatan_mutu` boleh memuat angka (mis. contoh nilai janggal) karena tidak pernah dikirim ke
model (KT-03); hanya dibaca manusia dan pemeriksa.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from mcp_bogor.models.params import SLUG_MAX_CHARS, SLUG_PATTERN
from mcp_bogor.services.catalog import CatalogDataset, get_dataset, query_tokens
from mcp_bogor.services.dictionary import DictionaryResult, OverrideError, parse_override
from mcp_bogor.services.info import InfoBundle
from mcp_bogor.storage import db
from mcp_bogor.storage.cache import DictionaryCache
from mcp_bogor.storage.metric_cards import CardFile, MetricCardFileError, read_card_files
from mcp_bogor.storage.overrides import OverrideFileError, read_override

CARD_ID_PATTERN = r"^KM-\d{2}$"
CASE_ID_PATTERN = r"^[A-Z]-\d{2}$"  # kode kasus golden set, mis. N-03

Status = Literal["perlu_konfirmasi", "kamus_belum_tervalidasi", "aktif"]
Level = Literal["error", "unchecked", "warning"]

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Slug = Annotated[str, StringConstraints(pattern=SLUG_PATTERN, max_length=SLUG_MAX_CHARS)]
CaseId = Annotated[str, StringConstraints(pattern=CASE_ID_PATTERN)]

# Satuan yang dianggap sama setelah huruf kecil dan spasi dirapikan; ubah di sini.
UNIT_ALIASES = {"%": "persen", "rp": "rupiah", "rp.": "rupiah"}


class MetricCardError(ValueError):
    """Satu atau lebih kartu tidak sah (skema atau aturan antarkartu)."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


class Periode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    awal: int = Field(ge=1900, le=2100)
    akhir: int = Field(ge=1900, le=2100)

    @model_validator(mode="after")
    def _ordered(self) -> Periode:
        if self.awal > self.akhir:
            raise ValueError("periode_tersedia.awal lebih besar dari akhir")
        return self


def _duplicates(values: Sequence[Any]) -> list[Any]:
    seen: set[Any] = set()
    return [v for v in values if v in seen or seen.add(v)]  # type: ignore[func-returns-value]


def synonym_key(text: str) -> tuple[str, ...]:
    """Bentuk sinonim/kueri yang dibandingkan: token kueri pencarian (sanitasi yang sama)."""
    return tuple(query_tokens(text))


class MetricCard(BaseModel):
    """Isi metric_cards/KM-xx.yaml."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: Annotated[str, StringConstraints(pattern=CARD_ID_PATTERN)]
    nama: Text
    definisi: Text
    slug: Slug
    kolom_nilai: Text
    kolom_dimensi: list[Text] = Field(min_length=1)
    satuan: Text
    opd: Text
    walidata: Text
    walidata_catatan: Text | None = None
    periode_tersedia: Periode
    catatan_mutu: list[Text] = Field(default_factory=list)
    sinonim: list[Text] = Field(min_length=1)
    dataset_berbeda_definisi: list[Slug] = Field(default_factory=list)
    status: Status
    kasus_uji: list[CaseId] = Field(default_factory=list)
    reviewed_by: Text | None = None
    reviewed_at: date | None = None

    @model_validator(mode="after")
    def _check(self) -> MetricCard:
        if (self.reviewed_by is None) != (self.reviewed_at is None):
            raise ValueError("reviewed_by dan reviewed_at wajib diisi berpasangan")
        if self.kolom_nilai in self.kolom_dimensi:
            raise ValueError("kolom_nilai tidak boleh ada di kolom_dimensi")
        if self.slug in self.dataset_berbeda_definisi:
            raise ValueError("dataset_berbeda_definisi tidak boleh memuat slug kartu sendiri")
        for name, values in (
            ("kolom_dimensi", self.kolom_dimensi),
            ("dataset_berbeda_definisi", self.dataset_berbeda_definisi),
            ("kasus_uji", self.kasus_uji),
        ):
            if dup := _duplicates(values):
                raise ValueError(f"{name} memuat duplikat: {dup}")
        keys = []
        for text in self.sinonim:
            key = synonym_key(text)
            if not key:
                raise ValueError(f"sinonim {text!r} kosong setelah sanitasi kueri (stopword?)")
            keys.append(key)
        if dup := _duplicates(keys):
            raise ValueError(f"sinonim sama setelah sanitasi: {[' '.join(k) for k in dup]}")
        return self

    def synonym_keys(self) -> list[tuple[str, ...]]:
        return [synonym_key(text) for text in self.sinonim]


def _errors(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in err['loc']) or 'kartu'}: {err['msg']}" for err in exc.errors()
    )


def parse_cards(files: Sequence[CardFile]) -> list[MetricCard]:
    """Validasi semua kartu sekaligus; semua masalah dikumpulkan ke satu MetricCardError."""
    problems: list[str] = []
    cards: list[MetricCard] = []
    for file in files:
        if not isinstance(file.data, dict):
            problems.append(f"kartu {file.name}: isi berkas harus objek")
            continue
        try:
            card = MetricCard.model_validate(file.data)
        except ValidationError as exc:
            problems.append(f"kartu {file.name}: {_errors(exc)}")
            continue
        if card.id != file.name:
            problems.append(f"kartu {file.name}: field id berisi {card.id!r}")
            continue
        cards.append(card)
    slugs: dict[str, str] = {}
    synonyms: dict[tuple[str, ...], str] = {}
    for card in cards:
        if card.slug in slugs:
            problems.append(f"kartu {card.id}: slug sama dengan {slugs[card.slug]}")
        slugs.setdefault(card.slug, card.id)
        for key in card.synonym_keys():
            owner = synonyms.setdefault(key, card.id)
            if owner != card.id:
                problems.append(f"kartu {card.id}: sinonim {' '.join(key)!r} sudah dipakai {owner}")
    if problems:
        raise MetricCardError(problems)
    return sorted(cards, key=lambda c: c.id)


def load_cards(directory: Path) -> list[MetricCard]:
    """Baca dan validasi direktori kartu. Galat → MetricCardFileError / MetricCardError."""
    return parse_cards(read_card_files(directory))


def synonym_index(cards: Sequence[MetricCard]) -> dict[tuple[str, ...], MetricCard]:
    """Sinonim (bentuk token) → kartu. Keunikan sudah dijamin parse_cards."""
    return {key: card for card in cards for key in card.synonym_keys()}


# ---------------------------------------------------------------- pemeriksaan


@dataclass(frozen=True)
class Finding:
    level: Level
    code: str
    message: str


@dataclass(frozen=True)
class CachedDictionary:
    """Kamus dari cache lokal (bundel get_dataset_info)."""

    dictionary: DictionaryResult
    periods_available: list[int] | None
    data_updated_at: datetime | None
    override_matches: bool  # dibuat dengan override yang sama dengan berkas sekarang


DictionaryLookup = Callable[[CatalogDataset], CachedDictionary | None]


def cache_lookup(cache: DictionaryCache, overrides_dir: Path) -> DictionaryLookup:
    """Kamus tersimpan apa pun versinya (tanpa portal). Override rusak → OverrideError."""

    def lookup(dataset: CatalogDataset) -> CachedDictionary | None:
        record = cache.get_any(dataset.slug)
        if record is None:
            return None
        try:
            bundle = InfoBundle.from_dict(record.payload)
        except ValueError:
            return None
        data = read_override(overrides_dir, dataset.slug)
        digest = None if data is None else parse_override(data, dataset.slug).digest()
        return CachedDictionary(
            dictionary=bundle.dictionary,
            periods_available=bundle.periods_available,
            data_updated_at=record.data_updated_at,
            override_matches=record.override_digest == digest,
        )

    return lookup


@dataclass
class CardCheck:
    card: MetricCard
    findings: list[Finding] = field(default_factory=list)

    def add(self, level: Level, code: str, message: str) -> None:
        self.findings.append(Finding(level, code, message))

    @property
    def outcome(self) -> Level | Literal["ok"]:
        for level in ("error", "unchecked", "warning"):
            if any(f.level == level for f in self.findings):
                return level  # type: ignore[return-value]
        return "ok"


@dataclass
class CheckReport:
    cards: list[CardCheck] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # kartu tidak sah (skema)

    @property
    def exit_code(self) -> int:
        """0 lulus (peringatan boleh), 1 ada galat, 3 ada yang belum dicek."""
        if self.problems or any(c.outcome == "error" for c in self.cards):
            return 1
        if any(c.outcome == "unchecked" for c in self.cards):
            return 3
        return 0

    def render(self) -> str:
        label = {"ok": "OK", "warning": "OK*", "unchecked": "BELUM DICEK", "error": "GALAT"}
        lines: list[str] = []
        for problem in self.problems:
            lines.append(f"GALAT  {problem}")
        for check in self.cards:
            card = check.card
            lines.append(f"{label[check.outcome]:<11} {card.id} {card.nama} [{card.status}]")
            for f in check.findings:
                lines.append(f"    - {f.level}: {f.message} ({f.code})")
        counts = {k: sum(c.outcome == k for c in self.cards) for k in label}
        lines.append(
            f"Ringkasan: {len(self.cards)} kartu; ok {counts['ok']}, ok+peringatan "
            f"{counts['warning']}, belum dicek {counts['unchecked']}, galat {counts['error']}"
            f"{f', kartu tidak sah {len(self.problems)}' if self.problems else ''}"
        )
        return "\n".join(lines)


def normalize_unit(value: str | None) -> str | None:
    if value is None:
        return None
    text = " ".join(value.split()).casefold()
    return UNIT_ALIASES.get(text, text) or None


def _check_catalog(check: CardCheck, dataset: CatalogDataset, conn: sqlite3.Connection) -> None:
    card = check.card
    for slug in card.dataset_berbeda_definisi:
        if get_dataset(conn, slug) is None:
            message = f"dataset_berbeda_definisi {slug} tidak ada di katalog"
            check.add("error", "RELATED_NOT_IN_CATALOG", message)
    if " ".join((dataset.opd or "").split()).casefold() != " ".join(card.opd.split()).casefold():
        check.add("error", "OPD_MISMATCH", f"opd kartu {card.opd!r}, katalog {dataset.opd!r}")
    catalog_period = (dataset.tahun_awal, dataset.tahun_akhir)
    if catalog_period != (card.periode_tersedia.awal, card.periode_tersedia.akhir):
        check.add(
            "warning",
            "PERIOD_DIFFERS",
            f"periode_tersedia {card.periode_tersedia.awal}-{card.periode_tersedia.akhir}, "
            f"katalog {dataset.tahun_awal}-{dataset.tahun_akhir}",
        )


def _check_dictionary(check: CardCheck, dataset: CatalogDataset, cached: CachedDictionary) -> None:
    card = check.card
    if cached.data_updated_at != dataset.data_updated_at:
        check.add(
            "warning",
            "DICTIONARY_OLDER",
            "kamus di cache dibuat dari versi data lain "
            f"({cached.data_updated_at}; katalog {dataset.data_updated_at})",
        )
    columns = {c.name: c for c in cached.dictionary.columns}
    measure = columns.get(card.kolom_nilai)
    if measure is None:
        check.add("error", "MEASURE_MISSING", f"kolom_nilai {card.kolom_nilai} tidak ada di data")
    elif measure.role != "measure":
        message = f"kolom_nilai {card.kolom_nilai} berperan {measure.role}"
        check.add("error", "MEASURE_ROLE", message)
    for name in card.kolom_dimensi:
        column = columns.get(name)
        if column is None:
            check.add("error", "DIMENSION_MISSING", f"kolom_dimensi {name} tidak ada di data")
        elif column.role != "dimension":
            check.add("error", "DIMENSION_ROLE", f"kolom_dimensi {name} berperan {column.role}")
    units = {normalize_unit(dataset.satuan), normalize_unit(measure.unit if measure else None)}
    units.discard(None)
    if normalize_unit(card.satuan) not in units:
        check.add(
            "error",
            "UNIT_MISMATCH",
            f"satuan kartu {card.satuan!r}, Satuan Dataset {dataset.satuan!r}, unit kolom "
            f"{measure.unit if measure else None!r}",
        )
    if cached.periods_available and (
        min(cached.periods_available),
        max(cached.periods_available),
    ) != (card.periode_tersedia.awal, card.periode_tersedia.akhir):
        check.add(
            "warning",
            "PERIOD_DIFFERS_DATA",
            f"tahun di data {min(cached.periods_available)}-{max(cached.periods_available)}, "
            f"kartu {card.periode_tersedia.awal}-{card.periode_tersedia.akhir}",
        )
    validated = cached.dictionary.dictionary_validated
    if card.status == "aktif" and not validated:
        reasons = ", ".join(r.code for r in cached.dictionary.reasons)
        message = f"status aktif tetapi kamus belum tervalidasi ({reasons})"
        check.add("error", "STATUS_NOT_VALIDATED", message)
    if card.status == "kamus_belum_tervalidasi" and validated:
        check.add(
            "error",
            "STATUS_OUTDATED",
            "kamus sudah tervalidasi; perbarui status kartu (mis. menjadi aktif)",
        )


def check_card(card: MetricCard, conn: sqlite3.Connection, lookup: DictionaryLookup) -> CardCheck:
    check = CardCheck(card)
    if card.status == "perlu_konfirmasi":
        check.add("warning", "NEEDS_CONFIRMATION", "status perlu_konfirmasi; tidak dipakai search")
    if card.reviewed_by is None:
        check.add("warning", "NOT_REVIEWED", "belum diperiksa (reviewed_by kosong)")
    dataset = get_dataset(conn, card.slug)
    if dataset is None:
        check.add("error", "NOT_IN_CATALOG", f"slug {card.slug} tidak ada di katalog lokal")
        return check
    _check_catalog(check, dataset, conn)
    try:
        cached = lookup(dataset)
    except (OverrideError, OverrideFileError) as exc:
        check.add("error", "OVERRIDE_INVALID", f"berkas override rusak: {exc}")
        return check
    if cached is None:
        message = "kamus belum ada di cache lokal; kolom, satuan kolom, dan status belum dicek"
        check.add("unchecked", "DICTIONARY_NOT_CACHED", message)
        if normalize_unit(card.satuan) != normalize_unit(dataset.satuan):
            check.add(
                "unchecked",
                "UNIT_NOT_CATALOG",
                f"satuan kartu {card.satuan!r} beda dengan Satuan Dataset {dataset.satuan!r}",
            )
        return check
    if not cached.override_matches:
        check.add(
            "unchecked",
            "DICTIONARY_OVERRIDE_CHANGED",
            "kamus di cache dibuat dengan override lain; panggil get_dataset_info untuk "
            "membangunnya ulang",
        )
        return check
    _check_dictionary(check, dataset, cached)
    return check


def _symmetry(checks: Sequence[CardCheck]) -> None:
    """R-10: pasangan dataset_berbeda_definisi antarkartu sebaiknya dua arah."""
    by_slug = {c.card.slug: c.card for c in checks}
    for check in checks:
        for slug in check.card.dataset_berbeda_definisi:
            other = by_slug.get(slug)
            if other is not None and check.card.slug not in other.dataset_berbeda_definisi:
                check.add(
                    "warning",
                    "RELATED_NOT_MUTUAL",
                    f"{other.id} tidak menyebut balik {check.card.id} di dataset_berbeda_definisi",
                )


def check_cards(
    cards: Sequence[MetricCard], conn: sqlite3.Connection, lookup: DictionaryLookup
) -> CheckReport:
    checks = [check_card(card, conn, lookup) for card in cards]
    _symmetry(checks)
    return CheckReport(cards=checks)


def run_check(cards_dir: Path, db_path: Path, cache_dir: Path, overrides_dir: Path) -> CheckReport:
    """Perintah --check: kartu + katalog lokal + cache kamus lokal. Tanpa portal.

    Katalog belum ada → db.CatalogNotReady (pemanggil: kode keluar 2).
    """
    try:
        cards = load_cards(cards_dir)
    except MetricCardFileError as exc:
        return CheckReport(problems=[str(exc)])
    except MetricCardError as exc:
        return CheckReport(problems=exc.problems)
    conn = db.connect_read(db_path)
    try:
        return check_cards(cards, conn, cache_lookup(DictionaryCache(cache_dir), overrides_dir))
    finally:
        conn.close()
