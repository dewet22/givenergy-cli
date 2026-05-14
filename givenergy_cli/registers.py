from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from enum import Enum
from pathlib import Path

from rich.console import Console
from rich.table import Table

from givenergy_modbus.client.client import Client
from givenergy_modbus.model.battery import Battery, BatteryRegisterGetter
from givenergy_modbus.model.inverter import SinglePhaseInverter
from givenergy_modbus.model.plant import Plant
from givenergy_modbus.model.register import Register
from givenergy_modbus.model.register_cache import RegisterCache


def _battery_enum_register_constraints() -> dict[Register, set[int]]:
    """For each single-register enum-typed field on Battery, capture the set of
    integer values the enum accepts. Built once at import time."""
    constraints: dict[Register, set[int]] = {}
    for defn in BatteryRegisterGetter.REGISTER_LUT.values():
        pc = defn.post_conv
        cls = pc[0] if isinstance(pc, tuple) else pc
        if isinstance(cls, type) and issubclass(cls, Enum) and len(defn.registers) == 1:
            constraints[defn.registers[0]] = {m.value for m in cls}
    return constraints


_BATTERY_ENUM_CONSTRAINTS = _battery_enum_register_constraints()


def _decode_battery(cache: RegisterCache) -> Battery:
    """Decode a Battery, zeroing any enum-typed register whose value isn't a
    valid enum member. The local inverter's modbus implementation occasionally
    hands back garbage (e.g. UsbDevice = 11 on device 0x33) which would otherwise
    abort the whole battery decode."""
    sanitised: RegisterCache | None = None
    for reg, valid in _BATTERY_ENUM_CONSTRAINTS.items():
        if cache.get(reg) not in valid:
            if sanitised is None:
                sanitised = RegisterCache(dict(cache))
            sanitised[reg] = 0
    return Battery.from_register_cache(sanitised or cache)


# CRITICAL log lines emitted by the modbus client's reader/writer tasks when we
# call `client.close()` — expected here, not in the TUI.
_EXPECTED_SHUTDOWN_NOISE = ("reader at EOF", "writer is closing")


class _SuppressShutdownNoise(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(s in msg for s in _EXPECTED_SHUTDOWN_NOISE)


@contextmanager
def _silence_shutdown_noise() -> Iterator[None]:
    logger = logging.getLogger("givenergy_modbus.client.client")
    flt = _SuppressShutdownNoise()
    logger.addFilter(flt)
    try:
        yield
    finally:
        logger.removeFilter(flt)


def _serialise_cache(cache: RegisterCache) -> dict[str, int]:
    return {f"{reg._type}({reg._idx})": value for reg, value in cache.items()}


def _deserialise_cache(data: dict[str, int]) -> RegisterCache:
    return RegisterCache.from_json(json.dumps(data))


async def _capture(host: str, port: int) -> tuple[Plant, str | None]:
    """Connect, detect, load config, refresh; return (plant, error). On partial
    failure we still return whatever the plant captured up to that point."""
    client = Client(host=host, port=port)
    error: str | None = None
    try:
        await client.connect()
        try:
            client.plant.capabilities = await client.detect(timeout=3.0)
            await client.load_config(timeout=3.0, retries=2)
            await client.refresh(timeout=3.0, retries=2)
        except TimeoutError:
            error = "timed out waiting for the inverter (returning partial data)"
        except Exception as exc:  # noqa: BLE001
            error = f"refresh failed: {exc!r} (returning partial data)"
    finally:
        await client.close()
    return client.plant, error


def export_plant(host: str, port: int, output: Path) -> None:
    console = Console()
    console.print(f"Connecting to [bold]{host}:{port}[/bold]…")
    with _silence_shutdown_noise():
        plant, error = asyncio.run(_capture(host, port))
    if error:
        console.print(f"[yellow]Warning:[/yellow] {error}")
    payload = {
        "inverter_serial_number": plant.inverter_serial_number,
        "data_adapter_serial_number": plant.data_adapter_serial_number,
        "register_caches": {
            f"0x{addr:02x}": _serialise_cache(cache)
            for addr, cache in plant.register_caches.items()
        },
    }
    output.write_text(json.dumps(payload, indent=2, sort_keys=True))
    total = sum(len(c) for c in plant.register_caches.values())
    console.print(
        f"Wrote [bold]{total}[/bold] registers across "
        f"[bold]{len(plant.register_caches)}[/bold] device address(es) to [cyan]{output}[/cyan]"
    )


def load_plant(path: Path) -> Plant:
    data = json.loads(path.read_text())
    caches = {
        int(addr, 16): _deserialise_cache(cache_data)
        for addr, cache_data in data["register_caches"].items()
    }
    return Plant(
        register_caches=caches,
        inverter_serial_number=data["inverter_serial_number"],
        data_adapter_serial_number=data["data_adapter_serial_number"],
    )


def _model_table(title: str, model: SinglePhaseInverter | Battery) -> Table:
    table = Table(
        title=title, show_header=True, header_style="bold magenta", expand=True
    )
    table.add_column("Field", style="cyan", no_wrap=True)
    table.add_column("Value", overflow="fold")
    for field, value in model.model_dump().items():
        if value is None or value == "":
            continue
        table.add_row(field, str(value))
    return table


def _register_table(addr: int, cache: RegisterCache) -> Table:
    table = Table(
        title=f"Device 0x{addr:02x} — {len(cache)} registers",
        show_header=True,
        header_style="bold yellow",
    )
    table.add_column("Register", style="cyan")
    table.add_column("Decimal", justify="right")
    table.add_column("Hex", justify="right", style="dim")
    for reg in sorted(cache.keys(), key=lambda r: (r._type, r._idx)):
        value = cache[reg]
        table.add_row(str(reg), str(value), f"0x{value:04x}")
    return table


def _decode_batteries(plant: Plant) -> list[tuple[int, int, Battery | str]]:
    # Each entry is (slot_index, device_addr, Battery or error message).
    # Battery #1 shares device 0x32 with the inverter (it uses IR 60-119 there);
    # battery #2 lives at 0x33, and so on. We deliberately don't use
    # plant.number_batteries / plant.batteries because the library's exception
    # handler doesn't catch ValueError from enum decoding.
    results: list[tuple[int, int, Battery | str]] = []
    for slot, addr in enumerate(sorted(plant.register_caches.keys()), start=1):
        try:
            battery = _decode_battery(plant.register_caches[addr])
        except Exception as exc:  # noqa: BLE001
            results.append((slot, addr, f"decode failed: {exc}"))
            continue
        results.append((slot, addr, battery))
    return results


def show_plant(plant: Plant) -> None:
    console = Console()
    decoded = _decode_batteries(plant)
    valid_count = sum(
        1 for _, _, b in decoded if isinstance(b, Battery) and b.is_valid()
    )
    console.rule("[bold green]Plant Identity[/bold green]")
    console.print(f"Inverter serial: [bold]{plant.inverter_serial_number}[/bold]")
    console.print(f"Adapter serial:  [bold]{plant.data_adapter_serial_number}[/bold]")
    console.print(
        f"Battery slots:   [bold]{len(decoded)}[/bold] "
        f"(valid: [bold green]{valid_count}[/bold green])"
    )

    console.rule("[bold green]Inverter[/bold green]")
    console.print(_model_table("Inverter", plant.inverter))

    if decoded:
        console.rule("[bold green]Batteries[/bold green]")
        for slot, addr, item in decoded:
            if isinstance(item, Battery):
                title = (
                    f"Battery #{slot} (device 0x{addr:02x}, valid={item.is_valid()})"
                )
                console.print(_model_table(title, item))
            else:
                console.print(
                    f"[yellow]Battery #{slot} (device 0x{addr:02x}): {item}[/yellow]"
                )

    console.rule("[bold green]Register Dump (debug)[/bold green]")
    for addr in sorted(plant.register_caches.keys()):
        console.print(_register_table(addr, plant.register_caches[addr]))
