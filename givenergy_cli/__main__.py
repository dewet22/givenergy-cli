from enum import Enum
from pathlib import Path

import typer

from givenergy_cli.app import GivEnergyApp
from givenergy_cli.capture import capture_frames
from givenergy_cli.mock import serve_mock
from givenergy_cli.registers import (
    export_plant,
    load_plant,
    probe_registers,
    show_plant,
)


def _parse_int(value: str | int) -> int:
    """Parse an int accepting decimal or 0x/0o/0b-prefixed (hex/octal/binary) input."""
    if isinstance(value, int):
        return value  # an option default is passed through already parsed
    return int(value, 0)  # base-0 auto-detects the 0x / 0o / 0b prefix


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


app = typer.Typer(help="GivEnergy CLI — control your inverter from the terminal.")


@app.callback()
def root(
    ctx: typer.Context,
    host: str = typer.Option(None, envvar="GIVENERGY_HOST"),
    port: int = typer.Option(8899, envvar="GIVENERGY_PORT"),
) -> None:
    """Global options shared by all subcommands."""
    ctx.obj = {"host": host, "port": port}


def _require_host(ctx: typer.Context) -> str:
    host = ctx.obj["host"]
    if not host:
        raise typer.BadParameter(
            "--host or GIVENERGY_HOST is required for this command."
        )
    return host


@app.command()
def tui(
    ctx: typer.Context,
    refresh_interval: float = typer.Option(15.0, envvar="GIVENERGY_REFRESH_INTERVAL"),
    log_level: LogLevel = typer.Option(LogLevel.INFO, envvar="GIVENERGY_LOG_LEVEL"),
) -> None:
    """Launch the interactive TUI."""
    GivEnergyApp(
        host=_require_host(ctx),
        port=ctx.obj["port"],
        refresh_interval=refresh_interval,
        log_level=log_level.value,
    ).run()


@app.command()
def export(
    ctx: typer.Context,
    output: Path = typer.Option(
        ...,
        "--output",
        "-o",
        help="Path to write the exported register JSON.",
        dir_okay=False,
        writable=True,
    ),
) -> None:
    """Capture a full register dump from the inverter into a portable JSON file."""
    export_plant(
        host=_require_host(ctx),
        port=ctx.obj["port"],
        output=output,
    )


@app.command()
def capture(
    ctx: typer.Context,
    output: Path = typer.Option(
        ...,
        "--output",
        "-o",
        help="Path to write the captured frames log.",
        dir_okay=False,
        writable=True,
    ),
    duration: float = typer.Option(
        60.0,
        "--duration",
        "-d",
        min=0.1,
        help="How long to capture, in seconds.",
    ),
) -> None:
    """Record raw redacted wire frames for a bug report."""
    capture_frames(
        host=_require_host(ctx),
        port=ctx.obj["port"],
        output=output,
        duration=duration,
    )


class RegisterType(str, Enum):
    HR = "hr"
    IR = "ir"


@app.command()
def probe(
    ctx: typer.Context,
    register_type: RegisterType = typer.Option(
        ...,
        "--type",
        "-t",
        help="Register bank to probe: 'hr' (holding) or 'ir' (input).",
    ),
    base: int = typer.Option(
        ...,
        "--base",
        "-b",
        parser=_parse_int,
        help="First register address to read (decimal or 0x-hex).",
    ),
    count: int = typer.Option(
        60,
        "--count",
        "-n",
        min=1,
        help="Number of registers to read (requests are split into chunks of 60).",
    ),
    device_address: int = typer.Option(
        0x11,
        "--device",
        "-d",
        parser=_parse_int,
        help="Modbus device address to target, decimal or 0x-hex "
        "(e.g. 0x11 for inverter, 0x31 for AC).",
    ),
) -> None:
    """Read an arbitrary register range directly from the inverter.

    Issues raw Modbus read requests, bypassing the normal polling. Useful for
    probing undocumented register blocks — e.g. to check whether HR(4080+) holds
    battery energy totals on AC-coupled models.

    Example — probe the HR(4080–4139) block on an AC inverter:

        givenergy-cli --host 192.168.1.x probe --type hr --base 4080 --count 60 --device 0x31
    """
    if not 0 <= device_address <= 0xFF:
        raise typer.BadParameter(
            f"Device address must be between 0 and 255 (0xff); got {device_address}.",
            param_hint="'--device' / '-d'",
        )
    if not 0 <= base <= 0xFFFF:
        raise typer.BadParameter(
            f"Base register must be between 0 and 65535 (0xffff); got {base}.",
            param_hint="'--base' / '-b'",
        )
    if base + count > 0x10000:
        raise typer.BadParameter(
            f"Range base {base} + count {count} exceeds the maximum Modbus register address (0xffff)."
        )
    probe_registers(
        host=_require_host(ctx),
        port=ctx.obj["port"],
        register_type=register_type.value,
        device_address=device_address,
        base=base,
        count=count,
    )


@app.command()
def inspect(
    path: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
        help="Path to a JSON file produced by `export`.",
    ),
) -> None:
    """Reconstruct a plant from an exported JSON file and dump it."""
    plant = load_plant(path)
    show_plant(plant)


@app.command("mock-server")
def mock_server(
    captures: list[Path] = typer.Option(
        ...,
        "--capture",
        "-c",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Capture .log file(s) to seed from (repeatable). Produced by `capture`.",
    ),
    bind: str = typer.Option(
        "127.0.0.1",
        "--bind",
        help="Bind address (use 0.0.0.0 to expose on the LAN).",
    ),
    port: int = typer.Option(8899, "--port", help="Bind port."),
) -> None:
    """Serve a mock GivEnergy plant from recorded captures, for offline testing.

    Replays one or more capture logs as a faithful in-memory plant that answers a
    real client's detect/load_config/refresh sequence — point `tui` or `export` at
    it with no hardware. Seed files come from the `capture` command.

    Example:

        givenergy-cli mock-server --capture plant.log
        givenergy-cli --host 127.0.0.1 tui   # in another terminal
    """
    if not 0 <= port <= 65535:
        raise typer.BadParameter(
            f"Port must be between 0 and 65535; got {port}.",
            param_hint="'--port'",
        )
    serve_mock(captures=captures, bind=bind, port=port)


if __name__ == "__main__":
    app()
