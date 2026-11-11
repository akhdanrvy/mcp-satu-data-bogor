"""File server data_ref dan tanda tangan (UT-11, KT-06; Dok 4 bagian 6, ADR-005, ADR-008).

Tanpa jaringan: Starlette TestClient memanggil aplikasi di dalam proses.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from starlette.testclient import TestClient

from mcp_bogor.config import WIB, ConfigError, Settings
from mcp_bogor.fileserver import app as app_mod
from mcp_bogor.fileserver.app import create_app
from mcp_bogor.fileserver.signing import DataRefSigner, sign, verification_keys, verify
from mcp_bogor.storage.cache import DataCache, FetchedData, QueryKey, data_path
from tests.fakes import make_settings

SECRET = "rahasia-aktif-langkah-7-" + "a" * 32
OLD_SECRET = "rahasia-lama-langkah-7-" + "b" * 32
SLUG = "tingkat-kemiskinan-di-kota-bogor-2"
NOW = datetime(2026, 11, 11, 10, 0, tzinfo=WIB)
PAYLOAD: dict[str, Any] = {
    "contract_version": "0.1",
    "slug": SLUG,
    "columns": [
        {"name": "tahun", "role": "dimension", "type": "number", "unit": None},
        {"name": "tingkat_kemiskinan", "role": "measure", "type": "number", "unit": "PERSEN"},
    ],
    "rows": [
        {"tahun": 2024, "tingkat_kemiskinan": 1.0},
        {"tahun": 2025, "tingkat_kemiskinan": 2.0},
    ],
    "provenance": {
        "source_url": "https://opendata.kotabogor.go.id/dataset/x",
        "opd": "Badan Pusat Statistik",
    },
    "officialness": {"status": "resmi"},
    "quality_flags": [],
    "periods_found": [2024, 2025],
    "missing_periods": [],
}


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    values = {
        "dataref_secret": SECRET,
        "fileserver_public_url": "http://127.0.0.1:8799/",
    }
    values.update(overrides)
    return make_settings(tmp_path, **values)


class Env:
    def __init__(self, tmp_path: Path, **overrides: Any) -> None:
        self.settings = _settings(tmp_path, **overrides)
        self.cache = DataCache.from_settings(self.settings, now=lambda: NOW)
        self.entry = self.cache.put(QueryKey.build(SLUG), FetchedData(PAYLOAD))
        self.ref = DataRefSigner(self.settings).make_data_ref(
            self.entry.cache_id, self.entry.row_count, NOW
        )
        self.clock = NOW.timestamp()

    def client(self, **kwargs: Any) -> TestClient:
        app = create_app(self.settings, clock=lambda: self.clock, **kwargs)
        return TestClient(app)

    @property
    def path(self) -> str:
        parts = urlsplit(self.ref.url)
        return f"{parts.path}?{parts.query}"

    def parts(self) -> tuple[str, str, str]:
        parts = urlsplit(self.ref.url)
        query = parse_qs(parts.query)
        return parts.path.rsplit("/", 1)[1], query["exp"][0], query["sig"][0]


# ---------------------------------------------------------------- signing


def test_data_ref_shape(tmp_path: Path) -> None:
    env = Env(tmp_path)
    cache_id, exp, sig = env.parts()
    assert env.ref.url == f"http://127.0.0.1:8799/d/{cache_id}?exp={exp}&sig={sig}"
    assert int(exp) == int(NOW.timestamp()) + 900
    assert env.ref.expires_at == NOW + timedelta(seconds=900)
    assert env.ref.row_count == 2 and env.ref.format == "json"
    assert len(sig) == 43 and "=" not in sig and SLUG not in env.ref.url
    assert sig == sign(SECRET.encode(), cache_id, exp)
    with pytest.raises(ValueError):
        DataRefSigner(env.settings).make_data_ref("bukan-id", 1, NOW)


def test_verify_rejects_malformed_input() -> None:
    key = SECRET.encode()
    cache_id = "a" * 26
    good = sign(key, cache_id, 100)
    assert verify([key], cache_id, "100", good)
    assert not verify([key], cache_id, "0100", good)  # exp persis seperti ditandatangani
    assert not verify([key], cache_id, "1e3", good)
    assert not verify([key], "A" * 26, "100", good)
    assert not verify([key], cache_id, "100", good[:-1] + "é")
    assert not verify([key], cache_id, "100", good + "=")
    assert not verify([], cache_id, "100", good)


def test_signer_and_keys_require_valid_secret(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        DataRefSigner(make_settings(tmp_path))
    with pytest.raises(ConfigError):
        DataRefSigner(_settings(tmp_path, dataref_secret="ganti-dengan-rahasia-acak"))
    with pytest.raises(ConfigError):
        verification_keys(_settings(tmp_path, dataref_secret_previous="pendek"))
    same = _settings(tmp_path, dataref_secret_previous=SECRET)
    assert verification_keys(same) == [SECRET.encode()]  # duplikat tidak dihitung dua kali


def test_app_refuses_to_start_without_valid_secret(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        create_app(make_settings(tmp_path))
    with pytest.raises(ConfigError):
        create_app(_settings(tmp_path, dataref_secret="x" * 31))


# ---------------------------------------------------------------- 200 + KT-06


def test_valid_link_serves_file(tmp_path: Path) -> None:
    env = Env(tmp_path)
    response = env.client().get(env.path)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert not any(h.lower().startswith("access-control-") for h in response.headers)
    body = response.json()
    # KT-06: rows cocok row_count; kolom dan provenance sama dengan yang diserahkan tool
    assert len(body["rows"]) == env.ref.row_count == 2
    assert body["columns"] == PAYLOAD["columns"]
    # KB-34: served_from hanya di amplop tool; retrieved_at diisi cache = retrieved_at versi.
    assert "served_from" not in body["provenance"]
    assert body["provenance"] == {**PAYLOAD["provenance"], "retrieved_at": NOW.isoformat()}
    assert body == {**PAYLOAD, "provenance": body["provenance"]}


def test_head_and_method_rules(tmp_path: Path) -> None:
    env = Env(tmp_path)
    client = env.client()
    head = client.head(env.path)
    assert head.status_code == 200 and head.content == b""
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        assert client.request(method, env.path).status_code == 405, method
    assert client.get("/x").status_code == 404
    assert client.get("/d").status_code == 404


# ---------------------------------------------------------------- 403 / 410 / 404 / 503


def _tamper(
    env: Env, *, cache_id: str | None = None, exp: str | None = None, sig: str | None = None
) -> str:
    real_id, real_exp, real_sig = env.parts()
    return f"/d/{cache_id or real_id}?exp={exp or real_exp}&sig={sig or real_sig}"


def test_tampered_links_are_403(tmp_path: Path) -> None:
    env = Env(tmp_path)
    client = env.client()
    cache_id, exp, sig = env.parts()
    flipped = ("A" if sig[0] != "A" else "B") + sig[1:]
    other_id = env.cache.put(QueryKey.build(SLUG, sort="tahun:asc"), FetchedData(PAYLOAD)).cache_id
    cases = [
        _tamper(env, sig=flipped),
        _tamper(env, exp=str(int(exp) + 1)),
        _tamper(env, cache_id=other_id),
        f"/d/{cache_id}?exp={exp}",
        f"/d/{cache_id}?sig={sig}",
        f"/d/{cache_id}",
        "/d/BUKAN_ID_SAH?exp=1&sig=x",
        _tamper(env, exp="abc"),
    ]
    for path in cases:
        response = client.get(path)
        assert response.status_code == 403, path
        assert response.json() == {"error": "forbidden"}
        assert response.headers["cache-control"] == "no-store"


def test_expired_link_is_410_but_forged_expired_link_is_403(tmp_path: Path) -> None:
    env = Env(tmp_path)
    env.clock = NOW.timestamp() + 899
    assert env.client().get(env.path).status_code == 200
    env.clock = NOW.timestamp() + 900
    assert env.client().get(env.path).status_code == 410
    _, _, sig = env.parts()
    forged = _tamper(env, sig=("A" if sig[0] != "A" else "B") + sig[1:])
    assert env.client().get(forged).status_code == 403  # tanda tangan dicek lebih dulu


def test_valid_link_after_gc_is_404(tmp_path: Path) -> None:
    env = Env(tmp_path)
    data_path(env.settings.cache_dir, env.entry.cache_id).unlink()
    assert env.client().get(env.path).status_code == 404


def test_disk_error_is_503(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = Env(tmp_path)

    def broken(cache_dir: Path, cache_id: str) -> bytes:
        raise PermissionError("disk")

    monkeypatch.setattr(app_mod, "read_data_file", broken)
    response = env.client().get(env.path)
    assert response.status_code == 503 and response.json() == {"error": "unavailable"}


# ---------------------------------------------------------------- rotasi secret (KB-33)


def test_previous_secret_only_verifies(tmp_path: Path) -> None:
    old = Env(tmp_path / "lama", dataref_secret=OLD_SECRET)
    rotated = _settings(
        tmp_path / "lama", dataref_secret=SECRET, dataref_secret_previous=OLD_SECRET
    )
    client = TestClient(create_app(rotated, clock=lambda: old.clock))
    assert client.get(old.path).status_code == 200  # tautan lama tetap diterima
    new_ref = DataRefSigner(rotated).make_data_ref(old.entry.cache_id, 2, NOW)
    assert new_ref.url.split("sig=")[1] == sign(SECRET.encode(), old.entry.cache_id, old.parts()[1])

    cleared = _settings(tmp_path / "lama", dataref_secret=SECRET)
    client = TestClient(create_app(cleared, clock=lambda: old.clock))
    assert client.get(old.path).status_code == 403  # setelah dikosongkan


# ---------------------------------------------------------------- log


def test_log_has_no_sig_exp_or_query(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    env = Env(tmp_path)
    caplog.set_level("INFO", logger="mcp_bogor.fileserver.app")
    client = env.client()
    client.get(env.path)
    client.get("/d/BUKAN_ID_SAH?exp=1&sig=rahasia")
    cache_id, exp, sig = env.parts()
    events = [json.loads(r.getMessage()) for r in caplog.records]
    assert [(e["cache_id"], e["status"]) for e in events] == [(cache_id, 200), (None, 403)]
    assert events[0]["size_bytes"] > 0 and "duration_ms" in events[0]
    assert sig not in caplog.text and exp not in caplog.text
    assert "sig=" not in caplog.text and "exp=" not in caplog.text and "rahasia" not in caplog.text
    assert set(events[0]) == {"event", "method", "cache_id", "status", "size_bytes", "duration_ms"}


def test_reads_from_configured_cache_dir(tmp_path: Path) -> None:
    env = Env(tmp_path)
    elsewhere = tmp_path / "lain"
    assert env.client(cache_dir=elsewhere).get(env.path).status_code == 404
    keys = [OLD_SECRET.encode()]
    assert env.client(keys=keys).get(env.path).status_code == 403
