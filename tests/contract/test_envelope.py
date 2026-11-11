"""Tes kontrak amplop (Dok 4 bagian 2 dan 7) dan templat pesan galat (KB-10 revisi).

- Invarian amplop, termasuk STALE_DATA dengan status=partial.
- KT-04 (bagian tabel): 10 kode galat = 9 kode Dok 4 bagian 7 + LEVEL_NOT_AVAILABLE (KK-A1).
- KT-02 versi awal: tiap jalur ok/partial/error menghasilkan semua field wajib bertipe benar.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from mcp_bogor.config import CONTRACT_VERSION
from mcp_bogor.models import (
    ALL_TOOLS,
    ERROR_SPECS,
    MESSAGE_TEMPLATES,
    DataResult,
    ErrorCode,
    InfoResult,
    SearchResult,
    ToolEnvelope,
    contains_formatted_number,
    contains_number,
    error,
    ok,
    partial,
)
from mcp_bogor.models.errors import OPTIONAL_CLAUSES, ErrorArgs, ToolError, is_code_allowed
from tests import model_samples as s

SEARCH, INFO, DATA = "search_datasets", "get_dataset_info", "get_dataset_data"

# Disalin dari Dok 4 bagian 7 (kolom Tool dan retryable) + KK-A1. Sengaja ditulis ulang,
# bukan diturunkan dari kode, agar perubahan tabel di kode tertangkap tes ini.
EXPECTED_ERROR_TABLE: dict[str, tuple[bool, set[str]]] = {
    "INVALID_PARAM": (False, {SEARCH, INFO, DATA}),
    "DATASET_NOT_FOUND": (False, {INFO, DATA}),
    "NO_MATCH": (False, {SEARCH}),
    "PERIOD_NOT_AVAILABLE": (False, {DATA}),
    "SOURCE_UNAVAILABLE": (True, {INFO, DATA}),
    "STALE_DATA": (False, {INFO, DATA}),
    "RATE_LIMITED": (True, {INFO, DATA}),
    "RESPONSE_TOO_LARGE": (False, {SEARCH, INFO, DATA}),
    "INTERNAL": (True, {SEARCH, INFO, DATA}),
    "LEVEL_NOT_AVAILABLE": (False, {DATA}),  # KK-A1
}

# Argumen templat yang sah untuk tiap kode (STALE_DATA lewat partial(stale=True)).
SLUG = "contoh-kemiskinan"
SAMPLE_ARGS: dict[ErrorCode, dict[str, Any]] = {
    ErrorCode.INVALID_PARAM: {"param": "page_size", "limit": 20},
    ErrorCode.DATASET_NOT_FOUND: {"slug": SLUG},
    ErrorCode.NO_MATCH: {},
    ErrorCode.PERIOD_NOT_AVAILABLE: {"slug": SLUG, "periods": [2026, 2027]},
    ErrorCode.SOURCE_UNAVAILABLE: {"slug": SLUG},
    ErrorCode.RATE_LIMITED: {},
    ErrorCode.RESPONSE_TOO_LARGE: {"limit": 2000},
    ErrorCode.INTERNAL: {},
    ErrorCode.LEVEL_NOT_AVAILABLE: {"slug": SLUG, "column": "kecamatan"},
}


def _base(**overrides: Any) -> dict[str, Any]:
    return {
        "status": "ok",
        "result": s.data_result(),
        "provenance": s.provenance(),
        "officialness": s.officialness(),
        **overrides,
    }


def _stale_error() -> dict[str, Any]:
    return {"code": "STALE_DATA", "message": "Data dari cache lama", "retryable": False}


def _internal_error() -> dict[str, Any]:
    return {"code": "INTERNAL", "message": "Galat tak terduga", "retryable": True}


# ---------------------------------------------------------------- KT-04 tabel


def test_error_table_matches_dok4_plus_kk_a1() -> None:
    assert len(ErrorCode) == 10
    assert {c.value for c in ErrorCode} == set(EXPECTED_ERROR_TABLE)
    assert set(ERROR_SPECS) == set(ErrorCode)
    for code, (retryable, tools) in EXPECTED_ERROR_TABLE.items():
        spec = ERROR_SPECS[ErrorCode(code)]
        assert spec.retryable is retryable, code
        assert set(spec.tools) == tools, code
    assert {SEARCH, INFO, DATA} == ALL_TOOLS


def test_error_codes_are_uppercase_strings() -> None:
    for code in ErrorCode:
        assert code.value == code.value.upper() == str(code)


@pytest.mark.parametrize("code", [c for c in ErrorCode if c is not ErrorCode.STALE_DATA])
def test_error_constructor_uses_table(code: ErrorCode) -> None:
    for tool in ERROR_SPECS[code].tools:
        env = error(code, **SAMPLE_ARGS[code], tool=tool)
        assert env.error is not None
        assert env.error.retryable is ERROR_SPECS[code].retryable
    for tool in ALL_TOOLS - ERROR_SPECS[code].tools:
        assert not is_code_allowed(code, tool)
        with pytest.raises(ValueError, match="tidak dipakai"):
            error(code, **SAMPLE_ARGS[code], tool=tool)


# ---------------------------------------------------------------- invarian amplop


def test_status_error_requires_error() -> None:
    with pytest.raises(ValidationError, match="wajib disertai error"):
        ToolEnvelope(status="error")


def test_status_error_requires_null_result() -> None:
    with pytest.raises(ValidationError, match="result=null"):
        ToolEnvelope(status="error", error=_internal_error(), result=s.data_result())


def test_status_ok_requires_null_error() -> None:
    with pytest.raises(ValidationError, match="error=null"):
        ToolEnvelope(**_base(error=_internal_error()))


@pytest.mark.parametrize("status", ["ok", "partial"])
def test_ok_and_partial_require_result(status: str) -> None:
    with pytest.raises(ValidationError, match="wajib membawa result"):
        ToolEnvelope(**_base(status=status, result=None))


def test_partial_allows_no_error() -> None:
    env = ToolEnvelope(**_base(status="partial"))
    assert env.error is None


def test_partial_rejects_non_stale_error() -> None:
    with pytest.raises(ValidationError, match="STALE_DATA"):
        ToolEnvelope(**_base(status="partial", error=_internal_error()))


def test_partial_with_stale_data() -> None:
    env = ToolEnvelope(
        **_base(status="partial", error=_stale_error(), provenance=s.provenance("stale_cache"))
    )
    assert env.error is not None and env.error.code is ErrorCode.STALE_DATA
    assert env.result is not None


def test_stale_data_never_with_status_error() -> None:
    with pytest.raises(ValidationError, match="status=partial"):
        ToolEnvelope(status="error", error=_stale_error(), provenance=s.provenance("stale_cache"))
    with pytest.raises(ValueError, match="partial"):
        error(ErrorCode.STALE_DATA)


def test_stale_data_pairs_with_stale_cache() -> None:
    with pytest.raises(ValidationError, match="berpasangan"):
        ToolEnvelope(**_base(status="partial", error=_stale_error()))  # served_from=live
    with pytest.raises(ValidationError, match="berpasangan"):
        ToolEnvelope(**_base(status="partial", error=_stale_error(), provenance=None))
    with pytest.raises(ValidationError, match="berpasangan"):
        ToolEnvelope(**_base(provenance=s.provenance("stale_cache")))  # tanpa STALE_DATA


def test_truncated_requires_reason() -> None:
    with pytest.raises(ValidationError, match="truncated_reason"):
        ToolEnvelope(**_base(status="partial", truncated=True))
    with pytest.raises(ValidationError, match="truncated_reason"):
        ToolEnvelope(**_base(truncated_reason="dipangkas"))
    env = ToolEnvelope(**_base(status="partial", truncated=True, truncated_reason="batas token"))
    assert env.truncated_reason == "batas token"


def test_truncated_never_with_status_ok() -> None:
    with pytest.raises(ValidationError, match="partial"):
        ToolEnvelope(**_base(truncated=True, truncated_reason="batas token"))


def test_contract_version_constant() -> None:
    assert ToolEnvelope(**_base()).contract_version == CONTRACT_VERSION == "0.1"
    with pytest.raises(ValidationError):
        ToolEnvelope(**_base(contract_version="1.0"))


def test_envelope_forbids_extra_and_long_request_id() -> None:
    with pytest.raises(ValidationError, match="extra"):
        ToolEnvelope(**_base(rows=[]))
    with pytest.raises(ValidationError):
        ToolEnvelope(**_base(request_id="r" * 65))


# ---------------------------------------------------------------- konstruktor


def test_ok_constructor() -> None:
    env = ok(
        s.data_result(),
        request_id="rq-1",
        provenance=s.provenance(),
        officialness=s.officialness(),
        quality_flags=[s.quality_flag()],
    )
    assert type(env) is ToolEnvelope[DataResult]
    assert (env.status, env.error, env.truncated) == ("ok", None, False)
    assert env.quality_flags == [s.quality_flag()]


def test_partial_constructor_variants() -> None:
    missing = partial(s.data_result(), provenance=s.provenance())
    assert missing.status == "partial" and missing.error is None and not missing.truncated

    cut = partial(s.search_result(), truncated_reason="batas token MCP_MAX_RESPONSE_TOKENS")
    assert cut.truncated and type(cut) is ToolEnvelope[SearchResult]

    stale = partial(
        s.info_result(),
        provenance=s.provenance("stale_cache"),
        stale=True,
    )
    assert stale.error is not None and stale.error.code is ErrorCode.STALE_DATA
    assert stale.error.message == MESSAGE_TEMPLATES[ErrorCode.STALE_DATA].text


def test_every_code_has_template() -> None:
    assert set(MESSAGE_TEMPLATES) == set(ErrorCode)
    fields = set(ErrorArgs.model_fields)
    for code, template in MESSAGE_TEMPLATES.items():
        assert template.required <= fields, code
        assert template.optional <= set(OPTIONAL_CLAUSES), code
        assert not template.required & template.optional, code
        assert not contains_number(template.text), code


def test_messages_carry_years_limits_and_slug() -> None:
    env = error(ErrorCode.PERIOD_NOT_AVAILABLE, slug=SLUG, periods=[2026, 2027])
    assert env.error is not None
    assert env.error.message == f"Periode 2026, 2027 tidak tersedia di dataset '{SLUG}'."

    env = error(ErrorCode.INVALID_PARAM, param="page_size", limit=20)
    assert env.error is not None
    assert env.error.message == "Parameter 'page_size' tidak valid. Batas: 20."

    env = error(ErrorCode.INVALID_PARAM, param="filters", column="kecamatan")
    assert env.error is not None
    assert env.error.message == "Parameter 'filters' tidak valid. Kolom: 'kecamatan'."

    env = error(ErrorCode.LEVEL_NOT_AVAILABLE, slug=SLUG, column="kelurahan")
    assert env.error is not None and "'kelurahan'" in env.error.message


def test_error_rejects_free_text() -> None:
    with pytest.raises(TypeError):
        error(ErrorCode.INTERNAL, "Gagal membaca 9.160.885 baris")  # type: ignore[misc]


@pytest.mark.parametrize(
    ("code", "kwargs", "match"),
    [
        (ErrorCode.DATASET_NOT_FOUND, {}, "butuh argumen"),
        (ErrorCode.PERIOD_NOT_AVAILABLE, {"slug": SLUG}, "butuh argumen"),
        (ErrorCode.NO_MATCH, {"slug": SLUG}, "tidak memakai"),
        (ErrorCode.INTERNAL, {"limit": 5}, "tidak memakai"),
        (ErrorCode.DATASET_NOT_FOUND, {"slug": "9.160.885"}, "angka berformat"),
        (ErrorCode.DATASET_NOT_FOUND, {"slug": "dua kata"}, "pattern"),
        (ErrorCode.DATASET_NOT_FOUND, {"slug": "x" * 256}, "at most"),
        (ErrorCode.LEVEL_NOT_AVAILABLE, {"slug": SLUG, "column": "1.234,5"}, "angka berformat"),
        (ErrorCode.INVALID_PARAM, {"param": "where"}, "parameter tool"),
        (ErrorCode.INVALID_PARAM, {"param": "page", "limit": -1}, "greater than"),
        (ErrorCode.PERIOD_NOT_AVAILABLE, {"slug": SLUG, "periods": [1999]}, "greater than"),
        (ErrorCode.PERIOD_NOT_AVAILABLE, {"slug": SLUG, "periods": []}, "at least"),
    ],
)
def test_error_rejects_bad_template_args(code: ErrorCode, kwargs: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        error(code, **kwargs)


def test_tool_error_direct_rejects_formatted_number() -> None:
    with pytest.raises(ValidationError, match="angka berformat"):
        ToolError(code="INTERNAL", message="Total 9.160.885 orang", retryable=True)
    # Tahun dan batas polos boleh.
    assert ToolError(code="INTERNAL", message="Periode 2026; batas 20", retryable=True)


def test_error_constructor_with_result_type_round_trips() -> None:
    env = error(ErrorCode.DATASET_NOT_FOUND, slug=SLUG, result_type=InfoResult)
    assert type(env) is ToolEnvelope[InfoResult]
    assert ToolEnvelope[InfoResult].model_validate_json(env.model_dump_json()) == env


# ---------------------------------------------------------------- KT-02 versi awal

ENVELOPE_FIELD_TYPES: dict[str, tuple[type, ...]] = {
    "contract_version": (str,),
    "status": (str,),
    "error": (dict, type(None)),
    "request_id": (str, type(None)),
    "provenance": (dict, type(None)),
    "officialness": (dict, type(None)),
    "quality_flags": (list,),
    "truncated": (bool,),
    "truncated_reason": (str, type(None)),
    "notices": (list,),  # KK-R (Langkah 8)
    "result": (dict, type(None)),
}


def _all_paths() -> list[tuple[str, ToolEnvelope[Any], type]]:
    rid = {"request_id": "rq-91c2"}
    one = {"provenance": s.provenance(), "officialness": s.officialness(), **rid}
    stale = {"provenance": s.provenance("stale_cache"), "officialness": s.officialness(), **rid}
    paths: list[tuple[str, ToolEnvelope[Any], type]] = [
        ("search-ok", ok(s.search_result(), **rid), SearchResult),
        (
            "search-truncated",
            partial(s.search_result(), truncated_reason="token", **rid),
            SearchResult,
        ),
        ("info-ok", ok(s.info_result(), **one), InfoResult),
        ("info-stale", partial(s.info_result(), stale=True, **stale), InfoResult),
        ("data-ok", ok(s.data_result(), quality_flags=[s.quality_flag()], **one), DataResult),
        ("data-missing-period", partial(s.data_result(), **one), DataResult),
        ("data-stale", partial(s.data_result(), stale=True, **stale), DataResult),
    ]
    for code in ErrorCode:
        if code is ErrorCode.STALE_DATA:
            continue
        for tool, rtype in ((SEARCH, SearchResult), (INFO, InfoResult), (DATA, DataResult)):
            if is_code_allowed(code, tool):
                paths.append(
                    (
                        f"{tool}-{code}",
                        error(code, **SAMPLE_ARGS[code], tool=tool, result_type=rtype),
                        rtype,
                    )
                )
    return paths


PATHS = _all_paths()


@pytest.mark.parametrize(("name", "env", "rtype"), PATHS, ids=[p[0] for p in PATHS])
def test_kt02_envelope_fields(name: str, env: ToolEnvelope[Any], rtype: type) -> None:
    payload = env.model_dump(mode="json")
    assert set(payload) == set(ENVELOPE_FIELD_TYPES), name
    for field, types in ENVELOPE_FIELD_TYPES.items():
        assert isinstance(payload[field], types), (name, field)

    assert payload["contract_version"] == "0.1"
    assert payload["status"] in {"ok", "partial", "error"}

    if payload["error"] is not None:
        err = payload["error"]
        assert set(err) == {"code", "message", "retryable"}
        assert isinstance(err["code"], str) and isinstance(err["message"], str)
        assert isinstance(err["retryable"], bool)
        assert err["code"] in EXPECTED_ERROR_TABLE
        assert err["retryable"] is EXPECTED_ERROR_TABLE[err["code"]][0]

    if payload["status"] == "error":
        assert payload["result"] is None and payload["error"] is not None
        assert not contains_formatted_number(payload["error"]["message"])
    else:
        assert isinstance(payload["result"], dict)

    if payload["provenance"] is not None:
        assert payload["provenance"]["retrieved_at"].endswith("+07:00")

    # round-trip lewat JSON dengan tipe result yang tepat
    again = ToolEnvelope[rtype].model_validate_json(env.model_dump_json())
    assert again.model_dump(mode="json") == payload


def test_kt02_covers_every_status_and_code() -> None:
    statuses = {env.status for _, env, _ in PATHS}
    codes = {env.error.code for _, env, _ in PATHS if env.error is not None}
    assert statuses == {"ok", "partial", "error"}
    assert codes == set(ErrorCode)


def test_json_schema_generation() -> None:
    for rtype in (SearchResult, InfoResult, DataResult):
        schema = ToolEnvelope[rtype].model_json_schema()
        assert set(schema["required"]) == {"status"}
        assert set(schema["properties"]) == set(ENVELOPE_FIELD_TYPES)


def test_unknown_argument_message_rules() -> None:
    """KB-42 (revisi): nama argumen asing hanya disebut bila berbentuk identifier."""
    from mcp_bogor.models.errors import ErrorArgs

    env = error(ErrorCode.INVALID_PARAM, param="where", unknown=True)
    assert env.error is not None
    assert env.error.message == "Parameter 'where' tidak valid. Argumen ini tidak dikenal."
    plain = error(ErrorCode.INVALID_PARAM, param="query", unknown=False)
    assert plain.error is not None and plain.error.message == "Parameter 'query' tidak valid."
    with pytest.raises(ValidationError):
        ErrorArgs(param="where")  # nama asing tanpa unknown=True
    with pytest.raises(ValidationError):
        ErrorArgs(param="9.160.885", unknown=True)  # bukan identifier
    with pytest.raises(ValueError):
        error(ErrorCode.NO_MATCH, unknown=True)  # templat lain tidak memakai klausa ini
