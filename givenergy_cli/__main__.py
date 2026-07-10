import contextlib
import os
from enum import Enum
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape

from givenergy_modbus.model.plant import Plant

from givenergy_cli.app import GivEnergyApp
from givenergy_cli.capture import capture_frames
from givenergy_cli.features import FEATURES, resolve_features
from givenergy_cli.mock import _parse_sentinel, _parse_spec_file, serve_mock
from givenergy_cli.registers import (
    _decode_batteries,
    check_import_size,
    export_plant,
    load_capture,
    probe_registers,
    show_plant,
    snapshot_plant,
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

_ENABLE_HELP = (
    "Enable an opt-in client feature (repeatable; comma-separated, or via "
    "GIVENERGY_FEATURES). "
    + (
        "Available — " + "; ".join(f"{f.name}: {f.help}" for f in FEATURES.values())
        if FEATURES
        else "No opt-in features in this release."
    )
)


@app.callback()
def root(
    ctx: typer.Context,
    host: str = typer.Option(None, envvar="GIVENERGY_HOST"),
    port: int = typer.Option(8899, envvar="GIVENERGY_PORT"),
    enable: list[str] = typer.Option([], "--enable", help=_ENABLE_HELP),
) -> None:
    """Global options shared by all subcommands."""
    features = resolve_features(enable, os.environ.get("GIVENERGY_FEATURES"))
    ctx.obj = {"host": host, "port": port, "features": features}


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
    redetect: bool = typer.Option(
        False,
        "--redetect",
        help="Ignore the cached plant topology and run a full detect on startup "
        "(also refreshes the cache). Use after changing hardware.",
    ),
    allow_writes: bool = typer.Option(
        False,
        "--allow-writes",
        help="Enable the Controls view to send commands to the inverter. Off by "
        "default — without it, Controls is read-only.",
    ),
) -> None:
    """Launch the interactive TUI."""
    GivEnergyApp(
        host=_require_host(ctx),
        port=ctx.obj["port"],
        refresh_interval=refresh_interval,
        log_level=log_level.value,
        redetect=redetect,
        allow_writes=allow_writes,
        features=ctx.obj["features"],
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
    redact: bool = typer.Option(
        True,
        "--redact/--no-redact",
        help="Redact serial numbers for share-safe output (default). Use --no-redact for raw data.",
    ),
) -> None:
    """Capture a full register dump from the inverter into a portable JSON file."""
    export_plant(
        host=_require_host(ctx),
        port=ctx.obj["port"],
        output=output,
        redact=redact,
        features=ctx.obj["features"],
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
        help="Wall-clock seconds of coverage; reconnects don't extend or reset it.",
    ),
    reconnect_after: float = typer.Option(
        60.0,
        "--reconnect-after",
        min=0.0,
        help="Reconnect if no register update arrives for this many seconds "
        "(a quiet stream). 0 disables the quiet check, leaving drop-only reconnect.",
    ),
) -> None:
    """Record raw redacted wire frames for a bug report.

    Reconnects automatically across dropped connections and (unless
    --reconnect-after 0) quiet stretches, writing all frames to a single file
    with a comment marker on each resumption.
    """
    capture_frames(
        host=_require_host(ctx),
        port=ctx.obj["port"],
        output=output,
        duration=duration,
        reconnect_after=reconnect_after,
        features=ctx.obj["features"],
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
    visual: bool = typer.Option(
        False,
        "--visual",
        help="Render the decoded register table instead of the default compact "
        "one-line-per-register text (the compact form is easier to paste into a "
        "bug report).",
    ),
    compact: bool = typer.Option(
        False,
        "--compact",
        "--terse",
        hidden=True,
        help="Deprecated no-op: compact output is now the default; use --visual "
        "for the decoded table.",
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
        compact=not visual,
        features=ctx.obj["features"],
    )


@app.command()
def inspect(
    path: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
        help="An `export` JSON file or a `probe` dump.",
    ),
) -> None:
    """Reconstruct a plant from an export JSON or probe dump and dump it."""
    try:
        plant = load_capture(path)
    except ValueError as exc:
        # These files arrive as bug-report attachments — a malformed or
        # oversized one should produce a clean message, not a traceback.
        raise typer.BadParameter(str(exc), param_hint="PATH") from exc
    show_plant(plant)


def _shell_banner(plant: Plant, source: str) -> str:
    """Plain-text banner summarising the loaded plant and the shell namespace."""
    lines = [f"GivEnergy shell — loaded from {source}"]
    caps = plant.capabilities
    if caps:
        lines.append(f"  device:   {caps.device_type}")
    if plant.inverter_serial_number:
        lines.append(f"  inverter: {plant.inverter_serial_number}")
    n_dev = sum(1 for c in plant.register_caches.values() if c)
    n_reg = sum(len(c) for c in plant.register_caches.values())
    lines.append(f"  loaded:   {n_reg} registers across {n_dev} device address(es)")
    if not caps:
        lines.append(
            "  note:     no capabilities — typed views (plant.inverter, .ems …) "
            "limited; raw `caches` available"
        )
    lines.append("  names:    plant, caches, batteries, show(), console")
    return "\n".join(lines)


def _start_shell(namespace: dict, banner: str) -> None:
    """Drop into IPython if the `[shell]` extra is installed, else the stdlib REPL."""
    print(banner)
    try:
        from IPython import start_ipython
    except ImportError:
        import code

        print(
            "(install the 'shell' extra for an IPython shell: "
            "pip install 'givenergy-cli[shell]')"
        )
        # Opt-in tab completion over the namespace; readline is absent on some
        # platforms (e.g. stock Windows), so degrade quietly if it can't load.
        with contextlib.suppress(ImportError):
            import readline
            import rlcompleter

            readline.set_completer(rlcompleter.Completer(namespace).complete)
            readline.parse_and_bind("tab: complete")
        code.interact(banner="", local=namespace)
    else:
        start_ipython(argv=[], user_ns=namespace)


@app.command()
def shell(
    ctx: typer.Context,
    path: Path | None = typer.Argument(
        None,
        exists=True,
        dir_okay=False,
        readable=True,
        help="An `export` JSON or `probe` dump to load offline. "
        "Omit to snapshot a live inverter via --host.",
    ),
) -> None:
    """Drop into an interactive Python shell with a reconstructed `plant`.

    Loads a plant from a file (export JSON or probe dump), or — with no file —
    takes a one-shot live snapshot via --host. The namespace exposes `plant`,
    `caches`, `batteries`, `show()` and `console`. Uses IPython if installed
    (the `[shell]` extra), otherwise the stdlib REPL.

    Examples:

        givenergy-cli shell plant.json        # offline, from an export
        givenergy-cli --host 192.168.1.x shell  # live snapshot
    """
    console = Console()
    if path is not None:
        try:
            plant = load_capture(path)
        except ValueError as exc:
            raise typer.BadParameter(str(exc), param_hint="PATH") from exc
        source = str(path)
    else:
        host = _require_host(ctx)
        port = ctx.obj["port"]
        console.print(f"Connecting to [bold]{host}:{port}[/bold]…")
        plant, error = snapshot_plant(host, port, features=ctx.obj["features"])
        if error:
            console.print(f"[yellow]Warning:[/yellow] {escape(error)}")
        if not any(plant.register_caches.values()):
            console.print("[red]No data captured — nothing to inspect.[/red]")
            raise typer.Exit(1)
        source = f"{host}:{port} (live)"

    namespace = {
        "plant": plant,
        "caches": plant.register_caches,
        "batteries": _decode_batteries(plant),
        "show": lambda: show_plant(plant),
        "console": console,
    }
    _start_shell(namespace, _shell_banner(plant, source))


@app.command("mock-server")
def mock_server(
    captures: list[Path] = typer.Option(
        [],
        "--capture",
        "-c",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Capture .log file(s) to seed from (repeatable). Produced by `capture`.",
    ),
    spec_file: Path | None = typer.Option(
        None,
        "--spec",
        exists=True,
        dir_okay=False,
        readable=True,
        help="JSON/YAML register spec to build a mock directly (no capture): maps "
        "device to 'HR:base'/'IR:base'/'MR:base' to a list of ints. "
        "Excludes --capture/--sentinel.",
    ),
    sentinels: list[str] = typer.Option(
        [],
        "--sentinel",
        help="Overlay sentinel values on the --capture base (repeatable): "
        "<device>:<bank>:<start>-<end>, e.g. 0x11:HR:0-119.",
    ),
    offset: int = typer.Option(
        0,
        "--offset",
        help="Sentinel value offset (raw = address + offset); used with --sentinel.",
    ),
    bind: str = typer.Option(
        "127.0.0.1",
        "--bind",
        help="Bind address (use 0.0.0.0 to expose on the LAN).",
    ),
    port: int = typer.Option(8899, "--port", help="Bind port."),
    log_level: LogLevel = typer.Option(LogLevel.INFO, envvar="GIVENERGY_LOG_LEVEL"),
) -> None:
    """Serve a mock GivEnergy plant for offline testing.

    Replays a faithful in-memory plant that answers a real client's
    detect/load_config/refresh sequence — point `tui` or `export` at it with no
    hardware. Three seeding modes:

    - `--capture <log>` — replay recorded capture(s) (from the `capture` command).
    - `--spec <file>` — build from a JSON/YAML register spec, no capture needed.
    - `--sentinel <device>:<bank>:<start>-<end>` — overlay sentinel values on a
      `--capture` base (for register identification).

    Example:

        givenergy-cli mock-server --capture plant.log
        givenergy-cli --host 127.0.0.1 tui   # in another terminal
    """
    if spec_file is not None and (captures or sentinels):
        raise typer.BadParameter(
            "--spec builds a mock on its own — don't combine it with --capture or --sentinel.",
            param_hint="'--spec'",
        )
    if sentinels and not captures:
        raise typer.BadParameter(
            "--sentinel overlays onto a --capture base; provide --capture too.",
            param_hint="'--sentinel'",
        )
    if spec_file is None and not captures:
        raise typer.BadParameter(
            "provide a seeding mode: --capture <log> (optionally with --sentinel), or --spec <file>.",
            param_hint="'--capture' / '--spec'",
        )
    if not 0 <= port <= 65535:
        raise typer.BadParameter(
            f"Port must be between 0 and 65535; got {port}.",
            param_hint="'--port'",
        )
    for capture_path in captures:
        try:
            check_import_size(capture_path)
        except ValueError as exc:
            raise typer.BadParameter(str(exc), param_hint="'--capture'") from exc

    spec = None
    if spec_file is not None:
        try:
            check_import_size(spec_file)
            spec = _parse_spec_file(spec_file)
        except ValueError as exc:
            raise typer.BadParameter(str(exc), param_hint="'--spec'") from exc
    parsed_sentinels = None
    if sentinels:
        try:
            parsed_sentinels = [_parse_sentinel(s) for s in sentinels]
        except ValueError as exc:
            raise typer.BadParameter(str(exc), param_hint="'--sentinel'") from exc

    serve_mock(
        captures=captures,
        bind=bind,
        port=port,
        log_level=log_level.value,
        spec=spec,
        sentinels=parsed_sentinels,
        offset=offset,
    )


if __name__ == "__main__":
    app()
