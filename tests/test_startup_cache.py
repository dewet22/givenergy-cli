"""Startup-path tests: the TUI skips cold detect() when a capabilities prior is
cached (warm path) and persists a fresh detect on a cold start."""

import asyncio

from givenergy_modbus.model.plant import Plant, PlantCapabilities

import givenergy_cli.app as app_mod
from givenergy_cli import capabilities_cache as cc


def _caps() -> PlantCapabilities:
    return PlantCapabilities.from_dict(
        {
            "schema_version": 1,
            "device_type": "HYBRID",
            "inverter_address": "0x32",
            "meter_addresses": ["0x01"],
            "lv_battery_addresses": ["0x32"],
            "bcu_stacks": [],
            "aio_battery_module_addresses": [],
        }
    )


class RecordingClient:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.plant = Plant()
        self.connected = True
        self.detect_calls = []  # list of `prior` args (None == cold)
        self.load_config_calls = 0
        self.refresh_calls = 0

    async def connect(self):
        pass

    async def detect(self, *, prior=None, **kw):
        self.detect_calls.append(prior)
        caps = _caps()
        self.plant.capabilities = caps
        return caps

    async def load_config(self, **kw):
        self.load_config_calls += 1

    async def refresh(self, **kw):
        self.refresh_calls += 1

    async def close(self):
        pass


def _run(monkeypatch, tmp_path, *, redetect=False):
    monkeypatch.setattr(cc.platformdirs, "user_cache_dir", lambda _n: str(tmp_path))
    monkeypatch.setattr(app_mod, "Client", RecordingClient)

    async def drive():
        app = app_mod.GivEnergyApp(
            host="10.0.0.9", refresh_interval=3600, redetect=redetect
        )
        async with app.run_test() as pilot:
            await pilot.pause(0.2)
            return app.client

    return asyncio.run(drive())


def test_cold_start_detects_and_persists(monkeypatch, tmp_path):
    client = _run(monkeypatch, tmp_path)
    # No prior cached → one cold detect (prior=None), then config+refresh.
    assert client.detect_calls == [None]
    assert client.load_config_calls >= 1
    assert client.refresh_calls >= 1
    # Capabilities are now persisted for next launch.
    assert cc.load("10.0.0.9", 8899) is not None


def test_redetect_forces_cold_despite_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(cc.platformdirs, "user_cache_dir", lambda _n: str(tmp_path))
    cc.save("10.0.0.9", 8899, _caps())  # cache present…

    client = _run(monkeypatch, tmp_path, redetect=True)
    # …but --redetect ignores it and runs a full cold detect.
    assert client.detect_calls == [None]


def test_warm_start_skips_cold_detect(monkeypatch, tmp_path):
    monkeypatch.setattr(cc.platformdirs, "user_cache_dir", lambda _n: str(tmp_path))
    cc.save("10.0.0.9", 8899, _caps())  # pre-seed the cache

    client = _run(monkeypatch, tmp_path)
    # Warm path: no cold detect; the only detect() is the hinted confirm
    # (prior is not None). load_config/refresh ran off the injected prior.
    assert client.detect_calls == [_caps()]  # i.e. [prior], not [None]
    assert all(p is not None for p in client.detect_calls)
    assert client.load_config_calls >= 1
    assert client.refresh_calls >= 1


def test_warm_start_adopts_changed_topology(monkeypatch, tmp_path):
    from givenergy_modbus.exceptions import PlantTopologyMismatch

    monkeypatch.setattr(cc.platformdirs, "user_cache_dir", lambda _n: str(tmp_path))
    stale = _caps()
    cc.save("10.0.0.9", 8899, stale)

    # The hardware now reports an extra battery — the hinted re-detect mismatches.
    actual = PlantCapabilities.from_dict(
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

    class MismatchClient(RecordingClient):
        async def detect(self, *, prior=None, **kw):
            self.detect_calls.append(prior)
            if prior is not None:
                self.plant.capabilities = None  # library nulls it on mismatch
                raise PlantTopologyMismatch("changed", prior=prior, actual=actual)
            return _caps()

    monkeypatch.setattr(app_mod, "Client", MismatchClient)

    async def drive():
        app = app_mod.GivEnergyApp(host="10.0.0.9", refresh_interval=3600)
        async with app.run_test() as pilot:
            await pilot.pause(0.2)
            return app

    app = asyncio.run(drive())
    # The new topology is adopted on the live client and re-persisted.
    assert app.client.plant.capabilities == actual
    assert cc.load("10.0.0.9", 8899) == actual


def test_partial_refresh_still_confirms_topology(monkeypatch, tmp_path):
    """A removed device makes the warm refresh partial — the path that most
    needs the topology re-check. The confirm must still run (regression: the
    partial used to abort the warm branch before _confirm_topology)."""
    from givenergy_modbus.exceptions import (
        PlantTopologyMismatch,
        RefreshPartiallySucceeded,
    )

    monkeypatch.setattr(cc.platformdirs, "user_cache_dir", lambda _n: str(tmp_path))
    cc.save("10.0.0.9", 8899, _caps())  # prior lists a battery that's now gone

    actual = PlantCapabilities.from_dict(
        {
            "schema_version": 1,
            "device_type": "HYBRID",
            "inverter_address": "0x32",
            "meter_addresses": ["0x01"],
            "lv_battery_addresses": [],  # the battery was removed
            "bcu_stacks": [],
            "aio_battery_module_addresses": [],
        }
    )

    class PartialThenMismatchClient(RecordingClient):
        async def refresh(self, **kw):
            self.refresh_calls += 1
            if self.refresh_calls == 1:  # first (warm) refresh sees the gone device
                raise RefreshPartiallySucceeded(
                    "1 of 2 reads failed",
                    plant=self.plant,
                    failures=[],
                    cause=ExceptionGroup("r", [TimeoutError()]),
                )

        async def detect(self, *, prior=None, **kw):
            self.detect_calls.append(prior)
            self.plant.capabilities = None
            raise PlantTopologyMismatch("changed", prior=prior, actual=actual)

    monkeypatch.setattr(app_mod, "Client", PartialThenMismatchClient)

    async def drive():
        app = app_mod.GivEnergyApp(host="10.0.0.9", refresh_interval=3600)
        async with app.run_test() as pilot:
            await pilot.pause(0.2)
            return app

    app = asyncio.run(drive())
    # Despite the partial first refresh, the confirm ran, adopted the change,
    # re-persisted, and repainted (a second refresh against the new topology).
    assert app.client.plant.capabilities == actual
    assert cc.load("10.0.0.9", 8899) == actual
    assert app.client.refresh_calls == 2
