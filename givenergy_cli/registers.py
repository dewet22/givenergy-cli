from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from enum import Enum
from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from givenergy_modbus.client.client import Client
from givenergy_modbus.exceptions import RefreshFailed, RefreshPartiallySucceeded
from givenergy_modbus.model.register import Converter
from givenergy_modbus.model.battery import Battery, BatteryRegisterGetter
from givenergy_modbus.model.ems import Ems
from givenergy_modbus.model.gateway import GatewayV1, GatewayV2
from givenergy_modbus.model.hv_bcu import HvStack
from givenergy_modbus.model.inverter import SinglePhaseInverter
from givenergy_modbus.model.inverter_threephase import ThreePhaseInverter
from givenergy_modbus.model.meter import Meter
from givenergy_modbus.model.plant import Plant, PlantCapabilities
from givenergy_modbus.model.register import Register
from givenergy_modbus.model.register_cache import RegisterCache
from givenergy_modbus.pdu import ReadHoldingRegistersRequest, ReadInputRegistersRequest


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


def _open_private(path: Path):
    """Open *path* for writing with owner-only permissions (rw-------).

    Export/capture files can contain hardware serials (notably with
    `--no-redact`), and default umask typically leaves them world-readable.
    `fchmod` tightens an existing file too — `O_CREAT`'s mode is ignored when
    the file already exists, so a plain open would inherit the old permissions.
    (`fchmod` is POSIX-only; on Windows we fall back to the `O_CREAT` mode.)
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        return os.fdopen(fd, "w")
    except Exception:  # noqa: BLE001 — don't leak the fd if fchmod/fdopen fails
        os.close(fd)
        raise


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
        except RefreshPartiallySucceeded as exc:
            failed = ", ".join(
                f"0x{f.device_address:02x} {f.request_type}({f.base_register})"
                for f in exc.failures
            )
            error = f"partial refresh — {len(exc.failures)} read(s) failed: {failed}"
        except RefreshFailed:
            error = "all register reads failed — inverter may be offline (returning partial data)"
        except Exception as exc:  # noqa: BLE001
            error = f"refresh failed: {exc!r} (returning partial data)"
    finally:
        await client.close()
    return client.plant, error


def export_plant(host: str, port: int, output: Path, *, redact: bool = True) -> None:
    console = Console()
    console.print(f"Connecting to [bold]{host}:{port}[/bold]…")
    with _silence_shutdown_noise():
        plant, error = asyncio.run(_capture(host, port))
    if error:
        # Error strings can embed exception reprs wrapping network-derived bytes.
        console.print(f"[yellow]Warning:[/yellow] {escape(error)}")
    payload = {
        "inverter_serial_number": (
            Converter.redact_serial(plant.inverter_serial_number)
            if redact
            else plant.inverter_serial_number
        ),
        "data_adapter_serial_number": (
            Converter.redact_serial(plant.data_adapter_serial_number)
            if redact
            else plant.data_adapter_serial_number
        ),
        "capabilities": plant.capabilities.to_dict() if plant.capabilities else None,
        "register_caches": {
            f"0x{addr:02x}": _serialise_cache(
                cache.redact_serials() if redact else cache
            )
            for addr, cache in plant.register_caches.items()
            if cache
        },
    }
    with _open_private(output) as f:
        f.write(json.dumps(payload, indent=2, sort_keys=True))
    populated = {addr: c for addr, c in plant.register_caches.items() if c}
    total = sum(len(c) for c in populated.values())
    caps_note = (
        f" · detected [bold]{plant.capabilities.device_type}[/bold]"
        if plant.capabilities
        else ""
    )
    redact_note = " · serials redacted" if redact else ""
    console.print(
        f"Wrote [bold]{total}[/bold] registers across "
        f"[bold]{len(populated)}[/bold] device address(es){caps_note}{redact_note} "
        f"to [cyan]{output}[/cyan]"
    )


# An honest export is tens of KB; these files arrive as bug-report attachments,
# so refuse anything implausibly large before reading it into memory.
_MAX_IMPORT_BYTES = 8 * 1024 * 1024


def check_import_size(path: Path) -> None:
    """Raise ValueError if *path* is too large to be a plausible export/capture."""
    size = path.stat().st_size
    if size > _MAX_IMPORT_BYTES:
        raise ValueError(
            f"{path} is {size:,} bytes — too large to be a plausible "
            f"export/capture (limit {_MAX_IMPORT_BYTES // (1024 * 1024)} MB)"
        )


def load_plant(path: Path) -> Plant:
    check_import_size(path)
    try:
        data = json.loads(path.read_text())
        caches = {
            int(addr, 16): _deserialise_cache(cache_data)
            for addr, cache_data in data["register_caches"].items()
        }
        caps_data = data.get("capabilities")
        capabilities = PlantCapabilities.from_dict(caps_data) if caps_data else None
        return Plant(
            register_caches=caches,
            capabilities=capabilities,
            inverter_serial_number=data["inverter_serial_number"],
            data_adapter_serial_number=data["data_adapter_serial_number"],
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{path} is not a valid plant export: {exc}") from exc


type _DecodableModel = (
    SinglePhaseInverter
    | ThreePhaseInverter
    | Battery
    | Meter
    | Ems
    | GatewayV1
    | GatewayV2
)


def _model_table(title: str, model: _DecodableModel) -> Table:
    table = Table(title=title, show_header=True, header_style="bold magenta")
    table.add_column("Field", style="cyan", no_wrap=True)
    table.add_column("Value", overflow="fold", max_width=40)
    for field, value in model.model_dump().items():
        if value is None or value == "":
            continue
        # Values decode from device-controlled register bytes — escape so a
        # crafted string can't smuggle Rich markup into the rendered table.
        table.add_row(field, escape(str(value)))
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


def _lv_battery_addresses(plant: Plant) -> list[int]:
    """Addresses of LV battery devices to decode. Prefer the detected list from
    `plant.capabilities`; for older exports without it (or HV plants), fall back
    to scanning the inverter and any 0x33–0x37 slots that returned data."""
    caps = plant.capabilities
    if caps and not caps.is_hv:
        return list(caps.lv_battery_addresses)
    if caps and caps.is_hv:
        return []
    return [
        addr for addr in sorted(plant.register_caches.keys()) if 0x32 <= addr <= 0x37
    ]


def _decode_batteries(plant: Plant) -> list[tuple[int, int, Battery | str]]:
    # Each entry is (slot_index, device_addr, Battery or error message).
    # Battery #1 shares device 0x32 with the inverter (it uses IR 60-119 there);
    # battery #2 lives at 0x33, and so on. We deliberately don't use
    # plant.batteries because the library's exception handler doesn't catch
    # ValueError from enum decoding — see `_decode_battery` for the workaround.
    results: list[tuple[int, int, Battery | str]] = []
    for slot, addr in enumerate(_lv_battery_addresses(plant), start=1):
        cache = plant.register_caches.get(addr)
        if cache is None:
            continue
        try:
            battery = _decode_battery(cache)
        except Exception as exc:  # noqa: BLE001
            results.append((slot, addr, f"decode failed: {exc}"))
            continue
        results.append((slot, addr, battery))
    return results


def _print_capabilities(console: Console, caps: PlantCapabilities) -> None:
    console.print(f"Device type:     [bold]{caps.device_type}[/bold]")
    console.print(f"Inverter addr:   [bold]0x{caps.inverter_address:02x}[/bold]")
    if caps.meter_addresses:
        addrs = ", ".join(f"0x{a:02x}" for a in caps.meter_addresses)
        console.print(f"Meters:          [bold]{addrs}[/bold]")
    if caps.lv_battery_addresses:
        addrs = ", ".join(f"0x{a:02x}" for a in caps.lv_battery_addresses)
        console.print(f"LV batteries:    [bold]{addrs}[/bold]")
    if caps.bcu_stacks:
        stacks = ", ".join(f"0x{0x70 + o:02x}×{n}" for o, n in caps.bcu_stacks)
        console.print(f"HV stacks:       [bold]{stacks}[/bold]")
    flags = [
        name
        for name, on in (
            ("three-phase", caps.is_three_phase),
            ("extended slots", caps.has_extended_slots),
            ("EMS", caps.is_ems),
            ("gateway", caps.is_gateway),
        )
        if on
    ]
    if flags:
        console.print(f"Profile:         [bold]{', '.join(flags)}[/bold]")


def _inverter_title(plant: Plant) -> str:
    return (
        "Three-phase Inverter"
        if isinstance(plant.inverter, ThreePhaseInverter)
        else "Inverter"
    )


def show_plant(plant: Plant) -> None:
    console = Console()
    decoded = _decode_batteries(plant)
    valid_count = sum(
        1 for _, _, b in decoded if isinstance(b, Battery) and b.is_valid()
    )
    console.rule("[bold green]Plant Identity[/bold green]")
    console.print(
        f"Inverter serial: [bold]{escape(str(plant.inverter_serial_number))}[/bold]"
    )
    console.print(
        f"Adapter serial:  [bold]{escape(str(plant.data_adapter_serial_number))}[/bold]"
    )
    if plant.capabilities:
        _print_capabilities(console, plant.capabilities)
    else:
        console.print("[dim]Capabilities:    not captured (legacy export)[/dim]")
    console.print(
        f"Battery slots:   [bold]{len(decoded)}[/bold] "
        f"(valid: [bold green]{valid_count}[/bold green])"
    )

    inverter_title = _inverter_title(plant)
    console.rule(f"[bold green]{inverter_title}[/bold green]")
    console.print(_model_table(inverter_title, plant.inverter))

    if (ems := plant.ems) is not None:
        console.rule("[bold green]EMS[/bold green]")
        console.print(_model_table("EMS", ems))

    if (gateway := plant.gateway) is not None:
        kind = "GatewayV2" if isinstance(gateway, GatewayV2) else "GatewayV1"
        console.rule(f"[bold green]Gateway ({kind})[/bold green]")
        console.print(_model_table(kind, gateway))

    if meters := plant.meters:
        console.rule("[bold green]Meters[/bold green]")
        for addr, meter in sorted(meters.items()):
            console.print(_model_table(f"Meter 0x{addr:02x}", meter))

    if stacks := plant.hv_stacks:
        console.rule("[bold green]HV Battery Stacks[/bold green]")
        for stack in stacks:
            _print_hv_stack(console, stack)

    if decoded:
        console.rule("[bold green]LV Batteries[/bold green]")
        for slot, addr, item in decoded:
            if isinstance(item, Battery):
                title = (
                    f"Battery #{slot} (device 0x{addr:02x}, valid={item.is_valid()})"
                )
                console.print(_model_table(title, item))
            else:
                console.print(
                    f"[yellow]Battery #{slot} (device 0x{addr:02x}): {escape(item)}[/yellow]"
                )

    console.rule("[bold green]Register Dump (debug)[/bold green]")
    for addr in sorted(plant.register_caches.keys()):
        cache = plant.register_caches[addr]
        if not cache:
            continue
        console.print(_register_table(addr, cache))


def _print_hv_stack(console: Console, stack: HvStack) -> None:
    console.print(_model_table(f"BCU 0x{stack.device_address:02x}", stack.bcu))
    for i, bmu in enumerate(stack.bmus):
        console.print(
            _model_table(f"BMU #{i + 1} (under 0x{stack.device_address:02x})", bmu)
        )


def _render_probe_compact(
    reg_label: str,
    device_address: int,
    host: str,
    port: int,
    chunks: list[tuple[int, list[int]]],
) -> str:
    """Render probe results as a compact, copy-paste-friendly hex dump (no table).

    A leading ``#`` comment line carries the device/host context, then one line
    per responding chunk in the form ``LABEL(base,count): <hex>`` — each register
    is four hex chars, concatenated, so a 60-register chunk is 240 chars wide.
    """
    header = f"# {reg_label} probe @ device 0x{device_address:02x} on {host}:{port}"
    lines = [
        f"{reg_label}({base},{len(values)}): " + "".join(f"{v:04x}" for v in values)
        for base, values in chunks
    ]
    return "\n".join([header, *lines])


def probe_registers(
    host: str,
    port: int,
    register_type: str,
    device_address: int,
    base: int,
    count: int,
    compact: bool = False,
) -> None:
    """Issue a raw holding or input register read and print the results.

    Issues one or more sequential requests of up to 60 registers each.
    A timeout or exception response is reported per-request rather than
    aborting the whole probe. With ``compact`` set, results print as plain
    one-line-per-register text instead of a table.
    """
    with _silence_shutdown_noise():
        asyncio.run(
            _probe(host, port, register_type, device_address, base, count, compact)
        )


async def _probe(
    host: str,
    port: int,
    register_type: str,
    device_address: int,
    base: int,
    count: int,
    compact: bool = False,
) -> None:
    console = Console()
    reg_label = "HR" if register_type == "hr" else "IR"
    console.print(
        f"Probing [bold]{reg_label}({base}..{base + count - 1})[/bold] "
        f"at device [bold]0x{device_address:02x}[/bold] "
        f"on [bold]{host}:{port}[/bold]…"
    )

    client = Client(host=host, port=port)
    try:
        await client.connect()
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Connection failed:[/red] {escape(str(exc))}")
        return

    # Issue requests in chunks of at most 60 registers.
    chunks: list[tuple[int, list[int]]] = []
    offset = 0
    any_response = False
    try:
        while offset < count:
            chunk = min(60, count - offset)
            chunk_base = base + offset
            RequestClass = (
                ReadHoldingRegistersRequest
                if register_type == "hr"
                else ReadInputRegistersRequest
            )
            request = RequestClass(
                base_register=chunk_base,
                register_count=chunk,
                device_address=device_address,
            )
            try:
                response = await client.send_request_and_await_response(
                    request, timeout=3.0, retries=1
                )
                chunks.append((chunk_base, list(response.register_values)))
                any_response = True
            except TimeoutError:
                console.print(
                    f"[yellow]  {reg_label}({chunk_base}..{chunk_base + chunk - 1}): "
                    f"timed out — no response[/yellow]"
                )
            except Exception as exc:  # noqa: BLE001
                console.print(
                    f"[red]  {reg_label}({chunk_base}..{chunk_base + chunk - 1}): "
                    f"error — {escape(str(exc))}[/red]"
                )
            offset += chunk
    finally:
        await client.close()

    if not any_response:
        console.print(
            "[yellow]No registers responded. The block may not exist on this device.[/yellow]"
        )
    elif compact:
        # soft_wrap keeps each chunk on a single logical line: without it Rich
        # word-wraps the space-less hex to the console width, folding the dump
        # and splitting the label from its data — defeating the copy-paste point.
        console.print(
            _render_probe_compact(reg_label, device_address, host, port, chunks),
            markup=False,
            highlight=False,
            soft_wrap=True,
        )
    else:
        table = Table(
            title=f"{reg_label} probe @ device 0x{device_address:02x}",
            show_header=True,
            header_style="bold yellow",
        )
        table.add_column("Register", style="cyan", no_wrap=True)
        table.add_column("Decimal", justify="right")
        table.add_column("Hex", justify="right", style="dim")
        for base, values in chunks:
            for i, val in enumerate(values):
                table.add_row(f"{reg_label}({base + i})", str(val), f"0x{val:04x}")
        console.print(table)
