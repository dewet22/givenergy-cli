"""Reconnect-loop tests for `capture`, driven by a fake Client (no sockets).

The fakes are scripted per-connection: each `Client(host, port)` call in the
loop pops the next fake from a list, so a test can stage e.g. drop-then-resume.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import givenergy_cli.capture as capture


class FakeClient:
    def __init__(
        self, *, frames=(), then="complete", connect_raises=False, connect_delay=0.0
    ):
        self.frames = frames
        self.then = then  # "drop" | "quiet" | "complete"
        self.connect_raises = connect_raises
        self.connect_delay = connect_delay
        self.connected = False
        self.closed = False
        self.capture_called = False
        # Mirrors the real Client.plant attribute the watchdog reads.
        self.plant = SimpleNamespace(register_block_updated_at={})

    async def connect(self):
        if self.connect_delay:
            await asyncio.sleep(self.connect_delay)
        if self.connect_raises:
            raise OSError("connect refused")
        self.connected = True

    async def capture_frames(self, sink, duration):
        self.capture_called = True
        for frame in self.frames:
            sink("rx", frame)
        if self.then == "drop":
            self.connected = False
            await asyncio.sleep(10)  # until the watcher cancels us
        elif self.then == "quiet":
            # Frames arrived, then ingestion goes stale while the socket stays up.
            self.plant.register_block_updated_at = {
                (0x32, "IR", 0, 60): datetime.now(UTC) - timedelta(seconds=100)
            }
            await asyncio.sleep(10)
        # "complete" → return, simulating the duration elapsing.

    async def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _fast_timers(monkeypatch):
    monkeypatch.setattr(capture, "_POLL_INTERVAL", 0.01)
    monkeypatch.setattr(capture, "_BACKOFF_INITIAL", 0.01)
    monkeypatch.setattr(capture, "_BACKOFF_CAP", 0.02)


def _install(monkeypatch, fakes):
    it = iter(fakes)
    monkeypatch.setattr(capture, "Client", lambda host, port: next(it))


def _read(path: Path):
    lines = path.read_text().splitlines()
    frames = [ln for ln in lines if not ln.startswith("#")]
    markers = [ln for ln in lines if ln.startswith("#")]
    return frames, markers


def test_reconnects_after_drop(monkeypatch, tmp_path):
    fakes = [
        FakeClient(frames=[b"\xaa", b"\xbb"], then="drop"),
        FakeClient(frames=[b"\xcc"], then="complete"),
    ]
    _install(monkeypatch, fakes)
    out = tmp_path / "frames.log"

    # reconnect_after=0 isolates the drop trigger (no quiet watchdog).
    asyncio.run(capture._run("h", 1, out, duration=5.0, reconnect_after=0.0))

    frames, markers = _read(out)
    assert "aa" in frames[0] and "bb" in frames[1]
    assert "cc" in frames[2]  # resumed after reconnect
    assert len(markers) == 1 and "drop" in markers[0]
    assert all(fc.closed for fc in fakes)


def test_reconnects_after_quiet_stream(monkeypatch, tmp_path):
    fakes = [
        FakeClient(frames=[b"\x11"], then="quiet"),
        FakeClient(frames=[b"\x22"], then="complete"),
    ]
    _install(monkeypatch, fakes)
    out = tmp_path / "frames.log"

    asyncio.run(capture._run("h", 1, out, duration=5.0, reconnect_after=0.05))

    frames, markers = _read(out)
    assert "11" in frames[0]
    assert "22" in frames[1]  # resumed after the quiet stretch
    assert len(markers) == 1 and "quiet" in markers[0]


def test_retries_failed_connect(monkeypatch, tmp_path):
    fakes = [
        FakeClient(connect_raises=True),
        FakeClient(frames=[b"\x33"], then="complete"),
    ]
    _install(monkeypatch, fakes)
    out = tmp_path / "frames.log"

    asyncio.run(capture._run("h", 1, out, duration=5.0, reconnect_after=0.0))

    frames, markers = _read(out)
    assert "33" in frames[0]
    # An initial connect failure isn't a gap in capture, so no marker is written.
    assert markers == []
    assert fakes[0].closed is False  # never connected
    assert fakes[1].closed is True


def test_slow_connect_respects_deadline(monkeypatch, tmp_path):
    """A connect that outlasts the remaining budget doesn't start a segment past
    the deadline (the -d wall-clock guarantee excludes connect time)."""
    fake = FakeClient(frames=[b"\x99"], then="complete", connect_delay=0.05)
    _install(monkeypatch, [fake])
    out = tmp_path / "frames.log"

    count = asyncio.run(capture._run("h", 1, out, duration=0.02, reconnect_after=0.0))

    assert count == 0
    assert fake.capture_called is False  # deadline passed during connect
    assert fake.closed is True  # but the connection was still closed


def test_single_segment_no_markers(monkeypatch, tmp_path):
    fakes = [FakeClient(frames=[b"\x44", b"\x55"], then="complete")]
    _install(monkeypatch, fakes)
    out = tmp_path / "frames.log"

    count = asyncio.run(capture._run("h", 1, out, duration=5.0, reconnect_after=60.0))

    frames, markers = _read(out)
    assert count == 2
    assert len(frames) == 2
    assert markers == []
