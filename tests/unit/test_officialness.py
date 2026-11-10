"""UT-05: resmi hanya bila validate=approve dan klasifikasi=public (FR-07)."""

from __future__ import annotations

import pytest

from mcp_bogor.services.officialness import (
    OFFICIAL_BASIS,
    compute_officialness,
    officialness_from_record,
)


@pytest.mark.parametrize(
    ("validate", "klasifikasi"),
    [("approve", "public"), ("APPROVE", " Public "), (" approve", "public")],
)
def test_resmi(validate: str, klasifikasi: str) -> None:
    result = compute_officialness(validate, klasifikasi)
    assert result.status == "resmi"
    assert result.basis == "validate=approve; klasifikasi=public" == OFFICIAL_BASIS


@pytest.mark.parametrize(
    ("validate", "klasifikasi", "basis"),
    [
        ("draft", "public", "validate=draft, bukan approve"),
        ("approve", "internal", "klasifikasi=internal, bukan public"),
        ("reject", "private", "validate=reject, bukan approve; klasifikasi=private, bukan public"),
        (None, "public", "validate=kosong, bukan approve"),
        ("approve", "None", "klasifikasi=kosong, bukan public"),
        ("", "-", "validate=kosong, bukan approve; klasifikasi=kosong, bukan public"),
    ],
)
def test_draf_with_reason(validate: object, klasifikasi: object, basis: str) -> None:
    result = compute_officialness(validate, klasifikasi)
    assert result.status == "draf"
    assert result.basis == basis


def test_from_record() -> None:
    record = {"validate": "approve", "klasifikasi": "public", "username": "x"}
    assert officialness_from_record(record).status == "resmi"
    assert officialness_from_record({}).status == "draf"
