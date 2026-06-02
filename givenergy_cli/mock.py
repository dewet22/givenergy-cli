from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from rich.console import Console

from givenergy_modbus.testing.mock_plant import MockPlant


async def _serve(captures: list[Path], bind: str, port: int) -> None:
    console = Console()
    mock = MockPlant.from_capture(*captures)
    if not mock.devices:
        console.print(
            "[yellow]Warning:[/yellow] no devices seeded — the capture(s) "
            "contained no decodable rx frames."
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
    captures: list[Path], bind: str, port: int, log_level: str = "INFO"
) -> None:
    logging.basicConfig(level=log_level)
    try:
        asyncio.run(_serve(captures, bind, port))
    except KeyboardInterrupt:
        Console().print("\nMock plant stopped.")
    except OSError as exc:
        Console().print(f"\n[red]Error:[/red] Failed to start mock server: {exc}")
        raise SystemExit(1)
