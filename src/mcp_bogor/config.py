"""Konfigurasi MCP Kota Bogor; semua nilai dari env (awalan MCP_) atau berkas .env.

Acuan: Dok 3 bagian 6 dan Dok 4 bagian 9. Nilai awal untuk hal [Belum ditentukan]
dicatat di docs/KEPUTUSAN_BUILD.md.
"""

from __future__ import annotations

from datetime import timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

CONTRACT_VERSION = "0.1"

# WIB tetap +07:00. Sengaja tidak memakai zoneinfo: Windows tidak membawa tzdata.
WIB = timezone(timedelta(hours=7), name="WIB")

# Nilai di .env.example; secret ini dianggap belum diisi.
_SECRET_PLACEHOLDER = "ganti-dengan-rahasia-acak"
_SECRET_MIN_BYTES = 32


class ConfigError(RuntimeError):
    """Konfigurasi tidak memenuhi syarat untuk menjalankan suatu komponen."""


# AK-05: User-Agent bawaan berisi placeholder ini dan harus diganti kontak tim.
USER_AGENT_PLACEHOLDER = "<kontak-tim>"


def require_user_agent(user_agent: str) -> str:
    """Satu cek User-Agent untuk adaptor dan skrip perekaman; gagal bila kosong/placeholder."""
    if not user_agent.strip() or USER_AGENT_PLACEHOLDER in user_agent:
        raise ConfigError(
            "MCP_USER_AGENT masih placeholder; isi dengan nama aplikasi dan kontak tim (AK-05)."
        )
    return user_agent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MCP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Sumber data (AK-01..06) ---
    source_base_url: str = "https://opendata.kotabogor.go.id/api"
    user_agent: str = "mcp-bogor/0.1 (+<kontak-tim>)"
    request_delay_s: float = Field(default=0.2, ge=0.2)  # AK-03: minimum 0,2 dtk
    http_timeout_s: float = Field(default=20.0, gt=0)
    max_retries: int = Field(default=5, ge=1, le=5)  # AK-04: maks 5 percobaan (total)
    # KB-24: tunggu (Retry-After/backoff) lebih lama dari ini → langsung gagal, tanpa tidur.
    backoff_max_s: float = Field(default=30.0, gt=0)

    # --- Penyimpanan ---
    db_path: Path = Path("./var/mcp_bogor.db")
    cache_dir: Path = Path("./var/cache")
    # KK-07: kurasi kamus kolom per slug (overrides/<slug>.yaml); tidak dibuat otomatis.
    overrides_dir: Path = Path("./overrides")
    cache_ttl_h: float = Field(default=24, gt=0)
    # KB-32: batas umur cache usang yang masih boleh disajikan saat portal gagal (KS-05).
    cache_stale_max_h: float = Field(default=168, gt=0)
    # KB-32: batas total ukuran cache data (MB) dan batas baris per cache.
    cache_max_mb: float = Field(default=500, gt=0)
    cache_max_rows: int = Field(default=50_000, ge=1)
    catalog_sync_min_interval_h: float = Field(default=24, ge=24)  # AK-06: maks 1x/24 jam
    # KB-28: sinkron failed/incomplete (bukan karena 429) boleh diulang setelah ini.
    catalog_sync_retry_after_h: float = Field(default=6, gt=0)
    # KB-26: jeda antarpermintaan sync_worker (proses terpisah dari server; pembatas laju
    # tidak lintas proses), lebih longgar agar laju gabungan tetap sopan.
    sync_request_delay_s: float = Field(default=0.5, ge=0.2)

    # --- Transport ---
    transport: Literal["stdio", "streamable-http"] = "stdio"
    http_host: str = "127.0.0.1"
    http_port: int = Field(default=8800, ge=1, le=65535)
    fileserver_host: str = "127.0.0.1"
    fileserver_port: int = Field(default=8799, ge=1, le=65535)
    fileserver_public_url: str = "http://localhost:8799"

    # --- data_ref (ADR-008, Dok 4 bagian 6) ---
    dataref_secret: SecretStr = SecretStr("")
    # KB-33: secret lama saat rotasi; hanya untuk verifikasi, tidak pernah untuk menandatangani.
    dataref_secret_previous: SecretStr = SecretStr("")
    dataref_ttl_seconds: int = Field(default=900, gt=0)

    # --- Kontrak dan batas (Dok 4 bagian 9) ---
    tool_prefix: str = "satudata_"
    max_response_tokens: int = Field(default=2000, gt=0)
    search_max_page_size: int = Field(default=20, ge=1)
    top_values: int = Field(default=10, ge=0)
    preview_rows: int = Field(default=5, ge=0, le=5)  # FR-14: maks 5 baris
    description_max_chars: int = Field(default=600, gt=0)
    inline_rows_fallback: bool = False
    force_refresh_min_interval_s: int = Field(default=300, ge=0)  # per slug+parameter

    # --- Mutu data (Dok 2 QC-02, KB-18) ---
    qc_ratio: float = Field(default=50.0, gt=1)

    # --- Log ---
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("source_base_url", "fileserver_public_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("db_path", "cache_dir", "overrides_dir")
    @classmethod
    def _resolve_path(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @model_validator(mode="after")
    def _retry_within_interval(self) -> Settings:
        if self.catalog_sync_retry_after_h > self.catalog_sync_min_interval_h:
            raise ValueError(
                "MCP_CATALOG_SYNC_RETRY_AFTER_H tidak boleh melebihi "
                "MCP_CATALOG_SYNC_MIN_INTERVAL_H"
            )
        return self

    @model_validator(mode="after")
    def _stale_after_ttl(self) -> Settings:
        if self.cache_stale_max_h < self.cache_ttl_h:
            raise ValueError("MCP_CACHE_STALE_MAX_H tidak boleh lebih kecil dari MCP_CACHE_TTL_H")
        return self

    @model_validator(mode="after")
    def _ensure_dirs(self) -> Settings:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        return self

    def require_dataref_secret(self) -> bytes:
        """Kunci HMAC data_ref; gagal bila kosong, placeholder, atau terlalu pendek.

        Dipanggil saat file server atau penanda tangan data_ref dibuat, bukan saat
        konfigurasi dimuat, agar mode stdio untuk pengembangan tetap bisa jalan.
        """
        return _check_secret(self.dataref_secret.get_secret_value(), "MCP_DATAREF_SECRET")

    def dataref_previous_secret(self) -> bytes | None:
        """Secret lama untuk verifikasi saja (KB-33); None bila kosong. Syarat KB-06 sama."""
        raw = self.dataref_secret_previous.get_secret_value()
        if not raw:
            return None
        return _check_secret(raw, "MCP_DATAREF_SECRET_PREVIOUS")


def _check_secret(raw: str, name: str) -> bytes:
    """KB-06: wajib terisi, bukan placeholder, minimal 32 byte."""
    if not raw or raw == _SECRET_PLACEHOLDER:
        raise ConfigError(f"{name} belum diisi.")
    key = raw.encode("utf-8")
    if len(key) < _SECRET_MIN_BYTES:
        raise ConfigError(f"{name} minimal {_SECRET_MIN_BYTES} byte.")
    return key


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Settings tunggal untuk proses; tes memakai Settings(_env_file=None) langsung."""
    return Settings()
