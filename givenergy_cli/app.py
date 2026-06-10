from __future__ import annotations

import logging
import math
from collections.abc import Callable
from datetime import datetime
from typing import Any, ClassVar

from rich.markup import escape
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
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
    TabbedContent,
    TabPane,
)

from givenergy_modbus.client.client import Client
from givenergy_modbus.exceptions import (
    CommunicationError,
    PlantTopologyMismatch,
    RefreshFailed,
    RefreshPartiallySucceeded,
)
from givenergy_modbus.model.inverter import SinglePhaseInverter
from givenergy_modbus.model.plant import Plant, PlantCapabilities

from givenergy_cli import capabilities_cache
from givenergy_cli.controls import ConfirmModal, ControlsPanel
from givenergy_cli.glance import IDLE_THRESHOLD, GlancePanel, TileRow
from givenergy_cli.history import (
    EnergyCounters,
    History,
    PlantSnapshot,
    sparkline,
    sparkline_centred,
)

# Getter signature: (inverter, plant) -> display string
Getter = Callable[[SinglePhaseInverter, Plant], str]


def _fmt_slot(slot) -> str:
    if slot is None:
        return "—"
    return f"{slot.start.strftime('%H:%M')}–{slot.end.strftime('%H:%M')}"


def _fmt_kw(value: float, width: int = 5, signed: bool = False) -> str:
    """Format a kW value in fixed visual width, adapting precision so the
    column stays the same as the magnitude grows: 2 decimals below 10,
    1 decimal in the tens, 0 decimals at 100 and above.

    ``width=5`` fits ``" X.XX"``, ``"99.99"``, ``" 12.3"``, ``"99.9 "``,
    ``"  123"`` and so on. Set ``signed=True`` to always show a leading ±
    (pair with ``width=6``).
    """
    sign = "+" if signed else ""
    abs_v = abs(value)
    if abs_v < 10:
        precision = 2
    elif abs_v < 100:
        precision = 1
    else:
        precision = 0
    return f"{value:>{sign}{width}.{precision}f}"


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


class Topology(Static):
    """Power-flow topology — cardinal layout showing direction and magnitude
    of instantaneous power between Solar, Grid, Load, EPS, and Battery."""

    DEFAULT_CSS = """
    Topology {
        border: solid $accent;
        padding: 1 2;
        height: auto;
        width: 87;
        min-height: 20;
    }
    """

    # Below this threshold (kW) we treat a flow as "idle" — neutral arrow, dimmed
    # text. Shared with the Glance view's flow_status so the headline sentence
    # and the diagram always classify flows identically.
    _IDLE_THRESHOLD: ClassVar[float] = IDLE_THRESHOLD
    # Window driving the in-box sparklines (PV, load, EPS, SOC).
    _SPARKLINE_WINDOW_S: ClassVar[float] = 420.0  # ~7 min

    SPINNER_FRAMES: ClassVar[str] = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._ready = False
        self._anim_frame = 0
        self._anim_timer: Any = None
        self._flow_frame = 0
        self._flow_timer: Any = None
        self._state: tuple[float, float, float, float, float, int, int] | None = None
        self._history: History[PlantSnapshot] | None = None

    def on_mount(self) -> None:
        self.update(self._compose_startup(0))
        self._anim_timer = self.set_interval(0.08, self._tick_startup)
        # Surface the sparkline-window duration on the panel border so the
        # value behind the in-box trends is discoverable.
        self.border_subtitle = (
            f"sparklines · trailing {int(self._SPARKLINE_WINDOW_S / 60)} min"
        )

    def _tick_startup(self) -> None:
        if self._ready:
            return
        self._anim_frame += 1
        self.update(self._compose_startup(self._anim_frame))

    def _tick_flow(self) -> None:
        if self._state is None:
            return
        self._flow_frame += 1
        self.update(
            self._compose_diagram(
                *self._state, frame=self._flow_frame, history=self._history
            )
        )

    def refresh_from(self, plant: Plant) -> None:
        inv = plant.inverter
        if not inv.model:
            return
        if not self._ready:
            self._ready = True
            if self._anim_timer is not None:
                self._anim_timer.stop()
                self._anim_timer = None
            # Drive the connector-flow animation at ~7 Hz once data arrives.
            self._flow_timer = self.set_interval(0.15, self._tick_flow)
        pv = ((inv.p_pv1 or 0) + (inv.p_pv2 or 0)) / 1000.0
        # p_grid_out: positive = export to grid, negative = import from grid
        grid = (inv.p_grid_out or 0) / 1000.0
        load = (inv.p_load_demand or 0) / 1000.0
        # p_battery follows i_battery convention: positive = discharge (out of battery)
        battery = (inv.p_battery or 0) / 1000.0
        eps = (inv.p_backup or 0) / 1000.0
        soc = inv.battery_soc or 0
        n_batteries = plant.number_batteries or 1
        # Cache so flow ticks can re-render with the latest values + history.
        self._state = (pv, grid, load, battery, eps, soc, n_batteries)
        self._history = getattr(self.app, "history", None)
        self.update(
            self._compose_diagram(
                pv,
                grid,
                load,
                battery,
                eps,
                soc,
                n_batteries,
                frame=self._flow_frame,
                history=self._history,
            )
        )

    @staticmethod
    def _flow_speed(power_kw: float) -> float:
        """Animation speed (cells/tick) as a function of flow magnitude.
        sqrt(power) gives a perceptually balanced rate: a 4 kW flow feels
        twice as fast as a 1 kW flow rather than four times. The lower bound
        keeps light flows visibly drifting; the upper bound stays below 2× to
        avoid aliasing with the arrow `period`, which at 2.0 would make the
        pattern visit only half the cells and read as strobing."""
        return min(1.5, max(0.25, math.sqrt(abs(power_kw))))

    @staticmethod
    def _h_flow(
        length: int,
        direction: str,
        active: bool,
        frame: int,
        period: int = 4,
        speed: float = 1.0,
    ) -> str:
        """Animated horizontal connector. `direction` is 'right' or 'left';
        arrows shift `speed` cells per frame in that direction, repeating
        every `period` cells. When inactive, returns plain dashes."""
        if not active:
            return "─" * length
        arrow = "▶" if direction == "right" else "◀"
        chars = ["─"] * length
        offset = int(frame * speed)
        for i in range(length):
            phase = (i - offset) if direction == "right" else (i + offset)
            if phase % period == 0:
                chars[i] = arrow
        return "".join(chars)

    @staticmethod
    def _v_flow_2(
        direction: str, active: bool, frame: int, speed: float = 1.0
    ) -> tuple[str, str]:
        """Animated 2-row vertical conduit. Returns (top, bottom) chars. The
        arrow alternates ends at a rate proportional to `speed`; at speed=1.0
        it swaps every two frames, which feels right at 1 kW."""
        if not active or direction == "none":
            return ("│", "│")
        arrow = "▼" if direction == "down" else "▲"
        at_top = int(frame * speed / 2) % 2 == 0
        if direction == "down":
            return (arrow, "│") if at_top else ("│", arrow)
        else:
            return ("│", arrow) if at_top else (arrow, "│")

    def _compose_startup(self, frame: int) -> str:
        """Animated dual-waveform placeholder shown while probing the inverter."""
        width = 81
        height = 15
        center = height // 2
        chars: list[list[str]] = [[" "] * width for _ in range(height)]
        cols: list[list[str | None]] = [[None] * width for _ in range(height)]

        # Two superimposed sinusoids, scrolling in the same direction at
        # different speeds. We track colour per cell so they remain visually
        # distinct where they intersect.
        for x in range(width):
            y = center + 5.5 * math.sin(2 * math.pi * (x - frame * 0.7) / 18)
            yi = int(round(y))
            if 0 <= yi < height:
                chars[yi][x] = "●"
                cols[yi][x] = "cyan"
        for x in range(width):
            y = center + 3.8 * math.sin(
                2 * math.pi * (x - frame * 0.45) / 11 + math.pi / 3
            )
            yi = int(round(y))
            if 0 <= yi < height:
                # Overdraw the primary where they meet, using a bullseye glyph
                # so the crossing is visible rather than hidden.
                if chars[yi][x] == "●":
                    chars[yi][x] = "◉"
                    cols[yi][x] = "bright_yellow"
                else:
                    chars[yi][x] = "•"
                    cols[yi][x] = "yellow"

        # Give the status message a clear row.
        for r in (center - 1, center, center + 1):
            chars[r] = [" "] * width
            cols[r] = [None] * width
        spinner = self.SPINNER_FRAMES[frame % len(self.SPINNER_FRAMES)]
        status = f"  {spinner}  Establishing connection to inverter…  "
        start = (width - len(status)) // 2
        for i, ch in enumerate(status):
            col = start + i
            if 0 <= col < width:
                chars[center][col] = ch
                cols[center][col] = "bold bright_white"

        # Emit each row as colour-runs so adjacent same-colour cells share one
        # `[col]...[/col]` span instead of one tag per character.
        lines = []
        for ri in range(height):
            parts: list[str] = []
            buf = ""
            cur: str | None = None
            for x in range(width):
                colour = cols[ri][x]
                if colour != cur:
                    if buf:
                        parts.append(f"[{cur}]{buf}[/{cur}]" if cur else buf)
                    buf = chars[ri][x]
                    cur = colour
                else:
                    buf += chars[ri][x]
            if buf:
                parts.append(f"[{cur}]{buf}[/{cur}]" if cur else buf)
            lines.append("".join(parts))
        return "\n".join(lines)

    # Per-unit identity colours — used for borders (always on) and for
    # value text / arrows when the flow is active (dimmed when idle).
    _C_SOLAR: ClassVar[str] = "yellow"
    _C_GRID: ClassVar[str] = "dark_orange"
    _C_BATTERY: ClassVar[str] = (
        "#7cff7c"  # bright pastel green — bypasses ANSI 10 remap
    )
    _C_LOAD: ClassVar[str] = "cyan"
    _C_EPS: ClassVar[str] = "cyan"
    _C_INV: ClassVar[str] = "bright_blue"
    _C_DIM: ClassVar[str] = "bright_black"
    # Sparkline / border colours for signed flows.
    _C_LIGHT_RED: ClassVar[str] = "#ff7c7c"  # import (grid)
    _C_LIGHT_GREEN: ClassVar[str] = "#7cff7c"  # export (grid) / discharge (battery)
    _C_LIGHT_BLUE: ClassVar[str] = "#7caaff"  # charge (battery)
    _C_GRID_IMPORTING: ClassVar[str] = "#bb4444"  # darker red border when importing
    _C_GRID_EXPORTING: ClassVar[str] = "#44aa44"  # darker green border when exporting

    def _compose_diagram(
        self,
        pv: float,
        grid: float,
        load: float,
        battery: float,
        eps: float,
        soc: int,
        n_batteries: int = 1,
        frame: int = 0,
        history: History[PlantSnapshot] | None = None,
    ) -> str:
        idle = self._IDLE_THRESHOLD
        DIM = self._C_DIM

        pv_c = self._C_SOLAR if pv > idle else DIM
        load_c = self._C_LOAD if load > idle else DIM
        eps_c = self._C_EPS if eps > idle else DIM
        grid_c = self._C_GRID if abs(grid) > idle else DIM
        bat_c = self._C_BATTERY if abs(battery) > idle else DIM

        # Grid in-box indicator and connector direction:
        #   export (grid > 0): inverter pushes left into Grid → ◀
        #   import (grid < 0): Grid pushes right into inverter → ▶
        grid_abs = abs(grid)
        grid_speed = self._flow_speed(grid_abs)
        if grid > idle:
            grid_dir = "◀"
            grid_connector = self._h_flow(8, "left", True, frame, speed=grid_speed)
        elif grid < -idle:
            grid_dir = "▶"
            grid_connector = self._h_flow(8, "right", True, frame, speed=grid_speed)
        else:
            grid_dir = "·"
            grid_connector = "─" * 8

        # Display convention: charging positive, discharging negative.
        # Physical p_battery follows i_battery: > 0 = discharge.
        if battery > idle:
            bat_display, bat_dir = -battery, "up"
        elif battery < -idle:
            bat_display, bat_dir = -battery, "down"
        else:
            bat_display, bat_dir = 0.0, "none"

        # Inverter throughput — sum of energy entering the converter.
        inv_through = pv + max(0.0, battery) + max(0.0, -grid)
        inv_c = self._C_INV if inv_through > idle else DIM

        # Animated connector segments — speed scales with magnitude of flow.
        pv_top, pv_bot = self._v_flow_2(
            "down", pv > idle, frame, speed=self._flow_speed(pv)
        )
        bat_top, bat_bot = self._v_flow_2(
            bat_dir,
            abs(battery) > idle,
            frame,
            speed=self._flow_speed(abs(battery)),
        )
        # Pure animation: combined-output trunk with no kW label, Y-splitter
        # at col 56 branching up to Load and down to EPS. The kW number is
        # already in the Load and EPS boxes themselves; the trunk's job is to
        # convey magnitude via animation speed.
        # p_load_demand is sensed at the busbar and includes the EPS branch
        # (confirmed against the modbus capture corpus: (IR24−IR30)−IR42 sits
        # at ~0 with EPS active), so the trunk carries `load` and the Loads
        # box shows the house-only remainder.
        total_out = load
        load = max(load - eps, 0.0)
        out_c = self._C_LOAD if total_out > idle else DIM
        trunk_speed = self._flow_speed(total_out)
        trunk = self._h_flow(
            14, "right", total_out > idle, frame, period=4, speed=trunk_speed
        )

        def col(text: str, c: str) -> str:
            return f"[{c}]{text}[/{c}]"

        # Sparklines for in-box trends over the last ~7 minutes. Drawn in
        # identity colours so they stay legible even when the instantaneous
        # value is idle (i.e. you can still see the history when "now" is 0).
        window = history.window(self._SPARKLINE_WINDOW_S) if history is not None else []
        # All sparklines share a 9-cell width for visual consistency. The
        # duration of the trailing window is shown in the panel's border
        # subtitle (see `on_mount`).
        pv_spark = sparkline([s.pv for s in window], width=9)
        load_spark = sparkline([s.load for s in window], width=9)
        eps_spark = sparkline([s.eps for s in window], width=9)
        # Signed power sparklines (centred at zero). For the battery, charge =
        # positive on the chart (matches the value display convention). For
        # grid, import = positive (energy entering the system rises above the
        # baseline; export drops below).
        bat_power_top, bat_power_bot = sparkline_centred(
            [-s.battery for s in window], width=9
        )
        grid_power_top, grid_power_bot = sparkline_centred(
            [-s.grid for s in window], width=9
        )

        # Inner-width values for each box (split so sparkline + numeric parts
        # can take different colours). Solar/Battery are 25 inner; Load/EPS
        # are 20 inner; Grid stays 14 inner.
        pv_value_main = (
            f"    {_fmt_kw(pv)} kW    "  # 16 (paired with 9-cell spark = 25)
        )
        grid_value = f" {grid_dir}  {_fmt_kw(grid_abs)} kW  "  # 14
        load_value_main = f" {_fmt_kw(load)} kW  "  # 11 (paired with 9-cell spark = 20)
        eps_value_main = f" {_fmt_kw(eps)} kW  "  # 11
        soc_str = f"{soc:>3}%"  # 4
        bat_flow_str = f"{_fmt_kw(bat_display, width=6, signed=True)} kW"  # 9 — paired with 9-cell SOC spark
        inv_label = "   Inverter  "  # 13
        inv_value = f"   {_fmt_kw(inv_through)} kW  "  # 13 — centred under "Inverter"
        # Total-output label sits above the trunk on row 6, centred in the 14
        # cells between the inverter wall and the Y-splitter's vertical-up.
        trunk_label = f"   {_fmt_kw(total_out)} kW   "  # 14
        # Battery box title pluralises when the plant reports a stack rather
        # than a single unit. Dash count keeps the box at 27 cells wide.
        bat_word = "Batteries" if n_batteries > 1 else "Battery"
        bat_top_str = f"┌─ 🔋 {bat_word} " + "─" * (19 - len(bat_word)) + "┐"

        # Column anchors (assuming 2-cell-wide emoji glyphs):
        #   col  3-18  : grid box (16 wide)
        #   col 19-26  : grid↔inverter connector (8 wide)
        #   col 21-47  : solar / battery (27 wide)
        #   col 27-41  : inverter box (15 wide)
        #   col 34     : vertical conduit (solar / battery)
        #   col 42-55  : combined-output trunk (14 wide, pure animation)
        #   col 56     : Y-splitter ┤ (row 7), verticals (rows 6/8), corners (rows 5/9)
        #   col 57-58  : `─▶` entry to Load (row 5) and EPS (row 9)
        #   col 59-80  : Load box (rows 4–6) / EPS box (rows 8–10) — 22 wide
        SOLAR = self._C_SOLAR
        BATTERY = self._C_BATTERY
        LOAD = self._C_LOAD
        EPS_C = self._C_EPS
        INV = self._C_INV
        # Grid border reflects current direction; defaults to the identity
        # colour when idle.
        if grid > idle:
            GRID = self._C_GRID_EXPORTING
        elif grid < -idle:
            GRID = self._C_GRID_IMPORTING
        else:
            GRID = self._C_GRID

        lines = [
            # 0-2: Solar (27 outer, ┬ at col 34)
            f"{' ' * 21}{col('┌─ 🌞 Solar ──────────────┐', SOLAR)}",
            (
                f"{' ' * 21}{col('│', SOLAR)}{col(pv_spark, SOLAR)}"
                f"{col(pv_value_main, pv_c)}{col('│', SOLAR)}"
            ),
            f"{' ' * 21}{col('└────────────┬────────────┘', SOLAR)}",
            # 3: Solar conduit top
            f"{' ' * 34}{col(pv_top, pv_c)}",
            # 4: Solar conduit bottom (right side now empty — Loads top moved
            # into the central row 5-9 band)
            f"{' ' * 34}{col(pv_bot, pv_c)}",
            # 5: Grid top + Inverter top + Combined Loads top
            (
                f"   {col('┌─ ', GRID)}⚡️{col(' Grid ────┐', GRID)}"
                f"        {col('╔══════╧══════╗', INV)}"
                f"                 {col('┌─ 🏠 Loads ─────────┐', LOAD)}"
            ),
            # 6: Grid spark top + Inverter label + trunk-label + Y-splitter
            # corner-up + Load value
            (
                f"   {col('│', GRID)}  {col(grid_power_top, self._C_LIGHT_RED)}   {col('│', GRID)}"
                f"        {col('║', INV)}{col(inv_label, inv_c)}{col('║', INV)}"
                f"{col(trunk_label, out_c)}{col('┌─▶', load_c)}"
                f"{col('│', LOAD)}{col(load_spark, LOAD)}"
                f"{col(load_value_main, load_c)}{col('│', LOAD)}"
            ),
            # 7: Grid spark bot + connector + Inverter value + trunk + Y-splitter
            # + Loads divider (with EPS sub-label). Connector enters the Grid
            # right wall at the spark_bot row; the kW value is on the row
            # below for readability.
            (
                f"   {col('│', GRID)}  {col(grid_power_bot, self._C_LIGHT_GREEN)}   {col('│', GRID)}"
                f"{col(grid_connector, grid_c)}"
                f"{col('╢', INV)}{col(inv_value, inv_c)}{col('╟', INV)}"
                f"{col(trunk, out_c)}{col('┤', out_c)}  "
                f"{col('├─ 🆘 EPS ───────────┤', LOAD)}"
            ),
            # 8: Grid value + Inverter blank + Y-splitter corner-down + EPS value
            (
                f"   {col('│', GRID)}{col(grid_value, grid_c)}{col('│', GRID)}"
                f"        {col('║', INV)}             {col('║', INV)}"
                f"{' ' * 14}{col('└─▶', eps_c)}"
                f"{col('│', LOAD)}{col(eps_spark, EPS_C)}"
                f"{col(eps_value_main, eps_c)}{col('│', LOAD)}"
            ),
            # 9: Grid bottom + Inverter bottom + Combined Loads bottom
            (
                f"   {col('└──────────────┘', GRID)}"
                f"        {col('╚══════╤══════╝', INV)}"
                f"                 {col('└────────────────────┘', LOAD)}"
            ),
            # 10: Battery conduit top (Loads bottom now at row 9)
            f"{' ' * 34}{col(bat_top, bat_c)}",
            # 11: Battery conduit bottom
            f"{' ' * 34}{col(bat_bot, bat_c)}",
            # 12-15: Battery — SOC and power flow stacked on the left,
            # double-height centred sparkline filling the right side. 27 outer.
            f"{' ' * 21}{col(bat_top_str, BATTERY)}",
            (
                f"{' ' * 21}{col('│', BATTERY)} {col(soc_str, BATTERY)}"
                f"           {col(bat_power_top, self._C_LIGHT_BLUE)}{col('│', BATTERY)}"
            ),
            (
                f"{' ' * 21}{col('│', BATTERY)} {col(bat_flow_str, bat_c)}"
                f"      {col(bat_power_bot, self._C_LIGHT_GREEN)}{col('│', BATTERY)}"
            ),
            f"{' ' * 21}{col('└─────────────────────────┘', BATTERY)}",
        ]
        return "\n".join(lines)


class EnergyBalance(Static):
    """Energy-balance ledger — input vs output, with conversion losses."""

    DEFAULT_CSS = """
    EnergyBalance {
        border: solid $accent;
        padding: 1 2;
        height: 1fr;
        width: 87;
        min-height: 18;
    }
    """

    _IDLE_THRESHOLD: ClassVar[float] = 0.05
    _IMBALANCE_WINDOW_S: ClassVar[float] = 60.0
    # Energy counters tick at 0.1 kWh: 5 min gives ~1.2 kW resolution, 30 min
    # gives ~0.2 kW. Default to 30 min so a 100–200 W bias is actually visible.
    _ENERGY_WINDOW_S: ClassVar[float] = 1800.0
    # Below this many kWh of counter movement the result is dominated by the
    # 0.1 kWh quantisation step; treat it as "not enough signal yet".
    _ENERGY_MIN_DELTA_KWH: ClassVar[float] = 0.2

    def on_mount(self) -> None:
        # Render an all-zero placeholder so the panel claims its space
        # before the first refresh lands.
        self.update(self._compose_balance(0.0, 0.0, 0.0, 0.0, 0.0))

    def refresh_from(self, plant: Plant) -> None:
        inv = plant.inverter
        if not inv.model:
            return
        pv = ((inv.p_pv1 or 0) + (inv.p_pv2 or 0)) / 1000.0
        grid = (inv.p_grid_out or 0) / 1000.0
        load = (inv.p_load_demand or 0) / 1000.0
        battery = (inv.p_battery or 0) / 1000.0
        eps = (inv.p_backup or 0) / 1000.0
        n_batteries = plant.number_batteries or 1
        history = getattr(self.app, "history", None)
        energy_history = getattr(self.app, "energy_history", None)
        self.update(
            self._compose_balance(
                pv,
                grid,
                load,
                battery,
                eps,
                n_batteries=n_batteries,
                history=history,
                energy_history=energy_history,
            )
        )

    def _compose_balance(
        self,
        pv: float,
        grid: float,
        load: float,
        battery: float,
        eps: float,
        n_batteries: int = 1,
        history: History[PlantSnapshot] | None = None,
        energy_history: History[EnergyCounters] | None = None,
    ) -> str:
        idle = self._IDLE_THRESHOLD
        grid_in = max(0.0, -grid)
        grid_out = max(0.0, grid)
        bat_out = max(0.0, battery)
        bat_in = max(0.0, -battery)
        in_total = pv + grid_in + bat_out
        # p_load_demand includes the EPS branch (busbar-sensed; confirmed from
        # the modbus capture corpus), so EPS is shown as an "of which" row
        # rather than added on top — adding it double-counted ~all of EPS in
        # the imbalance figure.
        out_total = load + grid_out + bat_in
        # Signed imbalance — not the same as conversion losses. The register
        # banks aren't read atomically by the inverter, so individual flows
        # can be sampled tens of ms apart and the totals won't balance even in
        # a real steady state. Expect small ± values during normal operation.
        imbalance = in_total - out_total
        bat_word = "Batteries" if n_batteries > 1 else "Battery"

        S = Topology._C_SOLAR
        G = Topology._C_GRID
        B = Topology._C_BATTERY
        L = Topology._C_LOAD
        E = Topology._C_EPS
        INV = Topology._C_INV
        DIM = Topology._C_DIM

        def v(value: float, identity: str) -> str:
            c = identity if abs(value) > idle else DIM
            return f"[{c}]{_fmt_kw(value)} kW[/{c}]"

        def row(
            l_label: str,
            l_val: float | None,
            l_col: str,
            r_label: str,
            r_val: float | None,
            r_col: str,
        ) -> str:
            left = f"{l_label:<22}{v(l_val, l_col)}" if l_val is not None else " " * 30
            right = f"{r_label:<22}{v(r_val, r_col)}" if r_val is not None else " " * 30
            return f"  {left}  {right}"

        # Prefer a windowed-mean imbalance when we have enough history;
        # otherwise fall back to the noisy instantaneous value.
        window_s = self._IMBALANCE_WINDOW_S
        imb_disp = imbalance
        in_disp = in_total
        n_samples = 1
        if history is not None and len(history) > 0:
            in_avg = history.mean("in_total", window_s)
            imb_avg = history.mean("imbalance", window_s)
            if in_avg is not None and imb_avg is not None:
                imb_disp = imb_avg
                in_disp = in_avg
                n_samples = len(history.window(window_s))
        smoothed = n_samples > 1
        imb_label = (
            f"Imbalance ({int(window_s)} s avg, n={n_samples})"
            if smoothed
            else "Imbalance (instantaneous)"
        )

        # Energy-counter view: differencing the cumulative per-source counters
        # over a longer window sidesteps register-bank sampling skew entirely.
        # IN  = ΔPV + Δgrid_import + Δbattery_discharge_day
        # OUT = Δload_day + Δgrid_export + Δbattery_charge_day
        # (`e_inverter_in_total` is the AC-charge counter, not a sources total,
        # so we don't use it here.) Resolution is bounded by the 0.1 kWh
        # counter step: ≈1.2 kW at 5 min, ≈0.2 kW at 30 min per component.
        ewindow_s = self._ENERGY_WINDOW_S
        e_in_kwh: float | None = None
        e_out_kwh: float | None = None
        e_dt_s: float | None = None
        if energy_history is not None and len(energy_history) >= 2:
            d_in = energy_history.diff("cum_in", ewindow_s)
            d_out = energy_history.diff("cum_out", ewindow_s)
            if d_in is not None and d_out is not None:
                e_in_kwh, e_dt_s = d_in
                e_out_kwh, _ = d_out

        hdr = "─" * 30
        imb_col = "yellow" if abs(imb_disp) > idle else DIM
        imb_pct = f"  ({imb_disp / in_disp * 100:>+5.1f}%)" if in_disp > idle else ""
        lines = [
            f"  [{INV}]INPUT[/{INV}]" + " " * 27 + f"[{INV}]OUTPUT[/{INV}]",
            f"  {hdr}  {hdr}",
            # Rows ordered so Grid and Battery line up across columns; Solar
            # sources have no symmetric output, EPS is an output-only path.
            row("Grid (import)", grid_in, G, "Grid (export)", grid_out, G),
            row(
                f"{bat_word} (discharge)", bat_out, B, f"{bat_word} (charge)", bat_in, B
            ),
            row("Solar", pv, S, "Load", load, L),
            row("", None, "", "└ of which EPS", eps, E),
            f"  {hdr}  {hdr}",
            row("Total", in_total, INV, "Total", out_total, INV),
            "",
            f"  {imb_label}: [{imb_col}]{_fmt_kw(imb_disp, signed=True)} kW{imb_pct}[/{imb_col}]",
        ]
        if e_in_kwh is not None and e_out_kwh is not None and e_dt_s is not None:
            elapsed_min = e_dt_s / 60
            kwh_str = f"Δ {e_in_kwh:.1f}/{e_out_kwh:.1f} kWh"
            if max(e_in_kwh, e_out_kwh) < self._ENERGY_MIN_DELTA_KWH:
                lines.append(
                    f"  [{DIM}]Inverter Δ ({elapsed_min:.0f}m, {kwh_str}): "
                    f"below resolution — wait longer.[/{DIM}]"
                )
            else:
                e_in_pw = e_in_kwh / (e_dt_s / 3600)
                e_out_pw = e_out_kwh / (e_dt_s / 3600)
                e_loss = e_in_pw - e_out_pw
                e_loss_pct = (
                    f" ({e_loss / e_in_pw * 100:>+5.1f}%)" if e_in_pw > idle else ""
                )
                loss_col = "yellow" if abs(e_loss) > idle else DIM
                lines.append(
                    f"  Inverter Δ ({elapsed_min:.0f}m, {kwh_str}): "
                    f"[{INV}]in {e_in_pw:.2f}[/{INV}] · "
                    f"[{INV}]out {e_out_pw:.2f}[/{INV}] · "
                    f"[{loss_col}]loss {e_loss:+.2f} kW{e_loss_pct}[/{loss_col}]"
                )
        else:
            lines.append(
                f"  [{DIM}]Inverter Δ ({int(ewindow_s / 60)}m): "
                f"waiting for counter ticks…[/{DIM}]"
            )
        lines.append(
            f"  [{DIM}](signed — register banks aren't sampled atomically)[/{DIM}]"
        )

        # "Today so far" rollup from the daily counters on the latest energy
        # snapshot. Hidden until we have at least one energy snapshot to read.
        if energy_history is not None and (latest := energy_history.latest) is not None:
            pv_day = latest.pv_day
            gi_day = latest.e_grid_in_day
            go_day = latest.e_grid_out_day
            load_day = latest.e_consumption_day
            bc_day = latest.e_battery_charge_day
            bd_day = latest.e_battery_discharge_day

            def dval(value: float | None, ident: str) -> str:
                if value is None:
                    return f"[{DIM}]  —  [/{DIM}]"
                c = ident if value > idle else DIM
                return f"[{c}]{value:.1f}[/{c}]"

            lines.append("")
            lines.append(
                "  Today: "
                + f"[{S}]🌞[/{S}] {dval(pv_day, S)}"
                + " · "
                + f"⚡️ ↓{dval(gi_day, G)} ↑{dval(go_day, G)}"
                + " · "
                + f"[{L}]🏠[/{L}] {dval(load_day, L)}"
                + " · "
                + f"[{B}]🔋[/{B}] ↓{dval(bc_day, B)} ↑{dval(bd_day, B)} kWh"
            )

        return "\n".join(lines)


class ConnectionStatus(Label):
    """Animated connection status indicator."""

    SPINNER_FRAMES: ClassVar[str] = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    # None = connecting, "probing" = running detect/load_config/refresh on startup,
    # "reconnecting" = attempting to re-establish after a drop, True = connected,
    # False = disconnected, "timeout" = refresh timed out
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
        elif self.connected == "probing":
            self.update(
                f"[yellow]{self.SPINNER_FRAMES[self._frame]}[/yellow] Probing..."
            )
        elif self.connected == "reconnecting":
            self.update(
                f"[yellow]{self.SPINNER_FRAMES[self._frame]}[/yellow] Reconnecting..."
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
        # Log messages routinely embed network-derived bytes (exception reprs,
        # frame dumps) — escape so they can't inject markup into the RichLog.
        self.log_widget.write(
            f"[{colour}]{record.levelname:<8}[/{colour}] "
            f"[dim]{record.name}[/dim]  {escape(record.getMessage())}"
        )


class GivEnergyApp(App):
    """GivEnergy TUI."""

    TITLE = "GivEnergy Monitor"

    CSS = """
    Screen { layout: vertical; }
    #tabs { height: 1fr; }
    #flow-layout { height: 1fr; }
    #analyst-layout { height: 1fr; }
    #analyst-left { width: auto; height: 100%; }
    #analyst-right { width: 1fr; height: 100%; }
    #log-panel {
        display: none;
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
        Binding("r", "quick_refresh", "Refresh"),
        Binding("shift+r", "full_refresh", "Full refresh"),
        Binding("1", "show_glance", "Glance"),
        Binding("2", "show_flow", "Flow"),
        Binding("3", "show_analyst", "Analyst"),
        Binding("4", "show_controls", "Controls"),
        # Modifier cycle works even while a text input has focus (the digit
        # shortcuts get typed into the field instead of switching tabs).
        # Input binds ctrl+left/right for word nav, so use pageup/down.
        Binding("ctrl+pagedown", "next_tab", "Next view"),
        Binding("ctrl+pageup", "prev_tab", "Prev view", show=False),
        Binding("l", "toggle_log", "Logs"),
        Binding("q", "quit", "Quit"),
    ]

    _TAB_IDS: ClassVar[list[str]] = [
        "glance-tab",
        "flow-tab",
        "analyst-tab",
        "controls-tab",
    ]

    SPINNER_FRAMES: ClassVar[str] = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    # Consecutive all-devices-silent refreshes tolerated before forcing a
    # reconnect (partial refreshes never count toward this).
    _FAILED_STREAK_LIMIT: ClassVar[int] = 3

    def __init__(
        self,
        host: str = "192.168.44.50",
        port: int = 8899,
        refresh_interval: float = 15.0,
        log_level: str = "WARNING",
        redetect: bool = False,
        allow_writes: bool = False,
    ) -> None:
        super().__init__()
        self._host = host
        self._port = port
        self._redetect = redetect
        self._allow_writes = allow_writes
        self.client = Client(host=host, port=port)
        self.refresh_interval = refresh_interval
        self.log_level = getattr(logging, log_level.upper(), logging.WARNING)
        # 1 h of history at 15 s refresh — enough for short-window smoothing
        # and trend lines without growing unbounded. Two parallel rings: one
        # for instantaneous power values, one for the cumulative energy
        # counters used to derive sampling-skew-free averages.
        history_len = int(3600 / max(refresh_interval, 1.0))
        self.history: History[PlantSnapshot] = History(maxlen=history_len)
        self.energy_history: History[EnergyCounters] = History(maxlen=history_len)
        self._next_refresh_full = False
        self._last_refresh_at: datetime | None = None
        self._refreshing_since: datetime | None = None
        self._reconnecting = False
        self._status_frame = 0
        # Partial refreshes are routine on this hardware — absorbed but surfaced
        # in the status bar. RefreshFailed means *no* device replied; a streak of
        # those means the link is wedged despite the socket being up, so we
        # escalate to a reconnect.
        self._last_refresh_partial = False
        self._refresh_failed_streak = 0

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="glance-tab", id="tabs"):
            with TabPane("Glance", id="glance-tab"):
                yield GlancePanel(id="glance")
            with TabPane("Flow", id="flow-tab"):
                with Vertical(id="flow-layout"):
                    yield TileRow(id="flow-tiles")
                    yield Topology(id="topology")
            with TabPane("Analyst", id="analyst-tab"):
                with Horizontal(id="analyst-layout"):
                    with Vertical(id="analyst-left"):
                        yield EnergyBalance(id="energy-balance")
                    with Vertical(id="analyst-right"):
                        yield InverterPanel()
                        yield BatteryPanel()
            with TabPane("Controls", id="controls-tab"):
                yield ControlsPanel(allow_writes=self._allow_writes, id="controls")
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
        # Panels paint as soon as data lands — this tick only reads the plant
        # (no I/O), so it's safe to run alongside the serialised startup below.
        self.set_interval(1, self._update_panels)
        try:
            await self.client.connect()
            cached = (
                None
                if self._redetect
                else capabilities_cache.load(self._host, self._port)
            )
            if cached is not None:
                # Warm start: trust the cached topology and paint immediately,
                # then confirm it with a cheap hinted re-detect. A *removed*
                # device makes this first refresh partial AND the prior stale, so
                # _record_refresh tolerates the partial (rather than aborting the
                # branch) and the confirm always runs.
                self.client.plant.capabilities = cached
                await self.client.load_config()
                await self._record_refresh(modbus_logger)
                await self._confirm_topology(cached, modbus_logger)
            else:
                # Cold start: full probe, then persist for next time.
                self.query_one(ConnectionStatus).connected = "probing"
                caps = await self.client.detect()
                self.client.plant.capabilities = caps
                capabilities_cache.save(self._host, self._port, caps)
                await self.client.load_config()
                await self._record_refresh(modbus_logger)
        except Exception as exc:  # noqa: BLE001
            modbus_logger.error(
                "Startup failed: %r — will retry from periodic tick", exc
            )
        self.set_interval(self.refresh_interval, self._periodic_refresh)
        self.set_interval(1, self._tick_status_bar)

    async def _record_refresh(self, logger: logging.Logger) -> None:
        """Refresh and snapshot, tolerating a partial result (kept + flagged so
        the status bar can show '· partial'). A full RefreshFailed propagates to
        the caller — there's nothing usable to paint."""
        try:
            await self.client.refresh()
            self._last_refresh_partial = False
        except RefreshPartiallySucceeded as exc:
            logger.warning("refresh incomplete: %s", exc)
            self._last_refresh_partial = True
        self._last_refresh_at = datetime.now()
        self._snapshot()

    async def _confirm_topology(
        self, prior: PlantCapabilities, logger: logging.Logger
    ) -> None:
        """Hinted re-detect against the cached prior. Adopt + re-persist a changed
        layout (and refresh against it); otherwise keep the prior. A probe failure
        keeps the prior — periodic refresh carries on.

        A hinted detect only re-confirms the addresses already in the prior, so it
        catches a *removed* or changed device but **not** a newly-added one — use
        ``--redetect`` after adding hardware."""
        try:
            await self.client.detect(prior=prior)
        except PlantTopologyMismatch as exc:
            logger.warning(
                "plant topology changed since last run — adopting new layout"
            )
            # detect() nulls plant.capabilities on mismatch; accept the new one
            # and repaint against it so the UI isn't left on the stale topology.
            self.client.plant.capabilities = exc.actual
            capabilities_cache.save(self._host, self._port, exc.actual)
            await self.client.load_config()
            await self._record_refresh(logger)
        except (CommunicationError, TimeoutError) as exc:
            logger.debug("topology re-check skipped: %r", exc)

    @work
    async def _periodic_refresh(self) -> None:
        if self._refreshing_since is not None:
            return
        full = self._next_refresh_full
        self._next_refresh_full = False
        self._refreshing_since = datetime.now()
        try:
            if not self.client.connected:
                self._reconnecting = True
                try:
                    # TODO(givenergy-modbus#62): connect() isn't safely re-entrant.
                    # We rely on the old tasks having self-terminated on EOF so
                    # `_shutting_down` stays False; don't call close() before this
                    # until the upstream issue is fixed.
                    await self.client.connect()
                except CommunicationError:
                    return
                finally:
                    self._reconnecting = False
            if full:
                await self.client.load_config()
            await self.client.refresh()
        except RefreshPartiallySucceeded:
            # Some reads failed — routine on this hardware. Whatever succeeded
            # has already been committed to the plant, so treat it as a refresh;
            # the library logs the per-read failures at WARNING for the log panel.
            self._last_refresh_partial = True
            self._refresh_failed_streak = 0
            self._last_refresh_at = datetime.now()
            self._snapshot()
        except RefreshFailed:
            # No device replied at all — the socket is up but the plant is
            # effectively unreachable. Tolerate a couple (the dongle has bad
            # moments), then force a reconnect via the existing machinery:
            # close() drops `connected`, and the status-bar tick re-dials.
            self._refresh_failed_streak += 1
            if self._refresh_failed_streak >= self._FAILED_STREAK_LIMIT:
                logging.getLogger("givenergy_modbus").warning(
                    "%d consecutive refreshes with no devices responding — "
                    "forcing a reconnect",
                    self._refresh_failed_streak,
                )
                self._refresh_failed_streak = 0
                await self.client.close()
        except TimeoutError:
            # Nothing usable this tick — keep showing the previous data.
            pass
        else:
            self._last_refresh_partial = False
            self._refresh_failed_streak = 0
            self._last_refresh_at = datetime.now()
            self._snapshot()
        finally:
            self._refreshing_since = None

    def _snapshot(self) -> None:
        at = self._last_refresh_at
        snap = PlantSnapshot.from_plant(self.client.plant, at=at)
        if snap is not None:
            self.history.append(snap)
        energy_counters = EnergyCounters.from_plant(self.client.plant, at=at)
        if energy_counters is not None:
            self.energy_history.append(energy_counters)

    def _update_panels(self) -> None:
        plant = self.client.plant
        self.query_one(GlancePanel).refresh_from(plant)
        self.query_one(TileRow).refresh_from(plant)
        self.query_one(Topology).refresh_from(plant)
        self.query_one(EnergyBalance).refresh_from(plant)
        self.query_one(InverterPanel).refresh_from(plant)
        self.query_one(BatteryPanel).refresh_from(plant)
        self.query_one(ControlsPanel).refresh_from(plant)

    def _tick_status_bar(self) -> None:
        status = self.query_one(ConnectionStatus)
        if self._reconnecting:
            status.connected = "reconnecting"
        else:
            status.connected = self.client.connected
        if not self.client.connected and self._refreshing_since is None:
            self._periodic_refresh()

        self._status_frame = (self._status_frame + 1) % len(self.SPINNER_FRAMES)
        refresh_label = self.query_one("#last-refresh", Label)
        if self._refreshing_since:
            spinner = self.SPINNER_FRAMES[self._status_frame]
            elapsed = int((datetime.now() - self._refreshing_since).total_seconds())
            refresh_label.update(f"[yellow]{spinner}[/yellow] Refreshing… {elapsed}s")
        elif self._last_refresh_at is None:
            refresh_label.update("[dim]Updated: never[/dim]")
        else:
            # Flag a partial refresh so degraded data is visible at a glance.
            partial = (
                " [yellow]· partial[/yellow]" if self._last_refresh_partial else ""
            )
            delta = int((datetime.now() - self._last_refresh_at).total_seconds())
            if delta < 60:
                refresh_label.update(f"[dim]Updated {delta}s ago[/dim]{partial}")
            elif delta < 3600:
                refresh_label.update(
                    f"[dim]Updated {delta // 60}m ago at {self._last_refresh_at.strftime('%H:%M')}[/dim]{partial}"
                )
            else:
                refresh_label.update(
                    f"[dim]Updated {delta // 3600}h ago at {self._last_refresh_at.strftime('%H:%M')}[/dim]{partial}"
                )

    def action_quick_refresh(self) -> None:
        self._next_refresh_full = False
        self._periodic_refresh()

    def action_full_refresh(self) -> None:
        self._next_refresh_full = True
        self._periodic_refresh()

    def action_toggle_log(self) -> None:
        log = self.query_one("#log-panel", RichLog)
        log.display = not log.display

    def action_show_glance(self) -> None:
        self.query_one(TabbedContent).active = "glance-tab"

    def action_show_flow(self) -> None:
        self.query_one(TabbedContent).active = "flow-tab"

    def action_show_analyst(self) -> None:
        self.query_one(TabbedContent).active = "analyst-tab"

    def action_show_controls(self) -> None:
        self.query_one(TabbedContent).active = "controls-tab"

    def _cycle_tab(self, step: int) -> None:
        tabs = self.query_one(TabbedContent)
        try:
            i = self._TAB_IDS.index(tabs.active)
        except ValueError:
            i = 0
        tabs.active = self._TAB_IDS[(i + step) % len(self._TAB_IDS)]

    def action_next_tab(self) -> None:
        self._cycle_tab(1)

    def action_prev_tab(self) -> None:
        self._cycle_tab(-1)

    # --- write command execution (Controls view) -----------------------------

    @on(ControlsPanel.Apply)
    def _on_control_apply(self, message: ControlsPanel.Apply) -> None:
        if not self._allow_writes:
            return
        # Freeze the control until the write resolves, so the 1 s read-back
        # doesn't briefly flip it back to the pre-write value.
        self.query_one(ControlsPanel).freeze(message.control_id)
        self._send_command(message.requests, message.label, message.control_id)

    @on(ControlsPanel.Dangerous)
    async def _on_control_dangerous(self, message: ControlsPanel.Dangerous) -> None:
        if not self._allow_writes:
            return
        confirmed = await self.push_screen_wait(
            ConfirmModal(message.prompt, message.token)
        )
        if confirmed:
            # Maintenance buttons aren't stateful controls — nothing to freeze.
            self._send_command(message.requests, message.label, None)

    @work
    async def _send_command(self, requests, label: str, control_id: str | None) -> None:
        """Execute a write command list and report the outcome to the log panel.
        Always unfreezes the originating control when the write resolves."""
        logger = logging.getLogger("givenergy_modbus")
        try:
            await self.client.one_shot_command(requests)
        except Exception as exc:  # noqa: BLE001 — surface any write failure, don't crash
            logger.warning("write failed (%s): %r", label, exc)
            self.notify(f"Write failed: {label}", severity="error", timeout=5)
        else:
            logger.info("applied: %s", label)
            # Re-read so the panels reflect the new state promptly.
            self._next_refresh_full = True
            self._periodic_refresh()
        finally:
            if control_id is not None:
                self.query_one(ControlsPanel).unfreeze(control_id)
