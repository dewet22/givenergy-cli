from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any, ClassVar

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.reactive import reactive
from textual.widgets import (
    Collapsible,
    DataTable,
    Footer,
    Header,
    Label,
    ProgressBar,
    RichLog,
    Static,
)

from givenergy_modbus.client.client import Client
from givenergy_modbus.model.inverter import SinglePhaseInverter
from givenergy_modbus.model.plant import Plant

# Getter signature: (inverter, plant) -> display string
Getter = Callable[[SinglePhaseInverter, Plant], str]


def _fmt_slot(slot) -> str:
    if slot is None:
        return "—"
    return f"{slot.start.strftime('%H:%M')}–{slot.end.strftime('%H:%M')}"


class PlantPanel(Static):
    """Base panel: renders a titled two-column DataTable driven by _ROWS."""

    DEFAULT_CSS = """
    PlantPanel {
        border: solid $accent;
        padding: 1 2;
        height: 100%;
        width: 1fr;
        overflow-y: auto;
    }
    PlantPanel .title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }
    PlantPanel DataTable {
        height: auto;
    }
    """

    _TITLE: ClassVar[str] = ""
    _ROWS: ClassVar[list[tuple[str, str, Getter]]] = []
    _SECTIONS: ClassVar[list[tuple[str, list[tuple[str, str, Getter]]]]] = []
    _ATTR_COL_WIDTH: ClassVar[int] = 10

    def compose(self) -> ComposeResult:
        yield Label(self._TITLE, classes="title")
        yield DataTable(show_header=False, cursor_type="none", id="main-table")
        for i, (section_title, _) in enumerate(self._SECTIONS):
            with Collapsible(title=section_title, collapsed=True):
                yield DataTable(
                    show_header=False, cursor_type="none", id=f"section-table-{i}"
                )

    def on_mount(self) -> None:
        self._table_cols: list[tuple[DataTable, Any]] = []
        main = self.query_one("#main-table", DataTable)
        main.add_column("attribute", key="attribute", width=self._ATTR_COL_WIDTH)
        col = main.add_column("value", key="value", width=40)
        self._table_cols.append((main, col))
        for key, label, _ in self._ROWS:
            main.add_row(label, "—", key=key)
        for i, (_, rows) in enumerate(self._SECTIONS):
            sec = self.query_one(f"#section-table-{i}", DataTable)
            sec.add_column("attribute", key="attribute", width=self._ATTR_COL_WIDTH)
            col = sec.add_column("value", key="value", width=40)
            self._table_cols.append((sec, col))
            for key, label, _ in rows:
                sec.add_row(label, "—", key=key)

    def on_resize(self) -> None:
        value_width = max(10, self.content_size.width - self._ATTR_COL_WIDTH - 1)
        for table, col_key in self._table_cols:
            table.columns[col_key].width = value_width
            table.refresh()

    def refresh_from(self, plant: Plant) -> None:
        inv = plant.inverter
        if not inv.model:
            return
        main = self.query_one("#main-table", DataTable)
        for key, _, getter in self._ROWS:
            main.update_cell(key, "value", getter(inv, plant))
        for i, (_, rows) in enumerate(self._SECTIONS):
            sec = self.query_one(f"#section-table-{i}", DataTable)
            for key, _, getter in rows:
                sec.update_cell(key, "value", getter(inv, plant))


class InverterPanel(PlantPanel):
    """Inverter identity and thermal status."""

    _TITLE = "Inverter"
    _ROWS: ClassVar[list[tuple[str, str, Getter]]] = [
        ("model", "Model", lambda inv, p: inv.model.name),
        ("serial", "Serial", lambda inv, p: p.inverter_serial_number),
        ("adapter", "Adapter", lambda inv, p: p.data_adapter_serial_number),
        ("firmware", "Firmware", lambda inv, p: inv.firmware_version),
        ("status", "Status", lambda inv, p: str(inv.status)),
        ("heatsink", "Heatsink", lambda inv, p: f"{inv.t_inverter_heatsink} °C"),
        ("charger", "Charger", lambda inv, p: f"{inv.t_charger} °C"),
        ("uptime", "Uptime", lambda inv, p: f"{inv.work_time_total} h"),
    ]
    _SECTIONS: ClassVar[list[tuple[str, list[tuple[str, str, Getter]]]]] = [
        (
            "Charge Slots",
            [
                (
                    f"charge_slot_{i}",
                    f"Slot {i}",
                    lambda inv, p, i=i: _fmt_slot(getattr(inv, f"charge_slot_{i}")),  # type: ignore[misc]
                )
                for i in range(1, 11)
            ],
        ),
        (
            "Discharge Slots",
            [
                (
                    f"discharge_slot_{i}",
                    f"Slot {i}",
                    lambda inv, p, i=i: _fmt_slot(getattr(inv, f"discharge_slot_{i}")),  # type: ignore[misc]
                )
                for i in range(1, 11)
            ],
        ),
    ]


class PowerFlowPanel(PlantPanel):
    """Real-time power flows."""

    _TITLE = "Power Flow"
    _ROWS: ClassVar[list[tuple[str, str, Getter]]] = [
        ("pv", "PV", lambda inv, p: f"{(inv.p_pv1 or 0) + (inv.p_pv2 or 0):>6} W"),
        ("grid", "Grid", lambda inv, p: f"{inv.p_grid_out or 0:>+6} W"),
        ("load", "Load", lambda inv, p: f"{inv.p_load_demand or 0:>6} W"),
        ("battery", "Battery", lambda inv, p: f"{inv.p_battery or 0:>+6} W"),
    ]


class BatteryPanel(PlantPanel):
    """Battery state, with SOC progress bar above the data table."""

    _TITLE = "Battery Storage"
    _ROWS: ClassVar[list[tuple[str, str, Getter]]] = [
        ("soc", "SOC", lambda inv, p: f"{inv.battery_soc} %"),
        ("temp", "Temp", lambda inv, p: f"{inv.t_battery} °C"),
        ("voltage", "Voltage", lambda inv, p: f"{inv.v_battery} V"),
        ("current", "Current", lambda inv, p: f"{inv.i_battery} A"),
        ("count", "Batteries", lambda inv, p: str(p.number_batteries)),
    ]

    DEFAULT_CSS = (
        PlantPanel.DEFAULT_CSS
        + """
    BatteryPanel ProgressBar { margin-bottom: 1; }
    """
    )

    def compose(self) -> ComposeResult:
        yield Label(self._TITLE, classes="title")
        yield ProgressBar(
            total=100,
            show_bar=True,
            show_percentage=True,
            show_eta=False,
            id="bat-soc-bar",
        )
        yield DataTable(show_header=False, cursor_type="none", id="main-table")

    def refresh_from(self, plant: Plant) -> None:
        try:
            soc = plant.inverter.battery_soc
        except Exception:
            return
        if soc is not None:
            self.query_one(ProgressBar).update(progress=soc)
        super().refresh_from(plant)


class ConnectionStatus(Label):
    """Animated connection status indicator."""

    SPINNER_FRAMES: ClassVar[str] = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    # None = connecting, True = connected, False = disconnected, "timeout" = refresh timed out
    connected: reactive[bool | None | str] = reactive(None)
    _frame: reactive[int] = reactive(0)

    def on_mount(self) -> None:
        self.set_interval(0.1, self._advance)

    def _advance(self) -> None:
        self._frame = (self._frame + 1) % len(self.SPINNER_FRAMES)
        self._render_state()

    def watch_connected(self) -> None:
        self._render_state()

    def _render_state(self) -> None:
        bold = "bold " if (self._frame // 5) % 2 else ""
        if self.connected is None:
            self.update(
                f"[yellow]{self.SPINNER_FRAMES[self._frame]}[/yellow] Connecting..."
            )
        elif self.connected == "timeout":
            self.update(f"[{bold}yellow]●[/{bold}yellow] Timeout")
        elif self.connected:
            self.update(f"[{bold}green]●[/{bold}green] Connected")
        else:
            self.update(f"[{bold}red]●[/{bold}red] Disconnected")


class ModbusLogHandler(logging.Handler):
    """Forwards givenergy_modbus log records into a RichLog widget."""

    LEVEL_COLOURS: ClassVar[dict[int, str]] = {
        logging.DEBUG: "dim white",
        logging.INFO: "white",
        logging.WARNING: "yellow",
        logging.ERROR: "red",
        logging.CRITICAL: "bold red",
    }

    def __init__(self, log_widget: RichLog) -> None:
        super().__init__()
        self.log_widget = log_widget

    def emit(self, record: logging.LogRecord) -> None:
        colour = self.LEVEL_COLOURS.get(record.levelno, "white")
        self.log_widget.write(
            f"[{colour}]{record.levelname:<8}[/{colour}] "
            f"[dim]{record.name}[/dim]  {record.getMessage()}"
        )


class GivEnergyApp(App):
    """GivEnergy TUI."""

    TITLE = "GivEnergy Monitor"

    CSS = """
    Screen { layout: vertical; }
    #panels { height: 1fr; }
    #log-panel {
        height: 10;
        width: 1fr;
        border-top: solid $accent;
        padding: 0 2;
        background: $panel;
    }
    #status-bar {
        height: 1;
        width: 1fr;
        padding: 0 2;
        background: $panel;
        color: $text-muted;
    }
    #last-refresh { width: 1fr; text-align: right; padding-right: 2; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("r", "refresh", "Refresh"),
        # Binding("c", "calibrate", "Calibrate SOC"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        host: str = "192.168.44.50",
        port: int = 8899,
        refresh_interval: float = 15.0,
        log_level: str = "WARNING",
    ) -> None:
        super().__init__()
        self.client = Client(host=host, port=port)
        self.refresh_interval = refresh_interval
        self.log_level = getattr(logging, log_level.upper(), logging.WARNING)
        self._next_refresh_full = False
        self._last_refresh_at: datetime | None = None
        self._last_refresh_failed = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="panels"):
            yield InverterPanel()
            yield PowerFlowPanel()
            yield BatteryPanel()
        yield RichLog(id="log-panel", highlight=True, markup=True)
        with Horizontal(id="status-bar"):
            yield Label("", id="last-refresh")
            yield ConnectionStatus(id="connection-status")
        yield Footer()

    @work(exclusive=True, exit_on_error=False)
    async def on_mount(self) -> None:
        handler = ModbusLogHandler(self.query_one(RichLog))
        handler.setLevel(self.log_level)
        modbus_logger = logging.getLogger("givenergy_modbus")
        modbus_logger.setLevel(self.log_level)
        modbus_logger.addHandler(handler)
        await self.client.connect()
        await self.client.refresh_plant(full_refresh=True)
        self._next_refresh_full = False
        self._refreshing_since: datetime | None = None
        # self.set_interval(self.refresh_interval, self._periodic_refresh)
        self.set_interval(1, self._tick_status_bar)
        self.set_interval(1, self._update_panels)

    async def _periodic_refresh(self) -> None:
        if not self.client.connected:
            return
        self._next_refresh_full = False
        self._refreshing_since = datetime.now()
        # try:
        #     await self.client.refresh_plant(full_refresh=full)
        # except TimeoutError:
        #     pass
        # else:
        self._last_refresh_at = datetime.now()
        self._update_panels()

        self._refreshing_since = None

    def _update_panels(self) -> None:
        plant = self.client.plant
        self.query_one(InverterPanel).refresh_from(plant)
        self.query_one(PowerFlowPanel).refresh_from(plant)
        self.query_one(BatteryPanel).refresh_from(plant)
        self._last_refresh_at = datetime.now()

    def _tick_status_bar(self) -> None:
        # Connection status
        status = self.query_one(ConnectionStatus)
        status.connected = self.client.connected

        # Last refresh label
        refresh_label = self.query_one("#last-refresh", Label)
        if self._refreshing_since:
            refresh_label.update(
                f"Refreshing ({(datetime.now() - self._refreshing_since).total_seconds()}s elapsed)"
            )
        else:
            if self._last_refresh_at is None:
                refresh_time = "never"
            else:
                delta = int((datetime.now() - self._last_refresh_at).total_seconds())
                if delta < 60:
                    ago = f"{delta}s ago"
                elif delta < 3600:
                    m = delta // 60
                    ago = f"{m}m ago"
                else:
                    h = delta // 3600
                    ago = f"{h}h ago"
                refresh_time = f"{self._last_refresh_at.strftime('%H:%M:%S')} ({ago})"
            refresh_label.update(f"Last refresh: {refresh_time}")

    def action_refresh(self) -> None:
        self._next_refresh_full = True
        self.notify("Full refresh queued.")

    # async def action_calibrate(self) -> None:
    #     if self.client.connected:
    #         await self.client.one_shot_command(commands.set_calibrate_battery_soc())
    #         self.notify("Battery SOC calibration initiated.")
