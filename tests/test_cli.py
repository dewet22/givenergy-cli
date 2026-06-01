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
