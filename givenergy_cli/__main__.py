from enum import Enum
from pathlib import Path

import typer

from givenergy_cli.app import GivEnergyApp
from givenergy_cli.capture import capture_frames
from givenergy_cli.registers import export_plant, load_plant, show_plant


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


if __name__ == "__main__":
    app()
