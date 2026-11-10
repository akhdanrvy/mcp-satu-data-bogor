"""Tes scripts/record_fixtures.py tanpa jaringan (respx; penjaga jaringan conftest aktif)."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
import respx

from mcp_bogor.config import WIB
from tests.http_fixtures import validate_exchange

ROOT = Path(__file__).parents[2]
BASE = "https://portal.test/api"
UA = "mcp-bogor/0.1 (+tim-mcp@contoh.test)"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "record_fixtures", ROOT / "scripts" / "record_fixtures.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["record_fixtures"] = module
    spec.loader.exec_module(module)
    return module


rf = _load_script()


class FakeSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def _clock() -> datetime:
    return datetime(2026, 11, 10, 9, 0, tzinfo=WIB)


def _ok(data: Any, **extra: Any) -> httpx.Response:
    return httpx.Response(200, json={"code": 200, "message": "OK", "data": data, **extra})


def _recorder(budget: int = 60) -> tuple[Any, FakeSleep, httpx.Client]:
    sleep = FakeSleep()
    client = httpx.Client(base_url=BASE)
    return rf.Recorder(client, budget=budget, sleep=sleep, clock=_clock), sleep, client


# ---------------------------------------------------------------- dry-run dan rencana


def test_plan_from_real_targets() -> None:
    targets = rf.load_targets()
    slugs = rf.golden_slugs(ROOT / targets["golden_set"]["path"])
    assert len(slugs) == targets["golden_set"]["expected_count"] == 10
    plan = rf.build_plan(targets, slugs)
    assert len(plan) == 35 <= rf.MAX_REQUESTS
    assert rf.upper_bound(targets, plan) <= rf.MAX_REQUESTS
    assert sum(p.conditional for p in plan) == 4
    paths = [p.path for p in plan]
    assert paths.count("/datasets") == 8
    assert "/organisasi" in paths and "/topik" in paths
    assert all(f"/datasets/{s}" in paths for s in slugs)
    assert paths[-1] == f"/datasets/{targets['invalid_slug']}"
    assert not any("/view" in p for p in paths)


def test_dry_run_sends_nothing(
    settings_env: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    with respx.mock(assert_all_mocked=True) as router:
        code = rf.main(["--out-root", str(tmp_path / "rec"), "--report", str(tmp_path / "r.md")])
        assert router.calls.call_count == 0
    out = capsys.readouterr().out
    assert code == 0
    assert out.startswith("DRY-RUN")
    assert "Jumlah terencana: 35 permintaan (31 pasti + 4 bersyarat)" in out
    assert "DITOLAK saat --execute" in out  # UA bawaan masih placeholder
    assert not (tmp_path / "rec").exists() and not (tmp_path / "r.md").exists()


def test_dry_run_flags_existing_folder(tmp_path: Path) -> None:
    targets = rf.load_targets()
    plan = rf.build_plan(targets, ["a"])
    text = rf.format_dry_run(plan, targets, base_url=BASE, user_agent=UA, out_dir=tmp_path)
    assert "SUDAH ADA" in text and "DITOLAK" not in text


def test_golden_count_warning(
    settings_env: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    targets_file = tmp_path / "t.yaml"
    text = (ROOT / "scripts" / "fixture_targets.yaml").read_text(encoding="utf-8")
    targets_file.write_text(text.replace("expected_count: 10", "expected_count: 11"), "utf-8")
    assert rf.main(["--targets", str(targets_file), "--out-root", str(tmp_path)]) == 0
    assert "PERINGATAN" in capsys.readouterr().out


# ---------------------------------------------------------------- aturan akses


@pytest.mark.parametrize(
    "path",
    [
        "/datasets/opendata/contoh/view",
        "/datasets/contoh/view",
        "/view",
        "/history_datasets",
        "/datasets/a/b",
        "/bigdata/a",
        "/organisasi/1",
    ],
)
def test_check_path_rejects(path: str) -> None:
    with pytest.raises(rf.AccessRuleViolation):
        rf.check_path(path)


@pytest.mark.parametrize(
    "path", ["/datasets", "/datasets/contoh", "/bigdata/s/t", "/organisasi", "/topik"]
)
def test_check_path_allows(path: str) -> None:
    rf.check_path(path)


def test_view_rejected_before_any_request() -> None:
    recorder, _, client = _recorder()
    with respx.mock(assert_all_called=False) as router, client:
        route = router.get(url__regex=r".*").mock(return_value=_ok({}))
        with pytest.raises(rf.AccessRuleViolation):
            recorder.get("/datasets/opendata/contoh/view")
    assert route.call_count == 0 and recorder.sent == 0


def test_targets_with_view_endpoint_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "t.yaml"
    bad.write_text("extra_endpoints: [/datasets/opendata/x/view]\n", encoding="utf-8")
    with pytest.raises(rf.AccessRuleViolation):
        rf.load_targets(bad)


def test_recorder_limits_cannot_be_loosened() -> None:
    client = httpx.Client(base_url=BASE)
    with pytest.raises(rf.ConfigProblem):
        rf.Recorder(client, budget=61)
    with pytest.raises(rf.ConfigProblem):
        rf.Recorder(client, delay_s=0.1)
    client.close()


@pytest.mark.parametrize("ua", ["mcp-bogor/0.1 (+<kontak-tim>)", "  "])
def test_placeholder_user_agent_refused(ua: str, tmp_path: Path) -> None:
    with pytest.raises(rf.ConfigProblem, match="MCP_USER_AGENT"):
        rf.execute(
            rf.load_targets(),
            [],
            base_url=BASE,
            user_agent=ua,
            out_root=tmp_path,
            report_path=tmp_path / "r.md",
            today=date(2026, 11, 10),
        )
    assert not (tmp_path / "2026-11-10").exists()


def test_existing_date_folder_not_overwritten(tmp_path: Path) -> None:
    (tmp_path / "2026-11-10").mkdir()
    (tmp_path / "2026-11-10" / "lama.json").write_text("{}", encoding="utf-8")
    with pytest.raises(rf.ConfigProblem, match="sudah ada"):
        rf.execute(
            rf.load_targets(),
            [],
            base_url=BASE,
            user_agent=UA,
            out_root=tmp_path,
            report_path=tmp_path / "r.md",
            today=date(2026, 11, 10),
        )
    assert (tmp_path / "2026-11-10" / "lama.json").read_text(encoding="utf-8") == "{}"


def test_execute_cli_reports_refusal(
    settings_env: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code = rf.main(["--execute", "--out-root", str(tmp_path), "--report", str(tmp_path / "r.md")])
    assert code == 2 and "DITOLAK" in capsys.readouterr().out


# ---------------------------------------------------------------- anggaran, jeda, 429, 5xx


def test_budget_stops_before_exceeding() -> None:
    recorder, sleep, client = _recorder(budget=3)
    with respx.mock as router, client:
        route = router.get(f"{BASE}/datasets").mock(return_value=_ok([]))
        for _ in range(3):
            recorder.get("/datasets")
        with pytest.raises(rf.BudgetExhausted):
            recorder.get("/datasets")
    assert route.call_count == 3 and recorder.sent == 3
    assert sleep.calls == [0.3, 0.3]  # jeda antarpermintaan, bukan sebelum yang pertama


def test_429_stops_without_retry() -> None:
    recorder, sleep, client = _recorder()
    with respx.mock as router, client:
        route = router.get(f"{BASE}/datasets").mock(
            return_value=httpx.Response(429, headers={"Retry-After": "30"}, json={"code": 429})
        )
        with pytest.raises(rf.RateLimitedStop, match="Retry-After=30") as info:
            recorder.get("/datasets")
    assert route.call_count == 1 and sleep.calls == []
    assert info.value.exchange["response"]["status"] == 429


def test_5xx_retried_once_then_ok() -> None:
    recorder, sleep, client = _recorder()
    with respx.mock as router, client:
        route = router.get(f"{BASE}/datasets").mock(
            side_effect=[httpx.Response(503, text="down"), _ok([])]
        )
        exchange = recorder.get("/datasets")
    assert route.call_count == 2 and exchange["response"]["status"] == 200
    assert sleep.calls == [5.0, 0.3]
    assert recorder.incidents == ["503 pada /datasets; retry sekali"]


def test_5xx_twice_stops() -> None:
    recorder, _, client = _recorder()
    with respx.mock as router, client:
        route = router.get(f"{BASE}/datasets").mock(return_value=httpx.Response(502, text="x"))
        with pytest.raises(rf.ServerErrorStop) as info:
            recorder.get("/datasets")
    assert route.call_count == 2
    assert info.value.exchange["response"]["text"] == "x"


def test_5xx_then_429_stops() -> None:
    recorder, _, client = _recorder()
    with respx.mock as router, client:
        router.get(f"{BASE}/datasets").mock(side_effect=[httpx.Response(500), httpx.Response(429)])
        with pytest.raises(rf.RateLimitedStop):
            recorder.get("/datasets")


@pytest.mark.parametrize(
    ("exc", "match"), [(httpx.ReadTimeout("t"), "timeout"), (httpx.ConnectError("c"), "jaringan")]
)
def test_network_errors_stop(exc: Exception, match: str) -> None:
    recorder, _, client = _recorder()
    with respx.mock as router, client:
        router.get(f"{BASE}/datasets").mock(side_effect=exc)
        with pytest.raises(rf.NetworkStop, match=match):
            recorder.get("/datasets")


# ---------------------------------------------------------------- sanitasi


def test_sanitize_removes_account_keys_recursively() -> None:
    raw = {
        "username": "a",
        "data": {
            "slug": "x",
            "cuid": 9,
            "history_draft": [
                {"type": "approve", "username": "b", "notes": "n"},
                {"type": "draft", "user_name": "c"},
            ],
        },
    }
    stats = rf.SanitizeStats()
    clean = rf.sanitize(raw, stats)
    assert clean == {
        "data": {
            "slug": "x",
            "history_draft": [{"type": "approve", "notes": "n"}, {"type": "draft"}],
        }
    }
    assert stats.removed_keys == 4
    assert raw["username"] == "a"  # input tidak diubah


def test_sanitize_redacts_contact_metadata_shapes() -> None:
    stats = rf.SanitizeStats()
    clean = rf.sanitize(
        {
            "metadata": [
                {"key": "Satuan Dataset", "value": "Persen"},
                {"key": "Kontak Produsen", "value": "0251-8321075 / dinas@kotabogor.go.id"},
                {"nama": " kontak produsen ", "nilai": "x", "urutan": 3},
                {"label": "Kontak Produsen", "teks": "y", "lain": "z"},
            ],
            "Kontak Produsen": "langsung",
        },
        stats,
    )
    meta = clean["metadata"]
    assert meta[0] == {"key": "Satuan Dataset", "value": "Persen"}
    assert meta[1] == {"key": "Kontak Produsen", "value": "<redacted>"}
    assert meta[2] == {"nama": " kontak produsen ", "nilai": "<redacted>", "urutan": 3}
    assert meta[3] == {"label": "Kontak Produsen", "teks": "<redacted>", "lain": "<redacted>"}
    assert clean["Kontak Produsen"] == "<redacted>"
    assert stats.redacted_contacts == 5


def test_scan_pii_reports_location_not_value() -> None:
    obj = {
        "a": "hubungi dinas@kotabogor.go.id",
        "b": [{"c": "telp (0251) 8321075"}, {"d": "HP 0812-3456-7890"}],
        "e": "<redacted>",
        "f": "2026-08-31T16:03:02+07:07",
        "g": "9.160.885",
        "h": 81234567890,
    }
    found = rf.scan_pii(obj, "001.json")
    assert [(p.json_path, p.kind) for p in found] == [
        ("$.a", "email"),
        ("$.b[0].c", "telepon"),
        ("$.b[1].d", "telepon"),
    ]
    assert obj["a"] == "hubungi dinas@kotabogor.go.id"  # dilaporkan, tidak dihapus
    assert "kotabogor" not in repr(found)


# ---------------------------------------------------------------- kandidat kartu metrik


def test_find_candidates_token_matching() -> None:
    items = [
        {"slug": "kk", "name": "Persentase KK Miskin di Kota Bogor", "organisasi_name": "Dinsos"},
        {"slug": "km", "name": "Tingkat Kemiskinan di Kota Bogor"},
        {"slug": "p1", "name": "Persentase Penanganan Pengaduan", "organisasi_name": "Inspektorat"},
        {"slug": "p2", "name": "Persentase Penanganan Pengaduan Masyarakat"},
        {"slug": "p3", "name": "Jumlah Pengaduan"},
    ]
    found = rf.find_candidates(items, rf.load_targets()["metric_cards"])
    assert [c["slug"] for c in found["KM-07"]] == ["kk"]
    assert [c["slug"] for c in found["KM-12"]] == ["p1", "p2"]
    assert found["KM-07"][0]["opd"] == "Dinsos"


# ---------------------------------------------------------------- eksekusi penuh (portal palsu)


def _fake_portal(router: respx.MockRouter, *, catalog_pages: int = 2) -> None:
    pages = [
        [{"slug": "a", "name": "Data A"}, {"slug": "b", "name": "Data B"}],
        [
            {
                "slug": "kk",
                "name": "Persentase KK Miskin",
                "history_draft": [
                    {"type": "approve", "cdate": "2026-01-01T00:00:00+07:07", "username": "rahasia"}
                ],
            }
        ],
    ]

    def catalog(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        return _ok(pages[page - 1], pagination={"page": page, "total_page": catalog_pages})

    router.get(f"{BASE}/datasets").mock(side_effect=catalog)
    router.get(f"{BASE}/organisasi").mock(return_value=_ok([{"id": 1}]))
    router.get(f"{BASE}/topik").mock(return_value=_ok([{"id": 2}]))
    for slug in ("a", "kk"):
        router.get(f"{BASE}/datasets/{slug}").mock(
            return_value=_ok(
                {
                    "slug": slug,
                    "schema": "s",
                    "table": f"t_{slug}",
                    "mdate": "2026-01-02T00:00:00",
                    "username": "rahasia",
                    "metadata": [{"key": "Kontak Produsen", "value": "rahasia@contoh.test"}],
                }
            )
        )
        router.get(f"{BASE}/bigdata/s/t_{slug}").mock(
            return_value=_ok(
                [{"id": 1, "tahun": 2024, "nilai": "5.89", "jumlah": "9.160.885"}],
                metadata=["id", "tahun", "nilai", "jumlah"],
            )
        )
    router.get(f"{BASE}/datasets/b").mock(return_value=_ok({"slug": "b"}))
    router.get(f"{BASE}/datasets/salah").mock(
        return_value=httpx.Response(404, json={"code": 404, "message": "x", "data": None})
    )


def _targets() -> dict[str, Any]:
    targets = rf.load_targets()
    targets["invalid_slug"] = "salah"
    targets["catalog"]["per_page"] = 2  # portal palsu: 2 item per halaman
    return targets


def test_execute_happy_path(tmp_path: Path) -> None:
    sleep = FakeSleep()
    report = tmp_path / "REPORT.md"
    with respx.mock(assert_all_mocked=True) as router, httpx.Client(base_url=BASE) as client:
        _fake_portal(router)
        result = rf.execute(
            _targets(),
            ["a", "b"],
            base_url=BASE,
            user_agent=UA,
            out_root=tmp_path / "rec",
            report_path=report,
            today=date(2026, 11, 10),
            client=client,
            sleep=sleep,
            clock=_clock,
        )
    # katalog 2 + organisasi + topik + a(detail, bigdata) + b(detail) + kk(detail, bigdata) + 404
    assert result.stop_reason is None and result.sent == 10
    assert len(sleep.calls) == 9 and set(sleep.calls) == {0.3}
    out_dir = tmp_path / "rec" / "2026-11-10"
    files = sorted(p.name for p in out_dir.glob("0*.json"))
    assert len(files) == 10 and files[0] == "001_katalog-hal-1.json"
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["request_count"] == 10 and manifest["file_count"] == 10
    assert [f["status"] for f in manifest["files"]][-1] == 404
    assert all(len(f["sha256"]) == 64 for f in manifest["files"])
    for name in files:
        text = (out_dir / name).read_text(encoding="utf-8")
        assert "rahasia" not in text and "username" not in text
        assert validate_exchange(json.loads(text)) == []

    body = report.read_text(encoding="utf-8")
    assert "Permintaan terkirim: 10 dari anggaran 60" in body
    assert "`b`: field schema/table tidak ditemukan" in body
    assert "**terkonfirmasi ada**" in body
    assert "`approve`: 1" in body
    assert "`s/t_a`.`nilai`: 1 sel" in body  # "5.89" desimal bertitik teks
    assert "`s/t_a`.`jumlah`: 1 sel" in body  # "9.160.885" terurai
    assert "history_draft[].cdate: +07:07: 1" in body and "mdate: tanpa offset: 2" in body
    assert "### KM-07 (1 kandidat, direkam otomatis)" in body
    assert "### KM-12 (0 kandidat, pilih manual)" in body
    assert "- 404: `/datasets/salah" in body
    assert "rahasia" not in body


def test_execute_stops_on_429_and_still_reports(tmp_path: Path) -> None:
    report = tmp_path / "REPORT.md"
    with respx.mock(assert_all_called=False) as router, httpx.Client(base_url=BASE) as client:
        router.get(f"{BASE}/datasets").mock(
            side_effect=[
                _ok([{"slug": "a", "name": "A"}] * 100, pagination={"total_page": 8}),
                httpx.Response(429, headers={"Retry-After": "60"}, json={"code": 429}),
            ]
        )
        other = router.get(url__regex=rf"{BASE}/(organisasi|topik|bigdata).*")
        result = rf.execute(
            _targets(),
            ["a"],
            base_url=BASE,
            user_agent=UA,
            out_root=tmp_path,
            report_path=report,
            today=date(2026, 11, 10),
            client=client,
            sleep=FakeSleep(),
            clock=_clock,
        )
    assert result.sent == 2 and other.call_count == 0
    assert result.stop_reason and result.stop_reason.startswith("RateLimitedStop")
    manifest = json.loads((tmp_path / "2026-11-10" / "manifest.json").read_text("utf-8"))
    assert [f["status"] for f in manifest["files"]] == [200, 429]
    assert "RateLimitedStop" in report.read_text(encoding="utf-8")


def test_execute_respects_budget_with_long_pagination(tmp_path: Path) -> None:
    targets = _targets()
    targets["catalog"]["max_pages"] = 9
    targets["bigdata"]["max_pages_per_dataset"] = 5
    with respx.mock as router, httpx.Client(base_url=BASE) as client:
        router.get(f"{BASE}/datasets").mock(
            return_value=_ok([{"slug": "x", "name": "X"}] * 100, pagination={"total_page": 99})
        )
        router.get(url__regex=rf"{BASE}/(organisasi|topik)").mock(return_value=_ok([]))
        router.get(url__regex=rf"{BASE}/datasets/.+").mock(
            return_value=_ok({"schema": "s", "table": "t"})
        )
        router.get(f"{BASE}/bigdata/s/t").mock(
            return_value=_ok([{"tahun": 1}] * 1000, pagination={"total_page": 99})
        )
        result = rf.execute(
            targets,
            [f"slug-{i}" for i in range(10)],
            base_url=BASE,
            user_agent=UA,
            out_root=tmp_path,
            report_path=tmp_path / "r.md",
            today=date(2026, 11, 10),
            client=client,
            sleep=FakeSleep(),
            clock=_clock,
        )
        assert len(router.calls) == rf.MAX_REQUESTS
    assert result.sent == rf.MAX_REQUESTS
    assert result.stop_reason and "BudgetExhausted" in result.stop_reason
    assert "masih berlanjut" in (tmp_path / "r.md").read_text(encoding="utf-8")


def test_describe_shapes() -> None:
    assert rf.describe(None) == "null"
    assert rf.describe({"b": 1, "a": 2}) == "object{a, b}"
    assert rf.describe([{"k": 1}]) == "list[object{k}] (n=1)"
    assert rf.describe([]) == "list[?] (n=0)"
    assert rf.describe({str(i): i for i in range(13)}).endswith(", ...}")
    assert rf.describe("x") == "str"
