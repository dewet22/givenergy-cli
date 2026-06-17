import json
import tempfile
from pathlib import Path

import givenergy_cli.__main__ as cli
import pytest
from typer.testing import CliRunner

runner = CliRunner()


def test_device_hex_and_decimal_equivalent(monkeypatch):
    """--device accepts both 0x32 and 50, resolving to the same value; --base parses hex too."""
    calls = []
    monkeypatch.setattr(cli, "probe_registers", lambda **kw: calls.append(kw))
    for arg in ("0x32", "50"):
        r = runner.invoke(
            cli.app,
            ["probe", "--type", "hr", "--base", "0x10", "--device", arg],
            env={"GIVENERGY_HOST": "127.0.0.1"},
        )
        assert r.exit_code == 0, r.output
    assert calls[0]["device_address"] == calls[1]["device_address"] == 0x32
    assert calls[0]["base"] == calls[1]["base"] == 0x10


def test_device_default_when_omitted(monkeypatch):
    """Omitting --device uses the 0x11 default without tripping the hex parser."""
    calls = []
    monkeypatch.setattr(cli, "probe_registers", lambda **kw: calls.append(kw))
    r = runner.invoke(
        cli.app,
        ["probe", "--type", "hr", "--base", "10"],
        env={"GIVENERGY_HOST": "127.0.0.1"},
    )
    assert r.exit_code == 0, r.output
    assert calls[0]["device_address"] == 0x11


def test_bad_hex_errors_cleanly():
    """Input that can't be parsed surfaces as a clean BadParameter, not a traceback."""
    # COLUMNS keeps rich from wrapping the message across the error-panel border.
    r = runner.invoke(
        cli.app, ["probe", "--type", "hr", "--base", "0xZZ"], env={"COLUMNS": "200"}
    )
    assert r.exit_code != 0
    assert "Invalid value" in r.output


def test_device_out_of_range():
    """A device address above 0xff is rejected with the bounds message."""
    r = runner.invoke(
        cli.app,
        ["probe", "--type", "hr", "--base", "0", "--device", "0x100"],
        env={"GIVENERGY_HOST": "127.0.0.1", "COLUMNS": "200"},
    )
    assert r.exit_code != 0
    assert "between 0 and 255" in r.output


def test_base_out_of_range():
    """A base register above 0xffff is rejected with the bounds message."""
    r = runner.invoke(
        cli.app,
        ["probe", "--type", "hr", "--base", "0x10000", "--device", "0x11"],
        env={"GIVENERGY_HOST": "127.0.0.1", "COLUMNS": "200"},
    )
    assert r.exit_code != 0
    assert "between 0 and 65535" in r.output


def test_probe_compact_default(monkeypatch):
    """probe forwards compact=False by default."""
    calls = []
    monkeypatch.setattr(cli, "probe_registers", lambda **kw: calls.append(kw))
    r = runner.invoke(
        cli.app,
        ["probe", "--type", "hr", "--base", "10"],
        env={"GIVENERGY_HOST": "127.0.0.1"},
    )
    assert r.exit_code == 0, r.output
    assert calls[0]["compact"] is False


def test_probe_compact_flag(monkeypatch):
    """--compact and its --terse alias both forward compact=True."""
    calls = []
    monkeypatch.setattr(cli, "probe_registers", lambda **kw: calls.append(kw))
    for flag in ("--compact", "--terse"):
        r = runner.invoke(
            cli.app,
            ["probe", "--type", "hr", "--base", "10", flag],
            env={"GIVENERGY_HOST": "127.0.0.1"},
        )
        assert r.exit_code == 0, r.output
    assert calls[0]["compact"] is True
    assert calls[1]["compact"] is True


def test_render_probe_compact_is_hex_dump():
    """Compact rendering is a header comment plus one LABEL(base,count): hex line
    per chunk, with no box-drawing table characters to mangle a copy-paste."""
    from givenergy_cli.registers import _render_probe_compact

    out = _render_probe_compact(
        "HR", 0x31, "192.168.1.5", 8899, [(4080, [0, 5, 0xFFFF]), (4140, [1])]
    )
    lines = out.splitlines()
    assert lines[0] == "# HR probe @ device 0x31 on 192.168.1.5:8899"
    assert lines[1] == "HR(4080,3): 00000005ffff"
    assert lines[2] == "HR(4140,1): 0001"
    assert not any(ch in out for ch in "┃━┏┓┗┛│─")


def test_render_probe_compact_not_folded_by_console():
    """A 240-char chunk line must reach the terminal as one logical line. Rich
    word-wraps space-less text to the console width unless soft_wrap is set, which
    would fold the hex and split it from its label — these flags must match the
    _probe call site."""
    from io import StringIO

    from rich.console import Console

    from givenergy_cli.registers import _render_probe_compact

    block = _render_probe_compact("HR", 0x31, "h", 1, [(0, list(range(60)))])
    console = Console(file=StringIO(), width=80)
    console.print(block, markup=False, highlight=False, soft_wrap=True)
    # Header + one chunk line = exactly two emitted lines, no folding.
    assert console.file.getvalue().count("\n") == 2


def test_mock_server_requires_capture():
    """--capture is mandatory."""
    r = runner.invoke(cli.app, ["mock-server"], env={"COLUMNS": "200"})
    assert r.exit_code != 0
    assert "--capture" in r.output


def test_mock_server_missing_file():
    """A nonexistent capture path is rejected before the server starts."""
    r = runner.invoke(
        cli.app,
        ["mock-server", "--capture", "does-not-exist.log"],
        env={"COLUMNS": "200"},
    )
    assert r.exit_code != 0
    assert "does not exist" in r.output


def test_mock_server_parses_args(monkeypatch):
    """Valid args are parsed and forwarded to serve_mock without starting a server."""
    calls = []
    monkeypatch.setattr(cli, "serve_mock", lambda **kw: calls.append(kw))
    seed = "tests/fixtures/two_batteries.json"  # any existing file; never parsed here
    r = runner.invoke(
        cli.app,
        ["mock-server", "--capture", seed, "--bind", "0.0.0.0", "--port", "9000"],
    )
    assert r.exit_code == 0, r.output
    assert calls[0]["bind"] == "0.0.0.0"
    assert calls[0]["port"] == 9000
    assert [str(p) for p in calls[0]["captures"]] == [seed]


def test_mock_server_port_out_of_range():
    """A port outside 0-65535 is rejected with a clean bounds message."""
    seed = "tests/fixtures/two_batteries.json"
    r = runner.invoke(
        cli.app,
        ["mock-server", "--capture", seed, "--port", "99999"],
        env={"COLUMNS": "200"},
    )
    assert r.exit_code != 0
    assert "between 0 and 65535" in r.output


def test_export_redact_default(monkeypatch):
    """export forwards redact=True to export_plant by default."""
    calls = []
    monkeypatch.setattr(cli, "export_plant", lambda **kw: calls.append(kw))
    with tempfile.NamedTemporaryFile(suffix=".json") as f:
        r = runner.invoke(
            cli.app,
            ["export", "--output", f.name],
            env={"GIVENERGY_HOST": "127.0.0.1"},
        )
    assert r.exit_code == 0, r.output
    assert calls[0]["redact"] is True


def test_export_no_redact_flag(monkeypatch):
    """--no-redact forwards redact=False to export_plant."""
    calls = []
    monkeypatch.setattr(cli, "export_plant", lambda **kw: calls.append(kw))
    with tempfile.NamedTemporaryFile(suffix=".json") as f:
        r = runner.invoke(
            cli.app,
            ["export", "--output", f.name, "--no-redact"],
            env={"GIVENERGY_HOST": "127.0.0.1"},
        )
    assert r.exit_code == 0, r.output
    assert calls[0]["redact"] is False


def test_export_plant_redacts_serials(monkeypatch):
    """export_plant with redact=True zeroes serial suffixes in top-level fields and register caches."""
    from givenergy_modbus.model.plant import Plant
    from givenergy_modbus.model.register_cache import RegisterCache

    from givenergy_cli.registers import export_plant

    # Build a RegisterCache with a real-shaped battery serial in IR(110-114).
    # CE2231G454 encodes as 5 big-endian uint16 values.
    real_serial = "CE2231G454"
    encoded = real_serial.encode("latin1").ljust(10, b"\x00")
    serial_vals = [int.from_bytes(encoded[i * 2 : i * 2 + 2], "big") for i in range(5)]

    cache = RegisterCache()
    for i, val in enumerate(serial_vals):
        from givenergy_modbus.model.register import IR

        cache[IR(110 + i)] = val

    plant = Plant()
    plant.inverter_serial_number = "CE2231G454"
    plant.data_adapter_serial_number = "SA2205T123"
    plant.register_caches[0x11] = cache

    async def _fake_capture(host: str, port: int):
        return plant, None

    monkeypatch.setattr("givenergy_cli.registers._capture", _fake_capture)

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        out = Path(f.name)

    try:
        export_plant(host="127.0.0.1", port=8899, output=out, redact=True)
        data = json.loads(out.read_text())

        assert data["inverter_serial_number"] == "CE2231G000"
        assert data["data_adapter_serial_number"] == "SA2205T000"
        cache_data = data["register_caches"]["0x11"]
        redacted_vals = [cache_data[f"IR({110 + i})"] for i in range(5)]
        redacted_bytes = b"".join(v.to_bytes(2, "big") for v in redacted_vals)
        redacted_str = redacted_bytes.decode("latin1").replace("\x00", "").upper()
        assert redacted_str == "CE2231G000"
    finally:
        out.unlink(missing_ok=True)


def test_inspect_rejects_malformed_json(tmp_path):
    """A file that isn't JSON produces a clean error, not a traceback."""
    bad = tmp_path / "bad.json"
    bad.write_text("{ this is not json")
    r = runner.invoke(cli.app, ["inspect", str(bad)], env={"COLUMNS": "200"})
    assert r.exit_code != 0
    assert "not a recognised plant export or probe dump" in r.output
    assert "Traceback" not in r.output


def test_inspect_rejects_wrong_shape(tmp_path):
    """Valid JSON with the wrong structure produces a clean error."""
    bad = tmp_path / "shape.json"
    bad.write_text('{"unexpected": "structure"}')
    r = runner.invoke(cli.app, ["inspect", str(bad)], env={"COLUMNS": "200"})
    assert r.exit_code != 0
    assert "not a recognised plant export or probe dump" in r.output
    assert "Traceback" not in r.output


def test_inspect_rejects_oversized_file(tmp_path, monkeypatch):
    """A file over the import size cap is rejected before being read."""
    import givenergy_cli.registers as registers

    monkeypatch.setattr(registers, "_MAX_IMPORT_BYTES", 1024)
    big = tmp_path / "big.json"
    big.write_text("x" * 2048)
    r = runner.invoke(cli.app, ["inspect", str(big)], env={"COLUMNS": "200"})
    assert r.exit_code != 0
    assert "too large" in r.output
    assert "Traceback" not in r.output


def test_parse_probe_dump_roundtrips_render():
    """A compact dump parses back to exactly the registers it rendered."""
    from givenergy_cli.registers import _render_probe_compact, parse_probe_dump

    chunks = [(0, [0x2001, 0x0003, 0x0832]), (60, [0x05DC])]
    text = _render_probe_compact("HR", 0x31, "10.0.0.1", 8899, chunks)
    assert parse_probe_dump(text) == {
        0x31: {
            "HR(0)": 0x2001,
            "HR(1)": 0x0003,
            "HR(2)": 0x0832,
            "HR(60)": 0x05DC,
        }
    }


def test_parse_probe_dump_ignores_status_and_timeout_lines():
    """The 'Probing …' line and 'timed out' diagnostics are not parsed as data."""
    from givenergy_cli.registers import parse_probe_dump

    text = "\n".join(
        [
            "Probing HR(0..119) at device 0x31 on 10.0.0.1:8899…",
            "  HR(60..119): timed out — no response",
            "# HR probe @ device 0x31 on 10.0.0.1:8899",
            "HR(0,2): 00010002",
        ]
    )
    assert parse_probe_dump(text) == {0x31: {"HR(0)": 1, "HR(1)": 2}}


def test_parse_probe_dump_merges_devices_and_banks():
    """Concatenated sections across devices and banks merge into one map."""
    from givenergy_cli.registers import parse_probe_dump

    text = "\n".join(
        [
            "# HR probe @ device 0x31 on h:1",
            "HR(0,1): 0001",
            "# IR probe @ device 0x32 on h:1",
            "IR(0,2): 000a000b",
        ]
    )
    assert parse_probe_dump(text) == {
        0x31: {"HR(0)": 1},
        0x32: {"IR(0)": 0x000A, "IR(1)": 0x000B},
    }


def test_load_capture_reads_export_json(tmp_path):
    """An export JSON loads into a Plant carrying its register caches."""
    from givenergy_cli.registers import load_capture

    export = tmp_path / "plant.json"
    export.write_text(
        json.dumps(
            {
                "inverter_serial_number": "",
                "data_adapter_serial_number": "",
                "capabilities": None,
                "register_caches": {"0x32": {"HR(0)": 1, "HR(1)": 2}},
            }
        )
    )
    plant = load_capture(export)
    assert 0x32 in plant.register_caches
    assert len(plant.register_caches[0x32]) == 2


def test_load_capture_reads_probe_dump(tmp_path):
    """A probe dump loads into a Plant: caches present, capabilities absent."""
    from givenergy_cli.registers import _render_probe_compact, load_capture

    dump = tmp_path / "dump.txt"
    dump.write_text(_render_probe_compact("HR", 0x31, "h", 1, [(0, [1, 2, 3])]))
    plant = load_capture(dump)
    assert 0x31 in plant.register_caches
    assert len(plant.register_caches[0x31]) == 3
    assert plant.capabilities is None


def test_load_capture_bails_on_unrecognised(tmp_path):
    """A file that is neither export nor probe dump raises a clean ValueError."""
    from givenergy_cli.registers import load_capture

    junk = tmp_path / "junk.txt"
    junk.write_text("hello world\nthis is not a register dump")
    with pytest.raises(ValueError, match="not a recognised"):
        load_capture(junk)


def test_shell_loads_file_into_namespace(tmp_path, monkeypatch):
    """shell FILE builds a Plant from the file and forwards it to the REPL seam."""
    from givenergy_cli.registers import _render_probe_compact

    captured = {}
    monkeypatch.setattr(
        cli, "_start_shell", lambda ns, banner: captured.update(ns=ns, banner=banner)
    )
    dump = tmp_path / "dump.txt"
    dump.write_text(_render_probe_compact("HR", 0x31, "h", 1, [(0, [1, 2, 3])]))
    r = runner.invoke(cli.app, ["shell", str(dump)])
    assert r.exit_code == 0, r.output
    plant = captured["ns"]["plant"]
    assert 0x31 in plant.register_caches
    assert set(captured["ns"]) >= {"plant", "caches", "batteries", "show", "console"}


def test_shell_live_snapshots_via_seam(monkeypatch):
    """With no file, shell snapshots live and forwards that plant (never connects)."""
    from givenergy_modbus.model.plant import Plant

    fake = Plant()
    monkeypatch.setattr(cli, "snapshot_plant", lambda host, port: (fake, None))
    captured = {}
    monkeypatch.setattr(cli, "_start_shell", lambda ns, banner: captured.update(ns=ns))
    r = runner.invoke(cli.app, ["shell"], env={"GIVENERGY_HOST": "127.0.0.1"})
    assert r.exit_code == 0, r.output
    assert captured["ns"]["plant"] is fake


def test_shell_requires_host_without_file():
    """No file and no host is a clean BadParameter, not a traceback."""
    r = runner.invoke(cli.app, ["shell"], env={"COLUMNS": "200"})
    assert r.exit_code != 0
    assert "host" in r.output.lower()
    assert "Traceback" not in r.output


def test_mock_server_rejects_oversized_capture(tmp_path, monkeypatch):
    """mock-server applies the same size cap to capture files."""
    import givenergy_cli.registers as registers

    monkeypatch.setattr(registers, "_MAX_IMPORT_BYTES", 1024)
    big = tmp_path / "big.log"
    big.write_text("x" * 2048)
    r = runner.invoke(
        cli.app, ["mock-server", "--capture", str(big)], env={"COLUMNS": "200"}
    )
    assert r.exit_code != 0
    assert "too large" in r.output


def test_model_table_escapes_markup():
    """Device-controlled strings render literally, not as Rich markup."""
    from rich.console import Console

    from givenergy_cli.registers import _model_table

    class StubModel:
        def model_dump(self):
            return {"serial": "[red]evil[/red][link=https://example.com]x[/link]"}

    console = Console(record=True, width=200)
    console.print(_model_table("Stub", StubModel()))
    text = console.export_text()
    # If markup were interpreted, the tags would be consumed by the renderer.
    assert "[red]evil[/red]" in text
    assert "[link=" in text


def test_export_writes_owner_only_permissions(monkeypatch, tmp_path):
    """export_plant creates the output file rw------- (owner-only)."""
    import os
    import stat

    from givenergy_modbus.model.plant import Plant

    from givenergy_cli.registers import export_plant

    plant = Plant()

    async def _fake_capture(host: str, port: int):
        return plant, None

    monkeypatch.setattr("givenergy_cli.registers._capture", _fake_capture)

    out = tmp_path / "plant.json"
    # Pre-create world-readable to confirm an existing file is tightened too.
    out.write_text("{}")
    out.chmod(0o644)

    export_plant(host="127.0.0.1", port=8899, output=out, redact=True)

    # Windows doesn't map POSIX permission bits, so only assert on Unix.
    if os.name != "nt":
        mode = stat.S_IMODE(os.stat(out).st_mode)
        assert mode == 0o600, oct(mode)
