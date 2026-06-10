"""Glance view — HASS-style headline summary — and the Flow tab's tile row.

Pure presentation: everything reads the already-decoded ``Plant`` via the same
``refresh_from(plant)`` 1-second tick the other panels use. The flow-state
classification lives here (``flow_status``) and Topology shares its idle
threshold, so the headline sentence and the diagram never disagree.
"""

from __future__ import annotations

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

from givenergy_modbus.model.battery import Battery
from givenergy_modbus.model.plant import Plant

from givenergy_cli.registers import _decode_batteries

# Below this threshold (kW) a flow is treated as idle. Shared with Topology.
IDLE_THRESHOLD = 0.05


def flow_status(
    pv: float, grid: float, battery: float, idle: float = IDLE_THRESHOLD
) -> tuple[str, str]:
    """Classify instantaneous flows (kW) into a (sentence, colour) headline.

    Sign conventions match the inverter registers: ``grid`` positive = export,
    ``battery`` positive = discharge. Grid activity headlines over battery
    activity — it's the flow the user is billed for.
    """
    if grid > idle:
        if pv > idle:
            return "Solar ahead of demand — exporting to the grid.", "green"
        return "Exporting to the grid.", "green"
    if grid < -idle:
        return "Importing from the grid.", "red"
    if battery > idle:
        return "Battery covering demand.", "magenta"
    if battery < -idle:
        if pv > idle:
            return "Charging battery from solar.", "cyan"
        return "Charging battery.", "cyan"
    return "Idle — no significant power flow.", "dim"


def _kw(watts: float | None) -> str:
    return f"{(watts or 0) / 1000.0:.2f} kW"


def _kwh(value: float | None) -> str:
    return f"{value:.1f}" if value is not None else "—"


def _per_string_line(inv, sep: str = " · ") -> str:
    return sep.join(
        f"String {n}: {_kw(p)}" for n, p in ((1, inv.p_pv1), (2, inv.p_pv2))
    )


def _valid_batteries(plant: Plant) -> list[Battery]:
    """Decoded batteries with a plausible serial, tolerant of the hardware's
    transient bad register values (`_decode_batteries` already sanitises)."""
    return [
        item
        for _slot, _addr, item in _decode_batteries(plant)
        if isinstance(item, Battery) and item.is_valid()
    ]


def _per_battery_line(batteries: list[Battery], sep: str = " · ") -> str:
    if not batteries:
        return "[dim]no batteries decoded[/dim]"
    return sep.join(
        f"{escape(str(b.serial_number))}: {b.soc if b.soc is not None else '—'}%"
        for b in batteries
    )


def _grid_line(inv) -> str:
    grid = (inv.p_grid_out or 0) / 1000.0
    if grid > IDLE_THRESHOLD:
        return f"Exporting {grid:.2f} kW"
    if grid < -IDLE_THRESHOLD:
        return f"Importing {-grid:.2f} kW"
    return "Grid balanced"


class GlancePanel(Static):
    """The headline view: status sentence, three big figures, chip row."""

    DEFAULT_CSS = """
    /* Static auto-sizes to its own (empty) renderable, not its children —
       width/height must be explicit or the whole panel collapses to 0x0. */
    GlancePanel { padding: 1 4; width: 1fr; height: 1fr; }
    GlancePanel #glance-status { height: 2; width: 1fr; text-style: bold; }
    GlancePanel #glance-figures { height: auto; width: 1fr; }
    GlancePanel .glance-figure {
        width: 1fr;
        height: auto;
        border: round $accent 30%;
        padding: 1 2;
        margin-right: 2;
    }
    GlancePanel #glance-chips { margin-top: 1; width: 1fr; color: $text-muted; }
    """

    def compose(self) -> ComposeResult:
        yield Static("[dim]Waiting for first refresh…[/dim]", id="glance-status")
        with Horizontal(id="glance-figures"):
            yield Static(id="glance-solar", classes="glance-figure")
            yield Static(id="glance-battery", classes="glance-figure")
            yield Static(id="glance-house", classes="glance-figure")
        yield Static(id="glance-chips")

    def refresh_from(self, plant: Plant) -> None:
        inv = plant.inverter
        if not inv.model:
            return

        pv = ((inv.p_pv1 or 0) + (inv.p_pv2 or 0)) / 1000.0
        grid = (inv.p_grid_out or 0) / 1000.0
        battery = (inv.p_battery or 0) / 1000.0
        sentence, colour = flow_status(pv, grid, battery)
        self.query_one("#glance-status", Static).update(
            f"[{colour}]●[/{colour}] {sentence}"
        )

        pv_day = (
            (inv.e_pv1_day or 0) + (inv.e_pv2_day or 0)
            if (inv.e_pv1_day is not None or inv.e_pv2_day is not None)
            else None
        )
        self.query_one("#glance-solar", Static).update(
            f"[dim]SOLAR TODAY[/dim]\n\n"
            f"[bold]{_kwh(pv_day)}[/bold] kWh\n\n"
            f"{_per_string_line(inv)}"
        )

        batteries = _valid_batteries(plant)
        soc = inv.battery_soc
        self.query_one("#glance-battery", Static).update(
            f"[dim]BATTERY[/dim]\n\n"
            f"[bold]{soc if soc is not None else '—'}[/bold] %\n\n"
            f"{_per_battery_line(batteries)}"
        )

        # givenergy-modbus 2.2 dropped the e_load_day counter with no successor,
        # so the house figure shows instantaneous demand rather than a daily
        # total (imported/exported today still appear in the chip row).
        load = (inv.p_load_demand or 0) / 1000.0
        self.query_one("#glance-house", Static).update(
            f"[dim]HOME · NOW[/dim]\n\n[bold]{load:.2f}[/bold] kW\n\n{_grid_line(inv)}"
        )

        chips = [
            f"[green]●[/green] {len(batteries)} "
            f"{'battery' if len(batteries) == 1 else 'batteries'} online",
            f"[yellow]●[/yellow] {_kwh(inv.e_grid_in_day)} kWh imported today",
            f"[green]●[/green] {_kwh(inv.e_grid_out_day)} kWh exported today",
            f"[blue]●[/blue] {_per_string_line(inv, sep='   [blue]●[/blue] ')}",
        ]
        self.query_one("#glance-chips", Static).update("   ".join(chips))


class TileRow(Static):
    """Three compact 'now' tiles above the Flow diagram (HASS flow header)."""

    DEFAULT_CSS = """
    TileRow { height: auto; width: 1fr; margin-bottom: 1; }
    /* Horizontal defaults to 1fr height, which would inflate the row to fill
       the tab and shove the Topology below it off-screen. */
    TileRow Horizontal { height: auto; width: 1fr; }
    TileRow .flow-tile {
        width: 1fr;
        border: round $accent 30%;
        padding: 0 2;
        margin-right: 2;
        height: auto;
    }
    """

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Static(id="tile-solar", classes="flow-tile")
            yield Static(id="tile-battery", classes="flow-tile")
            yield Static(id="tile-home", classes="flow-tile")

    def refresh_from(self, plant: Plant) -> None:
        inv = plant.inverter
        if not inv.model:
            return
        pv = ((inv.p_pv1 or 0) + (inv.p_pv2 or 0)) / 1000.0
        self.query_one("#tile-solar", Static).update(
            f"[dim]SOLAR · NOW[/dim]\n[bold]{pv:.2f}[/bold] kW\n{_per_string_line(inv)}"
        )
        soc = inv.battery_soc
        self.query_one("#tile-battery", Static).update(
            f"[dim]BATTERY · SOC[/dim]\n"
            f"[bold]{soc if soc is not None else '—'}[/bold] %\n"
            f"{_per_battery_line(_valid_batteries(plant))}"
        )
        load = (inv.p_load_demand or 0) / 1000.0
        self.query_one("#tile-home", Static).update(
            f"[dim]HOME · NOW[/dim]\n[bold]{load:.2f}[/bold] kW\n{_grid_line(inv)}"
        )
