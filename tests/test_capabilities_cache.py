from givenergy_modbus.model.plant import PlantCapabilities

from givenergy_cli import capabilities_cache as cc


def _caps() -> PlantCapabilities:
    return PlantCapabilities.from_dict(
        {
            "schema_version": 1,
            "device_type": "HYBRID",
            "inverter_address": "0x32",
            "meter_addresses": ["0x01"],
            "lv_battery_addresses": ["0x32", "0x33"],
            "bcu_stacks": [],
            "aio_battery_module_addresses": [],
        }
    )


def _use_tmp_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(cc.platformdirs, "user_cache_dir", lambda _name: str(tmp_path))


def test_round_trip(monkeypatch, tmp_path):
    _use_tmp_cache(monkeypatch, tmp_path)
    caps = _caps()
    assert cc.load("192.168.1.5", 8899) is None  # nothing cached yet
    cc.save("192.168.1.5", 8899, caps)
    assert cc.load("192.168.1.5", 8899) == caps


def test_keyed_per_host_and_port(monkeypatch, tmp_path):
    _use_tmp_cache(monkeypatch, tmp_path)
    cc.save("host-a", 8899, _caps())
    assert cc.load("host-b", 8899) is None
    assert cc.load("host-a", 9999) is None
    assert cc.load("host-a", 8899) is not None


def test_corrupt_file_returns_none(monkeypatch, tmp_path):
    _use_tmp_cache(monkeypatch, tmp_path)
    cc._path("h", 8899).write_text("{ not valid json")
    assert cc.load("h", 8899) is None


def test_save_swallows_unwritable_dir(monkeypatch, tmp_path):
    # Point the cache at a path whose parent can't be created (a file), and
    # confirm save() degrades silently rather than raising into startup.
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a dir")
    monkeypatch.setattr(cc.platformdirs, "user_cache_dir", lambda _name: str(blocker))
    cc.save("h", 8899, _caps())  # must not raise
    assert cc.load("h", 8899) is None
