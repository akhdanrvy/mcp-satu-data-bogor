"""Notice bertemplat (KK-R, Langkah 8a): tanpa teks bebas dan tanpa angka berformat."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mcp_bogor.models import NOTICE_TEMPLATES, Notice, NoticeCode, ok
from mcp_bogor.models.envelope import ToolEnvelope
from tests import model_samples as s


@pytest.mark.parametrize("code", list(NoticeCode))
def test_every_notice_renders_without_numbers(code: NoticeCode) -> None:
    """Templat sendiri tanpa angka; placeholder diisi nilai tanpa digit."""
    column = "total_pendapatan" if "{column}" in NOTICE_TEMPLATES[code] else None
    datasets = ("dataset-a", "dataset-b") if "{dataset}" in NOTICE_TEMPLATES[code] else None
    notice = Notice.from_template(code, column=column, datasets=datasets)
    assert notice.code is code and notice.column == column
    assert not any(ch.isdigit() for ch in notice.message)
    if column:
        assert f"'{column}'" in notice.message
    if datasets:
        assert notice.datasets == list(datasets)
        assert all(f"'{slug}'" in notice.message for slug in datasets)


def test_different_definition_notice_rules() -> None:
    """KK-T: tepat dua slug berbeda; slug boleh berdigit polos, bukan angka berformat."""
    pair = ("tingkat-kemiskinan-di-kota-bogor-2", "persentase-kepala-keluarga-miskin-di-kota-bogor")
    notice = Notice.from_template(NoticeCode.DIFFERENT_DEFINITION, datasets=pair)
    assert notice.datasets == list(pair) and notice.column is None
    with pytest.raises(ValueError):
        Notice.from_template(NoticeCode.DIFFERENT_DEFINITION)
    with pytest.raises(ValueError):
        Notice.from_template(NoticeCode.CATALOG_STALE, datasets=pair)
    with pytest.raises(ValidationError):
        Notice.from_template(NoticeCode.DIFFERENT_DEFINITION, datasets=(pair[0], pair[0]))
    with pytest.raises(ValidationError):
        Notice.from_template(NoticeCode.DIFFERENT_DEFINITION, datasets=("a", "1.234"))
    with pytest.raises(ValidationError):
        Notice(code=NoticeCode.CATALOG_STALE, message="x", datasets=list(pair))


def test_notice_column_rules() -> None:
    with pytest.raises(ValueError):
        Notice.from_template(NoticeCode.DICTIONARY_NOT_VALIDATED)
    with pytest.raises(ValueError):
        Notice.from_template(NoticeCode.REFRESH_THROTTLED, column="x")
    with pytest.raises(ValidationError):
        Notice(code=NoticeCode.EMPTY_RESULT, message="x", column="tahun")
    with pytest.raises(ValidationError):
        Notice(code=NoticeCode.EMPTY_RESULT, message="nilai 9.160.885")
    with pytest.raises(ValidationError):
        Notice.from_template(NoticeCode.UNIT_CONFLICT, column="1.234")


def test_notices_in_envelope_and_not_on_errors() -> None:
    notice = Notice.from_template(NoticeCode.CATALOG_STALE)
    env = ok(s.search_result(), notices=[notice])
    assert env.model_dump(mode="json")["notices"][0]["code"] == "CATALOG_STALE"
    with pytest.raises(ValidationError, match="notices"):
        ToolEnvelope(
            status="error",
            error={"code": "INTERNAL", "message": "x", "retryable": True},
            notices=[notice],
        )
