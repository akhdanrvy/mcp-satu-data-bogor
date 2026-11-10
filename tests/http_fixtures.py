"""Pembaca fixture "rekaman HTTP" untuk tes (sintetis dan rekaman asli).

Bentuk satu berkas:
    {"request": {"method", "path", "params"},
     "response": {"status", "headers", "json"[, "text"]}}      # respons HTTP
    {"request": {...}, "exception": {"type", "message"}}       # galat transport
Path relatif terhadap base URL portal (mis. "/datasets"). Fixture sintetis memuat
"_synthetic": true dan "_keterangan"; rekaman asli memuat "recorded_at".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import respx

SYNTHETIC_HTTP = Path(__file__).parent / "fixtures" / "synthetic" / "http"
EXCEPTIONS: dict[str, type[Exception]] = {
    "ConnectError": httpx.ConnectError,
    "ConnectTimeout": httpx.ConnectTimeout,
    "ReadTimeout": httpx.ReadTimeout,
}


def load_exchange(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_exchange(exchange: dict[str, Any]) -> list[str]:
    """Daftar masalah bentuk; kosong berarti sah."""
    problems: list[str] = []
    request = exchange.get("request")
    if not isinstance(request, dict):
        return ["request bukan objek"]
    if request.get("method") != "GET":
        problems.append("method bukan GET")
    path = request.get("path")
    if not isinstance(path, str) or not path.startswith("/"):
        problems.append("path harus diawali '/'")
    elif "/view" in path:
        problems.append("path memuat /view (AK-02)")
    if not isinstance(request.get("params"), dict):
        problems.append("params bukan objek")

    has_response, has_exception = "response" in exchange, "exception" in exchange
    if has_response == has_exception:
        problems.append("harus punya tepat satu dari response atau exception")
    if has_response:
        response = exchange["response"]
        status = response.get("status") if isinstance(response, dict) else None
        if not isinstance(status, int) or not 100 <= status <= 599:
            problems.append("response.status tidak sah")
        headers = response.get("headers") if isinstance(response, dict) else None
        if not isinstance(headers, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in headers.items()
        ):
            problems.append("response.headers harus objek str→str")
        if not isinstance(response, dict) or "json" not in response:
            problems.append("response.json wajib ada (boleh null)")
    if has_exception:
        exc = exchange["exception"]
        if not isinstance(exc, dict) or exc.get("type") not in EXCEPTIONS:
            problems.append(f"exception.type harus salah satu dari {sorted(EXCEPTIONS)}")
    return problems


def to_side_effect(exchange: dict[str, Any]) -> httpx.Response | Exception:
    """Ubah satu fixture menjadi respons/exception untuk respx."""
    if "exception" in exchange:
        exc = exchange["exception"]
        return EXCEPTIONS[exc["type"]](exc.get("message", ""))
    response = exchange["response"]
    kwargs: dict[str, Any] = {"headers": response["headers"]}
    if response["json"] is not None:
        kwargs["json"] = response["json"]
    else:
        kwargs["text"] = response.get("text", "")
    return httpx.Response(response["status"], **kwargs)


def mock_sequence(
    router: respx.MockRouter, base_url: str, exchanges: list[dict[str, Any]]
) -> respx.Route:
    """Daftarkan urutan respons untuk satu path (mis. 429 lalu 200)."""
    path = exchanges[0]["request"]["path"]
    return router.get(base_url + path).mock(side_effect=[to_side_effect(e) for e in exchanges])
