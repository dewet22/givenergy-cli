import givenergy_cli.__main__ as cli
import pytest
import typer
from typer.testing import CliRunner

from givenergy_modbus.client.client import Client

from givenergy_cli.features import (
    FEATURES,
    Feature,
    _parse_features,
    client_kwargs,
    resolve_features,
)

runner = CliRunner()


def test_feature_enabled_value_defaults_to_true():
    assert Feature("x", "kx", "help").enabled_value is True


def test_splice_heal_registry_entry():
    f = FEATURES["splice-heal"]
    assert f.client_kwarg == "splice_reject_heal_seconds"
    assert f.enabled_value == 300.0


def test_parse_features_empty():
    assert _parse_features([], None) == set()


def test_parse_features_unions_flags_and_env_with_comma_split():
    assert _parse_features(["a,b", "c"], "c,d") == {"a", "b", "c", "d"}


def test_parse_features_strips_whitespace_and_drops_empties():
    assert _parse_features([" a , b "], " c ,, ") == {"a", "b", "c"}


def test_resolve_features_accepts_known_from_flag_and_env():
    assert resolve_features(["splice-heal"], None) == frozenset({"splice-heal"})
    assert resolve_features([], "splice-heal") == frozenset({"splice-heal"})


def test_resolve_features_empty_is_empty_frozenset():
    assert resolve_features([], None) == frozenset()


def test_resolve_features_rejects_unknown_and_lists_available():
    with pytest.raises(typer.BadParameter) as exc:
        resolve_features(["bogus"], None)
    msg = str(exc.value)
    assert "bogus" in msg
    assert "splice-heal" in msg


def test_client_kwargs_without_features_is_empty():
    assert client_kwargs(frozenset()) == {}


def test_client_kwargs_splice_heal_sets_300_seconds():
    assert client_kwargs(frozenset({"splice-heal"})) == {
        "splice_reject_heal_seconds": 300.0
    }


def test_client_kwargs_apply_to_a_real_client():
    client = Client(
        host="127.0.0.1", port=8899, **client_kwargs(frozenset({"splice-heal"}))
    )
    assert client.plant.splice_reject_heal_seconds == 300.0


def test_cli_rejects_unknown_feature_flag():
    r = runner.invoke(cli.app, ["--enable", "bogus", "tui"], env={"COLUMNS": "200"})
    assert r.exit_code != 0
    assert "bogus" in r.output
    assert "splice-heal" in r.output


def test_cli_rejects_unknown_feature_env():
    r = runner.invoke(
        cli.app, ["shell"], env={"COLUMNS": "200", "GIVENERGY_FEATURES": "bogus"}
    )
    assert r.exit_code != 0
    assert "bogus" in r.output
    assert "splice-heal" in r.output


def test_enable_flag_threads_features_to_the_worker(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(cli, "probe_registers", lambda **kw: captured.update(kw))
    r = runner.invoke(
        cli.app,
        ["--enable", "splice-heal", "probe", "--type", "hr", "--base", "0"],
        env={"GIVENERGY_HOST": "127.0.0.1"},
    )
    assert r.exit_code == 0, r.output
    assert captured["features"] == frozenset({"splice-heal"})
