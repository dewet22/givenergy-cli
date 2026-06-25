"""Opt-in modbus client features, enabled via ``--enable <name>`` / ``GIVENERGY_FEATURES``.

Each known opt-in is one registry entry mapping a CLI-facing name to a ``Client(...)``
keyword argument and the value to pass when it is enabled. The surface stays boolean
(``--enable splice-heal``); the on-value is a registry detail, so an option whose modbus
kwarg takes a value (e.g. ``splice_reject_heal_seconds``) fits without ``name=value`` syntax.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import typer


@dataclass(frozen=True)
class Feature:
    name: str  # CLI-facing, e.g. "splice-heal"
    client_kwarg: str  # Client() keyword, e.g. "splice_reject_heal_seconds"
    help: str  # shown in --enable help and the unknown-feature error
    enabled_value: Any = True  # value passed to client_kwarg when enabled


FEATURES: dict[str, Feature] = {
    f.name: f
    for f in (
        Feature(
            "splice-heal",
            "splice_reject_heal_seconds",
            "Recover legitimate near-100%-SOC LiFePO4 charge-knee surges the battery "
            "splice-guard would otherwise hard-reject (sets a 300s heal window; modbus #299).",
            enabled_value=300.0,
        ),
    )
}


def _parse_features(enable: list[str], env: str | None) -> set[str]:
    """Split each ``--enable`` value and ``GIVENERGY_FEATURES`` on commas, union them,
    strip whitespace, and drop empty segments. Pure string handling — no validation."""
    names: set[str] = set()
    for raw in [*enable, env or ""]:
        for part in raw.split(","):
            cleaned = part.strip()
            if cleaned:
                names.add(cleaned)
    return names


def resolve_features(enable: list[str], env: str | None) -> frozenset[str]:
    """Resolve the enabled feature set from ``--enable`` values and ``GIVENERGY_FEATURES``.

    Raises ``typer.BadParameter`` (listing the available names) on an unknown feature."""
    names = _parse_features(enable, env)
    unknown = sorted(n for n in names if n not in FEATURES)
    if unknown:
        available = ", ".join(sorted(FEATURES)) or "none"
        raise typer.BadParameter(
            f"unknown feature(s): {', '.join(unknown)}. Available: {available}."
        )
    return frozenset(names)


def client_kwargs(features: frozenset[str]) -> dict[str, Any]:
    """Map enabled feature names to the ``Client(...)`` keyword arguments they set."""
    return {
        FEATURES[name].client_kwarg: FEATURES[name].enabled_value for name in features
    }
