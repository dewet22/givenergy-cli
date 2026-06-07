import json
import tempfile
from pathlib import Path

import givenergy_cli.__main__ as cli
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
