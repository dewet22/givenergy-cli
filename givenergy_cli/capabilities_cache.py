"""Per-host persistence of PlantCapabilities, so the TUI can paint from a prior
instead of waiting on the slow cold detect() probe at every launch.

The cache is purely an optimisation: anything here is regenerable by a fresh
detect(), so it lives under the platform *cache* dir (safe to delete) and every
operation is best-effort — a missing, corrupt, or unwritable file degrades to a
normal cold start rather than raising into the UI.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import platformdirs
from givenergy_modbus.model.plant import PlantCapabilities

_logger = logging.getLogger(__name__)


def _path(host: str, port: int) -> Path:
    safe_host = host.replace(":", "_").replace("/", "_")
    cache_dir = Path(platformdirs.user_cache_dir("givenergy-cli"))
    return cache_dir / f"capabilities-{safe_host}-{port}.json"


def load(host: str, port: int) -> PlantCapabilities | None:
    """Return the cached capabilities for *host:port*, or None if there's no
    usable prior (missing, unreadable, corrupt, or schema-mismatched)."""
    path = _path(host, port)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return PlantCapabilities.from_dict(data)
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001 — any failure means "no usable prior"
        _logger.debug("ignoring capabilities cache at %s: %r", path, exc)
        return None


def save(host: str, port: int, caps: PlantCapabilities) -> None:
    """Persist *caps* for *host:port*. Best-effort: write failures are logged
    and swallowed (the cache is an optimisation, not state to depend on)."""
    path = _path(host, port)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic replace so a crash mid-write can't leave a truncated file that
        # would then be ignored as corrupt on the next load.
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(caps.to_dict(), indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001 — caching must never break the app
        _logger.debug("could not write capabilities cache to %s: %r", path, exc)
