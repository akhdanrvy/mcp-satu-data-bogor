"""KT-03: tidak ada nilai kolom measure di respons tool (nol toleransi; FR-14, ADR-005).

Dua lapis (RENCANA_LANGKAH8 bagian 8, disetujui Akhdan 11 Nov 2026):

(a) Struktural. Tidak ada field respons yang bisa membawa nilai kolom measure: `top_values`
    hanya untuk kolom dimension, kolom measure selalu `top_values=null`, dan (di 8c) preview
    serta `filters_applied` hanya kolom dimension. Model kontrak menolak sebaliknya.

(b) Pencarian nilai khas measure di JSON respons. Untuk setiap nilai kolom measure di baris
    asli rekaman, dicari bentuk yang khas: nilai yang PUNYA DESIMAL, yang ≥ 3 DIGIT, atau yang
    BERFORMAT INDONESIA ("9.160.885", "1.234,5"); dalam bentuk mentah (Python/JSON) maupun
    berformat Indonesia. Bilangan kecil mentah (mis. 0, 1, 46) TIDAK dicari, karena wajar muncul
    sebagai row_count, page, atau bagian lain respons; risiko itu ditutup lapis (a).

Kolom measure diambil dari daftar acuan tetap di bawah (bukan dari hasil kamus), agar salah
peran di kamus tetap tertangkap. Langkah 8b: get_dataset_info. Langkah 8c: get_dataset_data.
"""

from __future__ import annotations

import asyncio
import json
import re
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from mcp_bogor.services.normalize import parse_id_number
from mcp_bogor.tools.common import ToolDeps
from mcp_bogor.tools.info import get_dataset_info
from tests.fakes import FakeSourceAdapter, make_settings
from tests.tool_support import synced_settings

RECORDED = Path(__file__).parents[1] / "fixtures" / "recorded" / "2026-11-10"
ROOT = Path(__file__).parents[2]

# Acuan independen: kolom measure ke-12 /bigdata rekaman (10 golden set + KM-07 + KM-12).
MEASURES: dict[str, str] = {
    "tingkat-kemiskinan-di-kota-bogor-2": "tingkat_kemiskinan",
    "kecepatan-lalu-lintas-segmen-1-dan-2-berdasarkan-hari-operasional-di-jalan-raya-pajajaran-"
    "kota-bogor": "kecepatan",
    "realisasi-pendapatan-asli-daerah-pad-kota-bogor": "total_pendapatan",
    "realisasi-belanja-modal-daerah-di-kota-bogor": "realisasi_belanja",
    "persentase-sisa-lebih-perhitungan-anggaran-silpa-terhadap-anggaran-pendapatan-dan-belanja-"
    "daerah-apbd-di-kota-bogor": "persentase_silpa",
    "perkembangan-jumlah-kunjungan-wisatawan-di-kota-bogor": "perkembangan_kunjungan",
    "tren-kunjungan-pasien-puskesmas-di-kota-bogor": "jumlah_pasien",
    "volume-lalu-lintas-segmen-1-dan-2-berdasarkan-hari-operasional-di-jalan-pajajaran--kota-"
    "bogor": "volume",
    "persentase-realisasi-pendapatan-asli-daerah-pad-kota-bogor": "persentase_pendapatan",
    "realisasi-penerimaan-pendapatan-asli-daerah-pad-bersumber-dari-retribusi-daerah-di-kota-"
    "bogor": "realisasi_pad",
    "persentase-kepala-keluarga-miskin-di-kota-bogor": "persentase",
    "persentase-penanganan-pengaduan-masyarakat-ke-inspektorat-daerah-kota-bogor": (
        "persentase_penanganan"
    ),
}


@cache
def _recorded_rows() -> dict[str, list[dict[str, Any]]]:
    """slug → baris /bigdata rekaman (lewat path detail → schema/table)."""
    exchanges = {}
    for path in sorted(RECORDED.glob("[0-9][0-9][0-9]_*.json")):
        exchange = json.loads(path.read_text(encoding="utf-8"))
        exchanges[exchange["request"]["path"]] = exchange["response"]["json"]
    out = {}
    for slug in MEASURES:
        record = exchanges[f"/datasets/{slug}"]["data"]
        out[slug] = exchanges[f"/bigdata/{record['schema']}/{record['table']}"]["data"]
    return out


def _id_format(number: float | int) -> list[str]:
    """Bentuk berformat Indonesia: titik ribuan, koma desimal."""
    if isinstance(number, float) and not number.is_integer():
        whole, frac = f"{number:.10f}".rstrip("0").split(".")
        grouped = f"{int(whole):,}".replace(",", ".")
        return [f"{grouped},{frac}", f"{whole},{frac}"]
    grouped = f"{int(number):,}".replace(",", ".")
    return [grouped] if "." in grouped else []


def distinctive_forms(value: Any) -> set[str]:
    """Bentuk teks khas sebuah nilai measure (aturan lapis b); kosong bila nilai kecil biasa."""
    forms: set[str] = set()
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"-?\d{1,3}(\.\d{3})+(,\d+)?|-?\d+,\d+", text):
            forms.add(text)  # berformat Indonesia apa adanya
        number = parse_id_number(text)
    elif isinstance(value, bool) or value is None:
        return forms
    else:
        number = value
    if number is None:
        return forms
    integral = isinstance(number, int) or float(number).is_integer()
    digits = len(str(abs(int(number))))
    if not integral or digits >= 3:
        forms.add(repr(number) if isinstance(number, float) else str(number))
        if integral:
            forms.add(str(int(number)))
        forms.update(_id_format(number))
    return forms


def find_leaks(payload: dict[str, Any], values: list[Any]) -> list[str]:
    text = json.dumps(payload, ensure_ascii=False)
    leaks = []
    for value in values:
        for form in distinctive_forms(value):
            if re.search(rf"(?<![0-9.,]){re.escape(form)}(?![0-9])", text):
                leaks.append(form)
    return leaks


def test_distinctive_forms_rule() -> None:
    assert distinctive_forms(46) == set()  # bilangan kecil mentah tidak dicari
    assert distinctive_forms(0) == set() and distinctive_forms(100.0) >= {"100", "100.0"}
    assert distinctive_forms(5.89) == {"5.89", "5,89"}
    assert "9.160.885" in distinctive_forms("9.160.885") and "9160885" in distinctive_forms(
        "9.160.885"
    )
    assert "1.015.884.216.919" in distinctive_forms(1015884216919.0)
    assert find_leaks({"x": "nilai 1.234,5"}, [1234.5]) == ["1.234,5"]
    assert find_leaks({"tahun": 2025, "n": 12}, [12.0]) == []


@pytest.fixture(scope="module")
def deps(tmp_path_factory: pytest.TempPathFactory) -> ToolDeps:
    root = tmp_path_factory.mktemp("kt03")
    settings = synced_settings(root)
    settings = make_settings(root, db_path=settings.db_path, overrides_dir=ROOT / "overrides")
    return ToolDeps(settings, adapter=FakeSourceAdapter())


@pytest.mark.parametrize("slug", sorted(MEASURES))
def test_kt03_get_dataset_info(deps: ToolDeps, slug: str) -> None:
    out = asyncio.run(get_dataset_info(deps, {"slug": slug}))
    assert out["status"] == "ok", out["error"]
    measure = MEASURES[slug]
    # (a) struktural
    for column in out["result"]["columns"]:
        if column["role"] != "dimension":
            assert column["top_values"] is None, column["name"]
    measure_column = next(c for c in out["result"]["columns"] if c["name"] == measure)
    assert measure_column["role"] == "measure" and measure_column["top_values"] is None
    # (b) nilai khas measure
    values = [row[measure] for row in _recorded_rows()[slug]]
    assert find_leaks(out, values) == []


# ---------------------------------------------------------------- get_dataset_data (8c)

DATA_VARIANTS: list[tuple[str, dict[str, Any]]] = [
    ("semua", {}),
    ("periode", {"periods": [2024, 2025]}),
    ("filter", {"filters": {"hari_operasional": ["HARI KERJA"]}}),
]


def _search_text(out: dict[str, Any]) -> dict[str, Any]:
    """URL data_ref (cache_id + exp + sig) adalah token bertanda tangan acak, bukan data;
    dikeluarkan dari pencarian lapis (b) agar digit acak di sig tidak menjadi positif palsu.
    Lapis (a) tetap memastikan data_ref hanya berisi url, expires_at, row_count, format."""
    clone = json.loads(json.dumps(out))
    if clone.get("result") and clone["result"].get("data_ref"):
        ref = clone["result"]["data_ref"]
        assert set(ref) == {"url", "expires_at", "row_count", "format"}
        ref["url"] = ref["expires_at"] = "<data_ref>"
    return clone


@pytest.mark.parametrize("variant", [v[0] for v in DATA_VARIANTS])
@pytest.mark.parametrize("slug", sorted(MEASURES))
def test_kt03_get_dataset_data(catalog_db: Path, tmp_path: Path, slug: str, variant: str) -> None:
    from tests.tool_support import ToolEnv

    args = dict(DATA_VARIANTS[[v[0] for v in DATA_VARIANTS].index(variant)][1])
    if "filters" in args and "hari_operasional" not in json.dumps(_recorded_rows()[slug][0]):
        pytest.skip("dataset tanpa kolom hari_operasional")
    env = ToolEnv(catalog_db, tmp_path, overrides_dir=ROOT / "overrides")
    out = env.data(slug=slug, **args)
    measure = MEASURES[slug]
    if out["result"] is not None:
        result = out["result"]
        # (a) struktural
        dims = {c["name"] for c in result["columns"] if c["role"] == "dimension"}
        assert measure not in dims
        assert all(set(row) <= dims for row in result["preview"])
        assert set(result["filters_applied"]) <= dims
        assert all(c["top_values"] is None for c in result["columns"])
    # (b) nilai khas measure, juga di respons galat
    values = [row[measure] for row in _recorded_rows()[slug]]
    assert find_leaks(_search_text(out), values) == []
