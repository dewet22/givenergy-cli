from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import yaml
from givenergy_modbus.model.register import HR, IR, MR, Register
from givenergy_modbus.testing.mock_plant import MockPlant
from rich.console import Console

_BANKS: dict[str, type[Register]] = {"HR": HR, "IR": IR, "MR": MR}


def _to_int(value: object) -> int:
    """Coerce a spec/sentinel scalar to an int — accepts 0x-hex or decimal
    strings and plain ints, but not bools or floats (registers are uint16)."""
    if isinstance(value, bool):
        raise ValueError(f"expected an integer, got bool {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)
    raise ValueError(f"expected an integer, got {type(value).__name__} {value!r}")


def _bank(token: str) -> type[Register]:
    try:
        return _BANKS[token.strip().upper()]
    except KeyError:
        raise ValueError(
            f"unknown register bank {token!r}; expected one of HR, IR, MR"
        ) from None


def _check_device(dev: int) -> int:
    if not 0 <= dev <= 0xFF:
        raise ValueError(f"device address must be 0..255 (0xff); got {dev}")
    return dev


def _check_register(addr: int) -> int:
    if not 0 <= addr <= 0xFFFF:
        raise ValueError(f"register address must be 0..65535 (0xffff); got {addr}")
    return addr


def _parse_sentinel(text: str) -> tuple[int, type[Register], range]:
    """Parse a ``device:bank:start-end`` sentinel spec into
    ``(device_address, register_class, range(start, end + 1))``."""
    parts = text.split(":")
    if len(parts) != 3:
        raise ValueError(
            f"sentinel {text!r} must be <device>:<bank>:<start>-<end> "
            "(e.g. 0x11:HR:0-119)"
        )
    dev_s, bank_s, span_s = parts
    start_s, sep, end_s = span_s.partition("-")
    if not sep:
        raise ValueError(f"sentinel range {span_s!r} must be 'start-end'")
    start = _check_register(_to_int(start_s))
    end = _check_register(_to_int(end_s))
    if end < start:
        raise ValueError(f"sentinel range end {end} is before start {start}")
    return _check_device(_to_int(dev_s)), _bank(bank_s), range(start, end + 1)


def _parse_spec_file(
    path: Path,
) -> dict[int, dict[tuple[type[Register], int], list[int]]]:
    """Parse a JSON/YAML register spec into MockPlant.from_spec's dict shape.

    Format: ``{device: {"BANK:base": [values]}}`` — device as int or 0x-hex
    string, BANK one of HR/IR/MR, values plain integers. JSON is valid YAML, so
    one loader covers both formats.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"could not parse spec file as JSON/YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("spec must be a mapping of device -> {'BANK:base': [values]}")
    spec: dict[int, dict[tuple[type[Register], int], list[int]]] = {}
    for dev_key, banks in raw.items():
        if not isinstance(banks, dict):
            raise ValueError(
                f"device {dev_key!r} must map to {{'BANK:base': [values]}}"
            )
        dev = _check_device(_to_int(dev_key))
        for bank_key, values in banks.items():
            bank_s, sep, base_s = str(bank_key).partition(":")
            if not sep:
                raise ValueError(f"register key {bank_key!r} must be 'BANK:base'")
            if not isinstance(values, list):
                raise ValueError(f"values for {bank_key!r} must be a list of integers")
            base = _check_register(_to_int(base_s))
            # Register values are left to from_spec's verify=True (rejects non-uint16).
            spec.setdefault(dev, {})[(_bank(bank_s), base)] = [
                _to_int(v) for v in values
            ]
    return spec


def _build_mock(
    captures: list[Path],
    spec: dict[int, dict[tuple[type[Register], int], list[int]]] | None,
    sentinels: list[tuple[int, type[Register], range]] | None,
    offset: int,
) -> MockPlant:
    if spec is not None:
        return MockPlant.from_spec(spec)
    if sentinels:
        return MockPlant.from_sentinels(*captures, spec=sentinels, offset=offset)
    return MockPlant.from_capture(*captures)


async def _serve(mock: MockPlant, bind: str, port: int) -> None:
    console = Console()
    if not mock.devices:
        console.print(
            "[yellow]Warning:[/yellow] no devices seeded — check the capture/spec input."
        )
    try:
        host, bound_port = await mock.start(bind, port)
        console.print(
            f"Mock plant listening on [bold]{host}:{bound_port}[/bold] "
            f"({len(mock.devices)} device(s) seeded). Press Ctrl+C to stop."
        )
        await mock.serve_forever()
    finally:
        await mock.aclose()


def serve_mock(
    *,
    captures: list[Path],
    bind: str,
    port: int,
    log_level: str = "INFO",
    spec: dict[int, dict[tuple[type[Register], int], list[int]]] | None = None,
    sentinels: list[tuple[int, type[Register], range]] | None = None,
    offset: int = 0,
) -> None:
    logging.basicConfig(level=log_level)
    try:
        mock = _build_mock(captures, spec, sentinels, offset)
    except ValueError as exc:
        # from_spec's verify=True rejects synthetic state the client would refuse.
        Console().print(f"[red]Error:[/red] {exc}")
        raise SystemExit(1)
    try:
        asyncio.run(_serve(mock, bind, port))
    except KeyboardInterrupt:
        Console().print("\nMock plant stopped.")
    except OSError as exc:
        Console().print(f"\n[red]Error:[/red] Failed to start mock server: {exc}")
        raise SystemExit(1)
