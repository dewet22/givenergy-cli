from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from rich.console import Console

from givenergy_modbus.client.client import Client

from givenergy_cli.registers import _silence_shutdown_noise


async def _run(host: str, port: int, output: Path, duration: float) -> int:
    client = Client(host=host, port=port)
    count = 0
    try:
        await client.connect()
        with output.open("w") as f:

            def write_line(direction: Literal["rx", "tx"], frame: bytes) -> None:
                nonlocal count
                ts = datetime.now(UTC).isoformat(timespec="microseconds")
                f.write(f"{ts} {direction} {frame.hex()}\n")
                f.flush()
                count += 1

            await client.capture_frames(write_line, duration=duration)
    finally:
        await client.close()
    return count


def capture_frames(host: str, port: int, output: Path, duration: float) -> None:
    console = Console()
    console.print(f"Connecting to [bold]{host}:{port}[/bold]…")
    console.print(f"Capturing redacted frames for {duration:g}s → {output}")
    with _silence_shutdown_noise():
        count = asyncio.run(_run(host, port, output, duration))
    console.print(
        f"Wrote [bold]{count}[/bold] frame(s) — safe to attach to a GitHub issue."
    )
