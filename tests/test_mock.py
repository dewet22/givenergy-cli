import json

import pytest


def test_parse_sentinel_valid():
    from givenergy_modbus.model.register import HR

    from givenergy_cli.mock import _parse_sentinel

    dev, bank, rng = _parse_sentinel("0x11:HR:0-119")
    assert dev == 0x11
    assert bank is HR
    assert rng == range(0, 120)


def test_parse_sentinel_decimal_device_and_lowercase_bank():
    from givenergy_modbus.model.register import IR

    from givenergy_cli.mock import _parse_sentinel

    dev, bank, rng = _parse_sentinel("50:ir:60-119")
    assert dev == 50
    assert bank is IR
    assert list(rng) == list(range(60, 120))


@pytest.mark.parametrize(
    "bad",
    ["0x11:HR", "0x11:HR:0", "0x11:XX:0-1", "0x11:HR:5-1", "bogus"],
)
def test_parse_sentinel_rejects_malformed(bad):
    from givenergy_cli.mock import _parse_sentinel

    with pytest.raises(ValueError):
        _parse_sentinel(bad)


def test_parse_spec_file_json(tmp_path):
    from givenergy_modbus.model.register import HR, IR

    from givenergy_cli.mock import _parse_spec_file

    p = tmp_path / "spec.json"
    p.write_text(json.dumps({"0x11": {"HR:0": [8193, 0, 1], "IR:60": [100, 200]}}))
    assert _parse_spec_file(p) == {0x11: {(HR, 0): [8193, 0, 1], (IR, 60): [100, 200]}}


def test_parse_spec_file_yaml(tmp_path):
    from givenergy_modbus.model.register import HR

    from givenergy_cli.mock import _parse_spec_file

    p = tmp_path / "spec.yaml"
    p.write_text('"0x11":\n  "HR:0": [1, 2, 3]\n')
    assert _parse_spec_file(p) == {0x11: {(HR, 0): [1, 2, 3]}}


@pytest.mark.parametrize("bad_values", [[1.5], [1.0], [True], ["x"]])
def test_parse_spec_file_rejects_non_int_values(tmp_path, bad_values):
    from givenergy_cli.mock import _parse_spec_file

    p = tmp_path / "spec.json"
    p.write_text(json.dumps({"0x11": {"HR:0": bad_values}}))
    with pytest.raises(ValueError):
        _parse_spec_file(p)


def test_build_mock_from_spec_seeds_device():
    """_build_mock routes a parsed spec through MockPlant.from_spec and seeds it."""
    from givenergy_modbus.model.register import HR

    from givenergy_cli.mock import _build_mock

    spec = {0x11: {(HR, 0): [0x2001], (HR, 21): [449]}}
    mock = _build_mock(captures=[], spec=spec, sentinels=None, offset=0)
    assert 0x11 in mock.devices
