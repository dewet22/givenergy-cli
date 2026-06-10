"""Glance view tests: flow_status classification + a Textual Pilot smoke test
for the Glance/Flow/Analyst tab structure (fake Client, no network)."""

import asyncio

from givenergy_cli.glance import IDLE_THRESHOLD, flow_status


def test_flow_status_exporting_with_solar():
    sentence, colour = flow_status(pv=2.16, grid=1.35, battery=0.0)
    assert "exporting" in sentence.lower()
    assert "solar ahead" in sentence.lower()
    assert colour == "green"


def test_flow_status_exporting_without_solar():
    sentence, colour = flow_status(pv=0.0, grid=0.5, battery=1.0)
    assert sentence == "Exporting to the grid."
    assert colour == "green"


def test_flow_status_importing():
    sentence, colour = flow_status(pv=0.1, grid=-2.0, battery=0.0)
    assert "importing" in sentence.lower()
    assert colour == "red"


def test_flow_status_battery_discharge():
    sentence, colour = flow_status(pv=0.0, grid=0.0, battery=1.2)
    assert "battery covering" in sentence.lower()
    assert colour == "magenta"


def test_flow_status_charging_from_solar():
    sentence, colour = flow_status(pv=3.0, grid=0.0, battery=-2.0)
    assert sentence == "Charging battery from solar."
    assert colour == "cyan"


def test_flow_status_charging_no_solar():
    sentence, colour = flow_status(pv=0.0, grid=0.0, battery=-2.0)
    assert sentence == "Charging battery."
    assert colour == "cyan"


def test_flow_status_idle_and_threshold_edges():
    sentence, colour = flow_status(pv=0.0, grid=0.0, battery=0.0)
    assert colour == "dim"
    # Exactly at the threshold is still idle (strict comparison).
    sentence, colour = flow_status(pv=0.0, grid=IDLE_THRESHOLD, battery=-IDLE_THRESHOLD)
    assert colour == "dim"
    # Just over tips it.
    _, colour = flow_status(pv=0.0, grid=IDLE_THRESHOLD + 0.01, battery=0.0)
    assert colour == "green"


def test_tui_tabs_smoke(monkeypatch):
    """App composes with Glance default; 1/2/3 switch tabs. Fake Client whose
    connect() raises — on_mount tolerates that and the UI still builds."""
    from textual.widgets import TabbedContent, TabPane

    import givenergy_cli.app as app_mod

    class FakeClient:
        def __init__(self, host, port):
            from givenergy_modbus.model.plant import Plant

            self.plant = Plant()
            self.connected = False

        async def connect(self):
            # TimeoutError: the class _periodic_refresh treats as a routine
            # transient, so background timers stay quiet during the test.
            raise TimeoutError("test: no network")

        async def close(self):
            pass

    monkeypatch.setattr(app_mod, "Client", FakeClient)

    async def drive():
        app = app_mod.GivEnergyApp(host="127.0.0.1", refresh_interval=3600)
        async with app.run_test() as pilot:
            tabs = app.query_one(TabbedContent)
            pane_ids = {pane.id for pane in app.query(TabPane)}
            assert pane_ids == {"glance-tab", "flow-tab", "analyst-tab"}
            assert tabs.active == "glance-tab"
            await pilot.press("2")
            assert tabs.active == "flow-tab"
            await pilot.press("3")
            assert tabs.active == "analyst-tab"
            await pilot.press("1")
            assert tabs.active == "glance-tab"

    asyncio.run(drive())
