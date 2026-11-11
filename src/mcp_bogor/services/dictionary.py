"""Kamus kolom (Dok 2 bagian 4, KK-01..08) dan peran kolom (KK-04 + KK-I). Langkah 6.

Fungsi murni: tanpa jaringan, berkas, atau jam sistem. Masukan disiapkan pemanggil:
deskripsi HTML dataset, `ColumnProbe` (nama kolom asli + metadata_filter rata), sampel
baris /bigdata (halaman pertama), metadata dataset rata (`Satuan Dataset`), dan override
(KK-07) yang sudah dibaca dari berkas oleh `storage/overrides.py`. Penyimpanan hasil ke
cache (KK-08) dikerjakan di Langkah 7-8.

Prinsip:
- Arti kolom hanya dari template deskripsi atau override; tidak pernah ditebak (KK-M).
- Pencocokan nama: persis setelah normalisasi ejaan nama (huruf kecil, spasi/`+` → `_`),
  ditambah tabel alias tetap untuk kolom wilayah standar. Kemiripan nama lain hanya
  dilaporkan sebagai saran, tidak diterapkan.
- Peran kolom ditentukan setelah urai NB-07 atas sampel, agar angka bertipe teks
  ("9.160.885", KM-08) tidak lolos sebagai dimension (KT-03).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from difflib import SequenceMatcher
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from mcp_bogor.adapters.base import ColumnProbe
from mcp_bogor.models.common import is_numeric_label_column, looks_like_formatted_number
from mcp_bogor.models.results import ColumnInfo, ColumnRole, DimensionValue
from mcp_bogor.services.normalize import (
    INTERNAL_ID_COLUMNS,
    html_to_text,
    is_empty,
    normalize_label,
    normalize_rows,
)

# ---------------------------------------------------------------- konstanta

# KB-30: ambang aturan (e). Kolom yang >= 90% nilai non-null sampelnya terurai NB-07
# sebagai angka diberi peran measure.
NUMERIC_SHARE_THRESHOLD = 0.9

# KB-30: jumlah maksimum top_values per kolom dimension (Dok 4 bagian 5: <= 10).
MAX_TOP_VALUES = 10

# KB-30: ambang kemiripan nama untuk SARAN (tidak pernah diterapkan otomatis).
SUGGESTION_MIN_RATIO = 0.75

# KK-02: alias tetap kolom wilayah standar. Setiap kelompok = satu kolom yang sama;
# nama deskripsi dan nama data dianggap cocok bila berada di kelompok yang sama.
REGION_ALIAS_GROUPS: tuple[frozenset[str], ...] = (
    frozenset(
        {
            "bps_kode_kabupaten_kota",
            "kode_kota",
            "kode_kabupaten_kota",
            "bps_kode_kabupatean_kota",
        }
    ),
    frozenset(
        {
            "bps_nama_kabupaten_kota",
            "nama_kota",
            "nama_kabupaten_kota",
            "bps_nama_kabupatean_kota",
        }
    ),
    frozenset({"kode_provinsi", "kode_prov"}),
    frozenset({"nama_provinsi", "nama_prov"}),
)
_ALIAS_INDEX = {name: group for group in REGION_ALIAS_GROUPS for name in group}

# Aturan (c): kolom nama wilayah.
_REGION_NAME = re.compile(r"^(nama_provinsi|(bps|kemendagri)_nama_[a-z0-9_]+)$")

# Naikkan bila bentuk DictionaryResult berubah; kamus lama di cache otomatis dibuat ulang.
DICTIONARY_RESULT_FORMAT = 1

TemplateType = Literal["number", "string", "category", "unknown"]
ColumnStatus = Literal["documented", "undocumented"]
MeaningSource = Literal["template", "override", "none"]

_TYPE_MAP: dict[str, TemplateType] = {
    "numerik": "number",
    "teks": "string",
    "text": "string",
    "kategorik": "category",
}

# KK-01: "- nama_kolom: [menyatakan] <arti> [dengan tipe data <tipe>][.]"
# Pemisah ":" (atau ";" pada satu deskripsi asli); nama boleh berspasi.
_VARIABLE_LINE = re.compile(r"^-\s*(?P<name>[^:;]+?)\s*[:;]\s*(?P<rest>.*)$")
_TYPE_CLAUSE = re.compile(r"\s*\bdengan\s+tipe\s+data\b\s*(?P<type>.*)$", re.IGNORECASE)
_MENYATAKAN = re.compile(r"^menyatakan\s*", re.IGNORECASE)
_NAME_SEPARATORS = re.compile(r"[\s+_]+")
_VALID_NAME = re.compile(r"^[a-z0-9_]+$")
_STRICT_TEMPLATE = re.compile(r"^-\s[a-z0-9_]+:\smenyatakan\s.+\sdengan\stipe\sdata\s\S.*$")


# ---------------------------------------------------------------- KK-01 / KK-02: urai template


@dataclass(frozen=True, slots=True)
class TemplateEntry:
    """Satu baris variabel deskripsi. `name` sudah dinormalisasi ejaannya."""

    raw_name: str
    name: str
    meaning: str | None
    type: TemplateType | None  # None = baris tanpa "dengan tipe data" (KK-01)
    strict: bool  # cocok persis dengan template baku (untuk statistik)


@dataclass(frozen=True, slots=True)
class TemplateParse:
    entries: list[TemplateEntry]
    variable_lines: int  # baris berawalan "- "
    unparsed_lines: int  # baris "- " yang tidak berbentuk "nama: arti"


def normalize_type(raw: str | None) -> TemplateType | None:
    """KK-02: numerik → number, teks/text → string, kategorik → category, lainnya → unknown.

    Hanya kata pertama yang dibaca ("teks (misalnya, "unit")" → string). None/kosong → None.
    """
    if raw is None:
        return None
    words = re.findall(r"[a-z]+", raw.casefold())
    if not words:
        return None
    return _TYPE_MAP.get(words[0], "unknown")


def normalize_column_name(raw: str) -> str:
    """Ejaan nama kolom: huruf kecil; spasi, `+`, dan `_` berurutan → satu `_`.

    "kapasitas jalan" → "kapasitas_jalan", "bps_nama_ kabupaten_kota" →
    "bps_nama_kabupaten_kota". Bukan pencocokan fuzzy: hanya tanda pemisah yang diseragamkan.
    """
    return _NAME_SEPARATORS.sub("_", raw.strip().casefold()).strip("_")


def parse_template_line(line: str) -> TemplateEntry | None:
    """Satu baris "- nama: ..." → TemplateEntry; bukan baris variabel → None."""
    match = _VARIABLE_LINE.match(line.strip())
    if match is None:
        return None
    raw_name = match.group("name").strip()
    name = normalize_column_name(raw_name)
    if not name:
        return None
    rest = match.group("rest").strip()
    type_match = _TYPE_CLAUSE.search(rest)
    data_type: TemplateType | None = None
    if type_match is not None:
        data_type = normalize_type(type_match.group("type"))
        rest = rest[: type_match.start()]
    meaning = _MENYATAKAN.sub("", rest).strip().rstrip(".").strip() or None
    return TemplateEntry(
        raw_name=raw_name,
        name=name,
        meaning=meaning,
        type=data_type,
        strict=_STRICT_TEMPLATE.match(line.strip()) is not None,
    )


def parse_template(description: str | None) -> TemplateParse:
    """KK-01: urai semua baris variabel dari deskripsi (HTML atau teks polos)."""
    text = html_to_text(description) or ""
    entries: list[TemplateEntry] = []
    variable_lines = unparsed = 0
    for line in text.splitlines():
        if not line.startswith("-") or not line.lstrip("-").strip():
            continue  # bukan butir, atau butir kosong "-"
        variable_lines += 1
        entry = parse_template_line(line)
        if entry is None:
            unparsed += 1
        else:
            entries.append(entry)
    return TemplateParse(entries=entries, variable_lines=variable_lines, unparsed_lines=unparsed)


# ---------------------------------------------------------------- KK-04 + KK-I: peran kolom


def is_region_name_column(name: str) -> bool:
    return _REGION_NAME.match(name.casefold()) is not None


def column_role(
    name: str,
    data_type: str | None,
    numeric_share: float | None = None,
    *,
    id_columns: Iterable[str] = INTERNAL_ID_COLUMNS,
) -> ColumnRole:
    """Peran kolom, aturan berurutan (KK-04 + KK-I, Langkah 6):

    (a) id internal (KB-17) → id;
    (b) token `kode`, awalan `no_`, atau `tahun`/`bulan`/`triwulan`/`semester`/`satuan`
        → dimension;
    (c) nama wilayah (`nama_provinsi`, `bps_nama_*`, `kemendagri_nama_*`) → dimension;
    (d) tipe number dari template → measure;
    (e) >= NUMERIC_SHARE_THRESHOLD nilai non-null sampel terurai NB-07 sebagai angka
        (termasuk nilai yang sudah bertipe angka) → measure;
    (f) selain itu → dimension.
    """
    if name in frozenset(id_columns):
        return "id"
    if is_numeric_label_column(name):
        return "dimension"
    if is_region_name_column(name):
        return "dimension"
    if data_type == "number":
        return "measure"
    if numeric_share is not None and numeric_share >= NUMERIC_SHARE_THRESHOLD:
        return "measure"
    return "dimension"


def numeric_shares(
    rows: Sequence[Mapping[str, Any]], columns: Iterable[str]
) -> dict[str, float | None]:
    """Porsi nilai non-null per kolom yang terurai NB-07 sebagai angka.

    Memakai `normalize_rows` dengan semua kolom diperlakukan sebagai number; jumlah
    `ParseIssue` per kolom = nilai yang gagal diurai. None bila kolom tanpa nilai non-null.
    Kolom kode (NB-05) tidak diurai normalize dan selalu dimension (aturan b).
    """
    names = list(columns)
    _, issues = normalize_rows(rows, dict.fromkeys(names, "number"), drop_columns=())
    failed: dict[str, int] = {}
    for issue in issues:
        failed[issue.column] = failed.get(issue.column, 0) + 1
    shares: dict[str, float | None] = {}
    for name in names:
        non_null = sum(1 for row in rows if not is_empty(row.get(name)))
        shares[name] = None if non_null == 0 else (non_null - failed.get(name, 0)) / non_null
    return shares


# ---------------------------------------------------------------- KK-07: override


class OverrideError(ValueError):
    """Berkas override tidak sah atau merujuk kolom yang tidak ada di data."""


class ColumnOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meaning: str | None = Field(default=None, min_length=1)
    role: ColumnRole | None = None
    unit: str | None = Field(default=None, min_length=1)
    # KB-30 (8): siapa dan kapan arti/satuan diverifikasi. Wajib berpasangan.
    reviewed_by: str | None = Field(default=None, min_length=1)
    reviewed_at: date | None = None

    @model_validator(mode="after")
    def _review_pair(self) -> ColumnOverride:
        if (self.reviewed_by is None) != (self.reviewed_at is None):
            raise ValueError("reviewed_by dan reviewed_at harus diisi bersama")
        return self

    @property
    def validates_measure(self) -> bool:
        """KB-30 (8): override memvalidasi measure undocumented hanya bila keempatnya ada."""
        return None not in (self.meaning, self.unit, self.reviewed_by, self.reviewed_at)


class DictionaryOverride(BaseModel):
    """Isi overrides/<slug>.yaml (KK-07)."""

    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=1)
    note: str | None = None
    columns: dict[str, ColumnOverride] = Field(min_length=1)

    def digest(self) -> str:
        """Sidik isi override; kamus di cache dibuat ulang bila berubah (KK-08)."""
        return hashlib.sha256(self.model_dump_json().encode("utf-8")).hexdigest()

    @field_validator("columns")
    @classmethod
    def _non_empty_entries(cls, value: dict[str, ColumnOverride]) -> dict[str, ColumnOverride]:
        for name, entry in value.items():
            if not name.strip():
                raise ValueError("nama kolom override tidak boleh kosong")
            if entry.meaning is None and entry.role is None and entry.unit is None:
                raise ValueError(f"override kolom {name!r} kosong (isi meaning, role, atau unit)")
        return value


def parse_override(data: Any, slug: str) -> DictionaryOverride:
    """Validasi skema override (hasil yaml.safe_load). Slug wajib sama dengan nama berkas."""
    if not isinstance(data, Mapping):
        raise OverrideError(f"override {slug}: isi berkas harus objek")
    try:
        override = DictionaryOverride.model_validate(data)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise OverrideError(f"override {slug} tidak sah: {details}") from exc
    if override.slug != slug:
        raise OverrideError(f"override {slug}: field slug berisi {override.slug!r}")
    return override


# ---------------------------------------------------------------- hasil


@dataclass(frozen=True, slots=True)
class ColumnReport:
    """Status validasi satu kolom data (KK-03)."""

    name: str
    status: ColumnStatus
    role: ColumnRole
    template_name: str | None = None  # nama di deskripsi yang dicocokkan
    matched_by: Literal["exact", "alias"] | None = None
    numeric_share: float | None = None
    overridden: bool = False
    # Override lengkap (meaning, unit, reviewed_by, reviewed_at) untuk kolom ini.
    curated: bool = False


@dataclass(frozen=True, slots=True)
class Suggestion:
    """Kemiripan nama orphan ↔ kolom undocumented. Hanya saran; tidak diterapkan."""

    template_name: str
    column: str
    ratio: float


@dataclass(frozen=True, slots=True)
class ValidationReason:
    # measure_undocumented: tanpa override lengkap (override parsial tetap dihitung di sini).
    code: Literal["measure_undocumented", "no_measure"]
    column: str | None = None


@dataclass(frozen=True)
class DictionaryResult:
    columns: list[ColumnInfo]
    reports: list[ColumnReport]
    orphans: list[str]  # nama di deskripsi yang tidak ada di data (KK-03)
    value_labels: list[str]  # baris deskripsi yang ternyata menjelaskan nilai kategori
    duplicates: list[str]  # nama yang muncul lebih dari sekali di deskripsi
    suggestions: list[Suggestion]
    dictionary_validated: bool
    reasons: list[ValidationReason]
    template_lines: int
    template_unparsed: int
    unit_source: Literal["dataset", "column", "conflict", "none"]
    unit_values: list[str] = field(default_factory=list)  # nilai unik kolom satuan
    # KK-06: kolom satuan (satu nilai) berbeda dengan "Satuan Dataset" (tanpa beda huruf).
    unit_differs_from_dataset: bool = False
    top_values_withheld: list[str] = field(default_factory=list)

    def status_of(self, name: str) -> ColumnStatus | None:
        return next((r.status for r in self.reports if r.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        """Bentuk JSON untuk cache kamus (storage.cache.DictionaryCache, KK-08)."""
        data = asdict(self)
        data["columns"] = [c.model_dump(mode="json") for c in self.columns]
        return {"format": DICTIONARY_RESULT_FORMAT, **data}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DictionaryResult:
        """Kebalikan to_dict. Format lain → ValueError (pemanggil membuat ulang kamus)."""
        if data.get("format") != DICTIONARY_RESULT_FORMAT:
            raise ValueError("format kamus di cache tidak dikenal")
        try:
            return cls(
                columns=[ColumnInfo.model_validate(c) for c in data["columns"]],
                reports=[ColumnReport(**r) for r in data["reports"]],
                orphans=list(data["orphans"]),
                value_labels=list(data["value_labels"]),
                duplicates=list(data["duplicates"]),
                suggestions=[Suggestion(**x) for x in data["suggestions"]],
                dictionary_validated=bool(data["dictionary_validated"]),
                reasons=[ValidationReason(**r) for r in data["reasons"]],
                template_lines=int(data["template_lines"]),
                template_unparsed=int(data["template_unparsed"]),
                unit_source=data["unit_source"],
                unit_values=list(data["unit_values"]),
                unit_differs_from_dataset=bool(data["unit_differs_from_dataset"]),
                top_values_withheld=list(data["top_values_withheld"]),
            )
        except (KeyError, TypeError, ValidationError) as exc:
            raise ValueError("isi kamus di cache tidak sah") from exc


# ---------------------------------------------------------------- bantuan


def _unique_values(
    column: str, metadata_filter: Mapping[str, list[Any]], rows: Sequence[Mapping[str, Any]]
) -> list[Any]:
    """Nilai unik non-kosong: metadata_filter dulu (lengkap), cadangan sampel baris."""
    source = metadata_filter.get(column) or [row.get(column) for row in rows]
    seen: set[Any] = set()
    out: list[Any] = []
    for value in source:
        if is_empty(value) or isinstance(value, bool | float | dict | list):
            continue
        key = value.strip() if isinstance(value, str) else value
        if key not in seen:
            seen.add(key)
            out.append(value)
    return out


def _top_values(column: str, values: list[Any]) -> tuple[list[DimensionValue] | None, bool]:
    """(top_values, ditahan). Ditahan bila ada nilai berformat angka di kolom non-label."""
    picked: list[DimensionValue] = [v for v in values if isinstance(v, str | int)][:MAX_TOP_VALUES]
    if not picked:
        return None, False
    if not is_numeric_label_column(column) and any(
        isinstance(v, str) and looks_like_formatted_number(v) for v in picked
    ):
        return None, True  # KT-03 lapis kedua: jangan bawa angka berformat lewat dimensi
    return picked, False


def _label_key(value: Any) -> str | None:
    if not isinstance(value, str) or is_empty(value):
        return None
    return normalize_label(value)[0].casefold()


def _match_entries(
    entries: list[TemplateEntry], data_columns: list[str]
) -> tuple[dict[str, tuple[TemplateEntry, str]], list[TemplateEntry], list[str]]:
    """Cocokkan entri deskripsi ke kolom data: persis dulu, lalu alias wilayah.

    Hasil: ({kolom_data: (entri, "exact"|"alias")}, entri_tak_cocok, nama_ganda).
    """
    by_norm = {normalize_column_name(c): c for c in data_columns}
    matched: dict[str, tuple[TemplateEntry, str]] = {}
    unmatched: list[TemplateEntry] = []
    duplicates: list[str] = []
    seen: set[str] = set()
    pending: list[TemplateEntry] = []
    for entry in entries:
        if entry.name in seen:
            duplicates.append(entry.name)
            continue
        seen.add(entry.name)
        column = by_norm.get(entry.name)
        if column is not None and column not in matched:
            matched[column] = (entry, "exact")
        else:
            pending.append(entry)
    for entry in pending:
        group = _ALIAS_INDEX.get(entry.name)
        target = next(
            (by_norm[n] for n in sorted(group or ()) if n in by_norm and by_norm[n] not in matched),
            None,
        )
        if target is not None:
            matched[target] = (entry, "alias")
        else:
            unmatched.append(entry)
    return matched, unmatched, duplicates


def _suggest(orphans: list[str], undocumented: list[str]) -> list[Suggestion]:
    out = []
    for orphan in orphans:
        for column in undocumented:
            ratio = SequenceMatcher(None, orphan, column).ratio()
            if ratio >= SUGGESTION_MIN_RATIO:
                out.append(Suggestion(orphan, column, round(ratio, 2)))
    return sorted(out, key=lambda s: (-s.ratio, s.template_name, s.column))


def _resolve_unit(
    dataset_unit: Any, satuan_values: list[Any]
) -> tuple[str | None, Literal["dataset", "column", "conflict", "none"], list[str]]:
    """KK-06: satuan measure. Kolom `satuan` dengan satu nilai unik menang; >1 → None."""
    labels: dict[str, str] = {}
    for value in satuan_values:
        key = _label_key(value)
        if key is not None:
            labels.setdefault(key, normalize_label(value)[0])
    values = list(labels.values())
    if len(values) > 1:
        return None, "conflict", values
    if len(values) == 1:
        return values[0], "column", values
    if isinstance(dataset_unit, str) and not is_empty(dataset_unit):
        return dataset_unit.strip(), "dataset", values
    return None, "none", values


# ---------------------------------------------------------------- KK-03..07: kamus lengkap


def build_dictionary(
    *,
    description: str | None,
    probe: ColumnProbe,
    sample_rows: Sequence[Mapping[str, Any]] = (),
    dataset_metadata: Mapping[str, Any] | None = None,
    override: DictionaryOverride | None = None,
    id_columns: Iterable[str] = INTERNAL_ID_COLUMNS,
) -> DictionaryResult:
    """Kamus kolom satu dataset: list[ColumnInfo] + ringkasan validasi.

    - Urutan kolom mengikuti `probe.columns` (cadangan: kunci sampel baris).
    - Override untuk kolom yang tidak ada di data → OverrideError.
    - dictionary_validated (KK-05/06) false bila ada measure undocumented tanpa override
      lengkap (meaning, unit, reviewed_by, reviewed_at; KB-30 (8)), atau tidak ada measure.
    """
    data_columns = list(probe.columns) or list(dict.fromkeys(k for row in sample_rows for k in row))
    id_set = frozenset(id_columns)
    overrides = dict(override.columns) if override is not None else {}
    unknown = sorted(set(overrides) - set(data_columns))
    if unknown:
        slug = override.slug if override is not None else "-"
        raise OverrideError(
            f"override {slug}: kolom {', '.join(unknown)} tidak ada di data "
            f"(kolom data: {', '.join(data_columns)})"
        )

    parsed = parse_template(description)
    matched, unmatched, duplicates = _match_entries(parsed.entries, data_columns)

    # Baris deskripsi yang menjelaskan nilai kategori (mis. "- HARI KERJA: ...").
    value_keys = {
        key
        for column in data_columns
        for value in _unique_values(column, probe.metadata_filter, sample_rows)
        if (key := _label_key(value)) is not None
    }
    value_labels = [e.raw_name for e in unmatched if e.raw_name.casefold() in value_keys]
    orphans = [e.name for e in unmatched if e.raw_name.casefold() not in value_keys]

    shares = numeric_shares(sample_rows, [c for c in data_columns if c not in id_set])
    satuan_column = next((c for c in data_columns if c.casefold() == "satuan"), None)
    satuan_values = (
        _unique_values(satuan_column, probe.metadata_filter, sample_rows) if satuan_column else []
    )
    meta = dataset_metadata or {}
    dataset_unit = meta.get("Satuan Dataset")
    unit, unit_source, unit_values = _resolve_unit(dataset_unit, satuan_values)
    unit_differs = (
        unit_source == "column"
        and isinstance(dataset_unit, str)
        and not is_empty(dataset_unit)
        and _label_key(dataset_unit) != _label_key(unit)
    )

    columns: list[ColumnInfo] = []
    reports: list[ColumnReport] = []
    withheld: list[str] = []
    for name in data_columns:
        entry, matched_by = matched.get(name, (None, None))
        ov = overrides.get(name)
        share = shares.get(name)
        role = column_role(name, entry.type if entry else None, share, id_columns=id_set)
        if ov is not None and ov.role is not None:
            role = ov.role

        meaning: str | None = entry.meaning if entry else None
        source: MeaningSource = "template" if meaning else "none"
        if ov is not None and ov.meaning is not None:
            meaning, source = ov.meaning, "override"

        column_unit = unit if role == "measure" else None
        if ov is not None and ov.unit is not None:
            column_unit = ov.unit

        top: list[DimensionValue] | None = None
        if role == "dimension":
            top, held = _top_values(name, _unique_values(name, probe.metadata_filter, sample_rows))
            if held:
                withheld.append(name)

        columns.append(
            ColumnInfo(
                name=name,
                role=role,
                type=entry.type if entry else None,
                meaning=meaning,
                unit=column_unit,
                meaning_source=source,
                top_values=top,
            )
        )
        reports.append(
            ColumnReport(
                name=name,
                status="documented" if entry else "undocumented",
                role=role,
                template_name=entry.name if entry else None,
                matched_by=matched_by,  # type: ignore[arg-type]
                numeric_share=share,
                overridden=ov is not None,
                curated=ov is not None and ov.validates_measure,
            )
        )

    reasons: list[ValidationReason] = []
    for rep in reports:
        if rep.role == "measure" and rep.status == "undocumented" and not rep.curated:
            reasons.append(ValidationReason("measure_undocumented", rep.name))
    if not any(r.role == "measure" for r in reports):
        reasons.append(ValidationReason("no_measure"))

    undocumented = [r.name for r in reports if r.status == "undocumented" and r.role != "id"]
    return DictionaryResult(
        columns=columns,
        reports=reports,
        orphans=orphans,
        value_labels=value_labels,
        duplicates=duplicates,
        suggestions=_suggest(orphans, undocumented),
        dictionary_validated=not reasons,
        reasons=reasons,
        template_lines=parsed.variable_lines,
        template_unparsed=parsed.unparsed_lines,
        unit_source=unit_source,
        unit_values=unit_values,
        unit_differs_from_dataset=unit_differs,
        top_values_withheld=withheld,
    )
