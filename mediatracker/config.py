"""Layered configuration.

Precedence, lowest to highest:
  1. dataclass defaults below
  2. config.toml  (non-secret, committed; parsed with stdlib tomllib)
  3. MEDIATRACKER_* environment variables
  4. CLI flags handled in __main__.py (they call apply_overrides)

Postgres credentials are deliberately NOT part of this object; db.py loads them
separately from a secret_postgre.env kept outside the repo.
"""
from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Config:
    # daemon
    host: str = "127.0.0.1"
    port: int = 55030
    # polling
    poll_interval_hours: float = 12.0
    startup_stagger_seconds: float = 5.0
    # fetch
    request_delay_seconds: float = 2.0
    request_timeout_seconds: float = 30.0
    user_agent: str = "MediaTracker/0.1 (local media-research crawler)"
    respect_robots: bool = True
    # storage
    data_dir: str = "~/.local/share/mediatracker"
    blob_dir: str = ""
    jsonl_dir: str = ""
    # postgres (non-secret parts only)
    pg_dbname: str = "MediaTracker"
    pg_host: str = "127.0.0.1"
    pg_port: int = 5432

    # ----- derived paths -------------------------------------------------
    @property
    def data_path(self) -> Path:
        return Path(self.data_dir).expanduser()

    @property
    def blob_path(self) -> Path:
        return Path(self.blob_dir).expanduser() if self.blob_dir else self.data_path / "blobs"

    @property
    def jsonl_path(self) -> Path:
        return Path(self.jsonl_dir).expanduser() if self.jsonl_dir else self.data_path / "jsonl"


def _default_config_toml() -> Path:
    # config.toml sits at the repo root, one level above this package.
    return Path(__file__).resolve().parent.parent / "config.toml"


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    """Build a Config from defaults <- config.toml <- MEDIATRACKER_* env."""
    cfg = Config()

    toml_path = Path(path) if path else _default_config_toml()
    if toml_path.is_file():
        with toml_path.open("rb") as fh:
            data = tomllib.load(fh)
        cfg = _apply_toml(cfg, data)
    else:
        log.warning("config.toml not found at %s; using built-in defaults", toml_path)

    cfg = _apply_env(cfg)
    return cfg


def _apply_toml(cfg: Config, data: dict) -> Config:
    d = data
    return replace(
        cfg,
        host=d.get("daemon", {}).get("host", cfg.host),
        port=int(d.get("daemon", {}).get("port", cfg.port)),
        poll_interval_hours=float(d.get("polling", {}).get("poll_interval_hours", cfg.poll_interval_hours)),
        startup_stagger_seconds=float(d.get("polling", {}).get("startup_stagger_seconds", cfg.startup_stagger_seconds)),
        request_delay_seconds=float(d.get("fetch", {}).get("request_delay_seconds", cfg.request_delay_seconds)),
        request_timeout_seconds=float(d.get("fetch", {}).get("request_timeout_seconds", cfg.request_timeout_seconds)),
        user_agent=d.get("fetch", {}).get("user_agent", cfg.user_agent),
        respect_robots=bool(d.get("fetch", {}).get("respect_robots", cfg.respect_robots)),
        data_dir=d.get("storage", {}).get("data_dir", cfg.data_dir),
        blob_dir=d.get("storage", {}).get("blob_dir", cfg.blob_dir),
        jsonl_dir=d.get("storage", {}).get("jsonl_dir", cfg.jsonl_dir),
        pg_dbname=d.get("postgres", {}).get("dbname", cfg.pg_dbname),
        pg_host=d.get("postgres", {}).get("host", cfg.pg_host),
        pg_port=int(d.get("postgres", {}).get("port", cfg.pg_port)),
    )


# env var name -> (field, caster)
_ENV_FIELDS: dict[str, tuple[str, type]] = {
    "MEDIATRACKER_HOST": ("host", str),
    "MEDIATRACKER_PORT": ("port", int),
    "MEDIATRACKER_POLL_INTERVAL_HOURS": ("poll_interval_hours", float),
    "MEDIATRACKER_REQUEST_DELAY_SECONDS": ("request_delay_seconds", float),
    "MEDIATRACKER_USER_AGENT": ("user_agent", str),
    "MEDIATRACKER_DATA_DIR": ("data_dir", str),
    "MEDIATRACKER_PG_DBNAME": ("pg_dbname", str),
    "MEDIATRACKER_PG_HOST": ("pg_host", str),
    "MEDIATRACKER_PG_PORT": ("pg_port", int),
}


def _apply_env(cfg: Config) -> Config:
    updates: dict[str, object] = {}
    for env_name, (field_name, caster) in _ENV_FIELDS.items():
        raw = os.environ.get(env_name)
        if raw is not None and raw != "":
            try:
                updates[field_name] = caster(raw)
            except ValueError:
                log.warning("ignoring bad value for %s=%r", env_name, raw)
    return replace(cfg, **updates) if updates else cfg
