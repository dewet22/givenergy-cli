"""Controls view: phase-aware dispatch units + a Pilot test that a control
change emits the right command, and that --allow-writes gates writes."""

import asyncio
from datetime import time as dt_time
from pathlib import Path

import pytest
from givenergy_modbus.model.plant import PlantCapabilities
from textual.widgets import Switch

import givenergy_cli.app as app_mod
from givenergy_cli import controls
from givenergy_cli.registers import load_plant

# A plant with a real inverter model (so the panel builds) but no capabilities
# of its own — we inject the topology under test.
_FIXTURE = load_plant(Path("tests/fixtures/two_batteries.json"))


def _caps(device_type="HYBRID") -> PlantCapabilities:
    return PlantCapabilities.from_dict(
        {
            "schema_version": 1,
            "device_type": device_type,
            "inverter_address": "0x32",
            "meter_addresses": [],
            "lv_battery_addresses": ["0x32"],
            "bcu_stacks": [],
            "aio_battery_module_addresses": [],
        }
    )


# --- dispatch ----------------------------------------------------------------


def test_parse_soc_bounds():
    assert controls.parse_soc("80") == 80
    for bad in ("3", "101", "abc", ""):
        with pytest.raises(ValueError):
            controls.parse_soc(bad)


def test_parse_hhmm():
    assert controls.parse_hhmm("23:30") == dt_time(23, 30)
    assert controls.parse_hhmm("") is None  # clears the endpoint
    for bad in ("24:00", "12:60", "nope"):
        with pytest.raises(ValueError):
            controls.parse_hhmm(bad)


def test_charge_target_single_phase_builds_command():
    reqs = controls.charge_target_cmd(80, _caps())
    assert isinstance(reqs, list) and reqs  # non-empty request list


def test_charge_target_unsupported_on_ems():
    with pytest.raises(controls.UnsupportedControl):
        controls.charge_target_cmd(80, _caps("EMS"))


def test_single_phase_has_two_slots():
    assert controls.slot_count(_caps()) == 2


# --- panel via Pilot ---------------------------------------------------------


class FakeClient:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.plant = _FIXTURE
        self.plant.capabilities = _caps()
        self.connected = True
        self.commands = []  # recorded one_shot_command request-lists

    async def connect(self):
        pass

    async def detect(self, *, prior=None, **kw):
        return _caps()

    async def load_config(self, **kw):
        pass

    async def refresh(self, **kw):
        pass

    async def one_shot_command(self, requests, **kw):
        self.commands.append(requests)

    async def close(self):
        pass


def test_toggle_emits_command_when_allowed(monkeypatch):
    monkeypatch.setattr(app_mod, "Client", FakeClient)

    async def go():
        app = app_mod.GivEnergyApp(
            host="127.0.0.1", refresh_interval=3600, allow_writes=True
        )
        async with app.run_test() as pilot:
            await pilot.pause(0.1)
            # Force the panel to build its rows from the plant, then let the
            # mounts settle (the periodic _update_panels timer would do this at
            # t=1s, but we don't want to wait).
            app.query_one(controls.ControlsPanel).refresh_from(app.client.plant)
            await pilot.pause(0.2)
            switch = app.query_one("#enable-charge", Switch)
            switch.value = not switch.value  # user toggles
            await pilot.pause(0.2)
            assert app.client.commands, "a write should have been sent"

    asyncio.run(go())


def test_reads_back_current_state(monkeypatch):
    """The panel reflects the inverter's current values (a second refresh syncs,
    once the dynamically-built widgets are queryable)."""
    monkeypatch.setattr(app_mod, "Client", FakeClient)

    async def go():
        app = app_mod.GivEnergyApp(
            host="127.0.0.1", refresh_interval=3600, allow_writes=True
        )
        async with app.run_test() as pilot:
            await pilot.pause(0.1)
            panel = app.query_one(controls.ControlsPanel)
            panel.refresh_from(app.client.plant)  # builds
            await pilot.pause(0.2)
            panel.refresh_from(app.client.plant)  # syncs values
            await pilot.pause(0.2)
            inv = app.client.plant.inverter
            assert app.query_one("#enable-charge", Switch).value == bool(
                inv.enable_charge
            )
            assert app.query_one("#charge-target").value == str(inv.charge_target_soc)
            assert app.query_one(
                "#cs1-start"
            ).value == inv.charge_slot_1.start.strftime("%H:%M")

    asyncio.run(go())


def test_readonly_blocks_writes(monkeypatch):
    monkeypatch.setattr(app_mod, "Client", FakeClient)

    async def go():
        app = app_mod.GivEnergyApp(
            host="127.0.0.1", refresh_interval=3600, allow_writes=False
        )
        async with app.run_test() as pilot:
            await pilot.pause(0.1)
            panel = app.query_one(controls.ControlsPanel)
            panel.refresh_from(app.client.plant)
            await pilot.pause(0.2)
            # Even if an Apply message is posted, the app must not send when
            # writes are disabled.
            panel.post_message(
                controls.ControlsPanel.Apply([object()], "x", "enable-charge")
            )
            await pilot.pause(0.2)
            assert app.client.commands == []
            # Controls render disabled in read-only mode.
            assert app.query_one("#enable-charge", Switch).disabled

    asyncio.run(go())


def test_frozen_control_holds_optimistic_value(monkeypatch):
    """A control with a write in flight isn't flipped back by the read-back
    sync; once unfrozen it re-syncs to the real state."""
    monkeypatch.setattr(app_mod, "Client", FakeClient)

    async def go():
        app = app_mod.GivEnergyApp(
            host="127.0.0.1", refresh_interval=3600, allow_writes=True
        )
        async with app.run_test() as pilot:
            await pilot.pause(0.1)
            panel = app.query_one(controls.ControlsPanel)
            panel.refresh_from(app.client.plant)
            await pilot.pause(0.2)
            panel.refresh_from(app.client.plant)  # sync to fixture (enable_charge True)
            await pilot.pause(0.1)
            switch = app.query_one("#enable-charge", Switch)
            assert switch.value is True

            # Optimistic value without going through the (async) write path:
            # mark the echo so it isn't treated as a user toggle, then flip.
            panel._programmatic.add("enable-charge")
            switch.value = False
            await pilot.pause(0.05)
            panel.freeze("enable-charge")
            # A read-back must NOT flip it back to the inverter's True.
            panel.refresh_from(app.client.plant)
            await pilot.pause(0.1)
            assert switch.value is False
            assert switch.has_class("-pending")  # frozen, but not disabled
            assert not switch.disabled  # so focus isn't stolen

            # Once the write resolves, unfreeze and re-sync to real state.
            panel.unfreeze("enable-charge")
            panel.refresh_from(app.client.plant)
            await pilot.pause(0.1)
            assert switch.value is True
            assert not switch.has_class("-pending")

    asyncio.run(go())


def test_commit_on_blur_and_dedup(monkeypatch):
    """A changed input value commits when it loses focus (HA-style); an
    unchanged value (e.g. tabbing through, or blur after Enter) sends nothing."""
    from givenergy_cli.controls import _CommitInput

    monkeypatch.setattr(app_mod, "Client", FakeClient)

    async def go():
        app = app_mod.GivEnergyApp(
            host="127.0.0.1", refresh_interval=3600, allow_writes=True
        )
        async with app.run_test() as pilot:
            await pilot.pause(0.1)
            panel = app.query_one(controls.ControlsPanel)
            panel.refresh_from(app.client.plant)
            await pilot.pause(0.2)
            panel.refresh_from(app.client.plant)  # _last_value now = fixture
            await pilot.pause(0.1)

            inp = app.query_one("#charge-target", _CommitInput)
            # Blur with the unchanged value → no write.
            inp.focus()
            await pilot.pause(0.05)
            app.query_one("#soc-reserve").focus()  # blur charge-target
            await pilot.pause(0.1)
            assert app.client.commands == [], "unchanged blur must not write"

            # Change the value and blur → commits.
            inp.value = "55"
            inp.focus()
            await pilot.pause(0.05)
            app.query_one("#soc-reserve").focus()
            await pilot.pause(0.2)
            assert app.client.commands, "changed blur should write"

    asyncio.run(go())


def test_ctrl_pagedown_cycles_tabs(monkeypatch):
    """The modifier cycle switches views even with a text input focused."""
    from textual.widgets import TabbedContent

    monkeypatch.setattr(app_mod, "Client", FakeClient)

    async def go():
        app = app_mod.GivEnergyApp(
            host="127.0.0.1", refresh_interval=3600, allow_writes=True
        )
        async with app.run_test() as pilot:
            await pilot.pause(0.1)
            panel = app.query_one(controls.ControlsPanel)
            panel.refresh_from(app.client.plant)
            await pilot.pause(0.2)
            tabs = app.query_one(TabbedContent)
            await pilot.press("4")
            assert tabs.active == "controls-tab"
            app.query_one("#charge-target").focus()  # an input now has focus
            await pilot.pause(0.05)
            await pilot.press("ctrl+pagedown")  # wraps to glance
            assert tabs.active == "glance-tab"
            await pilot.press("ctrl+pageup")  # back to controls
            assert tabs.active == "controls-tab"

    asyncio.run(go())


def test_write_result_flashes_control(monkeypatch):
    """A resolved write flashes the control green (ok) or red (fail) and clears
    the in-flight tint."""
    monkeypatch.setattr(app_mod, "Client", FakeClient)

    async def go():
        app = app_mod.GivEnergyApp(
            host="127.0.0.1", refresh_interval=3600, allow_writes=True
        )
        async with app.run_test() as pilot:
            await pilot.pause(0.1)
            panel = app.query_one(controls.ControlsPanel)
            panel.refresh_from(app.client.plant)
            await pilot.pause(0.2)
            sw = app.query_one("#enable-charge", Switch)

            panel.freeze("enable-charge")
            assert sw.has_class("-pending")
            panel.write_finished("enable-charge", ok=True)
            assert not sw.has_class("-pending")
            assert sw.has_class("-write-ok")
            assert not sw.has_class("-write-fail")

            panel.freeze("enable-charge")
            panel.write_finished("enable-charge", ok=False)
            assert sw.has_class("-write-fail")

    asyncio.run(go())


def test_readback_does_not_emit_write(monkeypatch):
    """Syncing a switch from the plant (e.g. external state change) must not be
    mistaken for a user toggle and emit a spurious write-back."""
    monkeypatch.setattr(app_mod, "Client", FakeClient)

    async def go():
        app = app_mod.GivEnergyApp(
            host="127.0.0.1", refresh_interval=3600, allow_writes=True
        )
        async with app.run_test() as pilot:
            await pilot.pause(0.1)
            panel = app.query_one(controls.ControlsPanel)
            panel.refresh_from(app.client.plant)  # builds (switch defaults False)
            await pilot.pause(0.2)
            panel.refresh_from(app.client.plant)  # syncs False -> True (fixture)
            await pilot.pause(0.2)
            # The sync changed the switch value, but it must not be mistaken for
            # a user toggle and emit a write.
            assert app.query_one("#enable-charge", Switch).value is True
            assert app.client.commands == [], "read-back must not emit a write"

    asyncio.run(go())
