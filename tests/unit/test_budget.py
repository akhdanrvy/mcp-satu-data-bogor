"""services/budget (KB-38): perkiraan token konservatif dan pemilihan kandidat yang muat."""

from __future__ import annotations

from collections.abc import Iterator

from mcp_bogor.services.budget import (
    BYTES_PER_TOKEN,
    TruncatedReason,
    compact_json_bytes,
    estimate_tokens,
    fit_to_budget,
    truncate_text,
)


def test_estimate_is_bytes_over_three_rounded_up() -> None:
    assert BYTES_PER_TOKEN == 3
    assert compact_json_bytes({"a": 1}) == len('{"a":1}')
    assert estimate_tokens({"a": 1}) == 3  # 7 byte -> 3 token
    assert estimate_tokens("é") == 2  # tanda kutip + 2 byte UTF-8 = 4 byte -> 2 token
    assert estimate_tokens([]) == 1


def test_fit_picks_first_candidate_that_fits_lazily() -> None:
    produced: list[int] = []

    def candidates() -> Iterator[tuple[TruncatedReason | None, str]]:
        steps = ((300, None), (30, TruncatedReason.ITEMS), (3, TruncatedReason.PREVIEW))
        for size, reason in steps:
            produced.append(size)
            yield reason, "x" * size

    assert fit_to_budget(candidates(), 20, lambda c: c) == (TruncatedReason.ITEMS, "x" * 30)
    assert produced == [300, 30]  # kandidat ketiga tidak dibuat
    assert fit_to_budget(iter([(None, "x" * 300)]), 20, lambda c: c) is None


def test_reasons_are_fixed_codes_without_digits() -> None:
    for reason in TruncatedReason:
        assert reason.value.endswith("_dipangkas")
        assert not any(c.isdigit() for c in reason.value)


def test_truncate_text_on_word_boundary() -> None:
    assert truncate_text(None, 5) is None
    assert truncate_text("pendek", 10) == "pendek"
    assert truncate_text("satu dua tiga empat", 12) == "satu dua…"
    assert truncate_text("abcdefghijklmnop", 6) == "abcde…"
    assert len(truncate_text("kata " * 100, 50) or "") <= 50
