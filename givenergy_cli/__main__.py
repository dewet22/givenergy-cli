from enum import Enum

import typer

from givenergy_cli.app import GivEnergyApp


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


app = typer.Typer(help="GivEnergy TUI — control your inverter from the terminal.")


@app.command()
def main(
    host: str = typer.Option(envvar="GIVENERGY_HOST"),
    port: int = typer.Option(8899, envvar="GIVENERGY_PORT"),
    refresh_interval: float = typer.Option(15.0, envvar="GIVENERGY_REFRESH_INTERVAL"),
    log_level: LogLevel = typer.Option(LogLevel.INFO, envvar="GIVENERGY_LOG_LEVEL"),
):
    GivEnergyApp(
        host=host,
        port=port,
        refresh_interval=refresh_interval,
        log_level=log_level.value,
    ).run()


if __name__ == "__main__":
    app()
