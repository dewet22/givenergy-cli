from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TextIO
from collections.abc import Callable

from rich.console import Console

from givenergy_modbus.client.client import Client
from givenergy_modbus.exceptions import CommunicationError

from givenergy_cli.features import client_kwargs
from givenergy_cli.registers import _open_private, _silence_shutdown_noise

# Tuning knobs — module-level so tests can shrink them.
_POLL_INTERVAL = 1.0  # how often to check liveness while a capture segment runs
_BACKOFF_INITIAL = 1.0  # first reconnect delay after a failed connect
_BACKOFF_CAP = 30.0  # ceiling for the exponential backoff

# Connect failures we retry rather than abort on.
_CONNECT_ERRORS = (OSError, TimeoutError, CommunicationError)

WriteLine = Callable[[Literal["rx", "tx"], bytes], None]


def _idle_seconds(client: Client, since: datetime, now: datetime) -> float:
    """Seconds since the most recent register block was ingested. Falls back to
    *since* (the connect time) when nothing has arrived yet, so a stream that is
    silent from the outset is treated as idle rather than ageless."""
    stamps = client.plant.register_block_updated_at
    newest = max(stamps.values()) if stamps else since
    return (now - newest).total_seconds()


async def _capture_segment(
    client: Client,
    write_line: WriteLine,
    remaining: float,
    reconnect_after: float,
    connected_since: datetime,
) -> str | None:
    """Tee frames for up to *remaining* seconds. Return early with a reason —
    ``"drop"`` (connection lost) or ``"quiet"`` (no register blocks ingested for
    *reconnect_after* seconds, though the socket is up) — or ``None`` if the full
    duration elapsed."""
    cap_task = asyncio.create_task(
        client.capture_frames(write_line, duration=remaining)
    )
    try:
        while True:
            done, _ = await asyncio.wait(
                {cap_task}, timeout=min(_POLL_INTERVAL, max(remaining, 0.0))
            )
            if cap_task in done:
                await cap_task  # surface any exception; duration elapsed normally
                return None
            if not client.connected:
                return "drop"
            if reconnect_after > 0:
                idle = _idle_seconds(client, connected_since, datetime.now(UTC))
                if idle > reconnect_after:
                    return "quiet"
    finally:
        if not cap_task.done():
            cap_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cap_task


def _marker(f: TextIO, message: str) -> None:
    """Write a comment line into the capture output. Comment lines start with
    ``#`` so frame-parsing tooling skips them."""
    ts = datetime.now(UTC).isoformat(timespec="microseconds")
    f.write(f"# {ts} {message}\n")
    f.flush()


async def _run(
    host: str,
    port: int,
    output: Path,
    duration: float,
    reconnect_after: float,
    features: frozenset[str] = frozenset(),
) -> int:
    """Capture redacted frames for *duration* wall-clock seconds, reconnecting
    across drops and (when reconnect_after > 0) quiet stretches. All segments are
    written to a single output file with a comment marker on each resumption."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + duration
    count = 0
    backoff = _BACKOFF_INITIAL
    # (reason, monotonic time the previous segment was interrupted), pending until
    # the next successful reconnect lets us record the gap duration.
    pending: tuple[str, float] | None = None

    with _open_private(output) as f:

        def write_line(direction: Literal["rx", "tx"], frame: bytes) -> None:
            nonlocal count
            ts = datetime.now(UTC).isoformat(timespec="microseconds")
            f.write(f"{ts} {direction} {frame.hex()}\n")
            f.flush()
            count += 1

        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break

            client = Client(host=host, port=port, **client_kwargs(features))
            try:
                await client.connect()
            except _CONNECT_ERRORS:
                # Couldn't (re)establish the connection — back off and retry until
                # the deadline. Recompute the budget first: a slow connect attempt
                # may have eaten into it. Nothing to close on a failed connect.
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(backoff, remaining))
                backoff = min(backoff * 2, _BACKOFF_CAP)
                continue

            # connect() can take up to the connect timeout, so recompute the budget
            # before capturing: the segment length (and the -d wall-clock guarantee)
            # must exclude connect time, and a reconnect near the deadline must not
            # start a fresh segment past it.
            remaining = deadline - loop.time()
            if remaining <= 0:
                await client.close()
                break

            reason: str | None = None
            try:
                backoff = _BACKOFF_INITIAL
                if pending is not None:
                    prev_reason, interrupted_at = pending
                    gap = loop.time() - interrupted_at
                    _marker(f, f"reconnected after {gap:.0f}s gap ({prev_reason})")
                    pending = None

                reason = await _capture_segment(
                    client, write_line, remaining, reconnect_after, datetime.now(UTC)
                )
            finally:
                await client.close()

            if reason is None:
                break
            pending = (reason, loop.time())

    return count


def capture_frames(
    host: str,
    port: int,
    output: Path,
    duration: float,
    reconnect_after: float = 60.0,
    features: frozenset[str] = frozenset(),
) -> None:
    console = Console()
    console.print(f"Connecting to [bold]{host}:{port}[/bold]…")
    resilience = (
        "auto-reconnect on drop or quiet stream"
        if reconnect_after > 0
        else "auto-reconnect on drop"
    )
    console.print(
        f"Capturing redacted frames for {duration:g}s ({resilience}) → {output}"
    )
    with _silence_shutdown_noise():
        count = asyncio.run(
            _run(host, port, output, duration, reconnect_after, features)
        )
    # Redaction happens upstream per-frame; frames the library can't decode are
    # passed through untouched, so don't promise more than is guaranteed.
    console.print(
        f"Wrote [bold]{count}[/bold] frame(s) — serials redacted in all decodable "
        f"frames. Worth a skim before attaching to a public issue."
    )
