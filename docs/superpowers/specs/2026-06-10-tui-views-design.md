# TUI views restructure — Glance / Flow / Analyst (phase 1)

Status: approved · Date: 2026-06-10

## Goal

Mirror the four HASS dashboards in the TUI. This phase delivers the view
structure and the two read-only gaps; the Controls (write) view is a separate
follow-up project with its own safety design.

| View | Content | Source |
|------|---------|--------|
| Glance (`1`, default) | status sentence · Solar today / Battery SOC / House today headlines · chip row | new `givenergy_cli/glance.py` |
| Flow (`2`) | three headline tiles above the animated Topology diagram | tiles new; Topology existing |
| Analyst (`3`) | EnergyBalance · InverterPanel · BatteryPanel | moved from the old Live tab |

The old "Energy" tab was a placeholder and is removed.

## Design

**`givenergy_cli/glance.py`** (new module; app.py is ~1.2k lines already):

- `flow_status(pv, grid, battery, idle=0.05) -> tuple[str, str]` — pure
  function returning (sentence, colour) using the same thresholds as
  Topology's flow classification, so diagram and sentence always agree.
  States: exporting / importing / battery-discharging / charging-from-solar /
  idle.
- `GlancePanel(Static)` — status line (`● sentence`), three headline figures
  (SOLAR TODAY kWh + per-string kW, BATTERY combined % + per-battery
  serial/SOC, HOUSE TODAY kWh + grid kW with direction), chip row (batteries
  online, per-battery SOC, imported/exported today, per-string kW). Follows
  the established `refresh_from(plant)` 1-second update pattern. All
  device-derived strings escaped (`rich.markup.escape`, audit L-2).
- Flow tiles — same data accessors as the Glance sub-lines, rendered as a
  horizontal tile row for the Flow tab.

**`givenergy_cli/app.py`**:

- `compose()` builds `TabbedContent(initial="glance-tab")` with the three
  panes; bindings `1/2/3` → `action_show_glance/flow/analyst`; `r`/`R`/`l`/`q`
  unchanged; `_update_panels` extended to the new widgets.
- The export/import/charge/discharge classification currently inlined in
  `Topology._compose_diagram` is extracted so `flow_status` and the diagram
  share it (no visual change to the diagram).

Data comes entirely from existing fields: `p_pv1/p_pv2`, `p_grid_out`
(signed, +export), `p_battery` (signed), `p_load_demand`, `battery_soc`,
`e_pv1_day+e_pv2_day`, `e_grid_in/out_day`, `e_load_day`,
`plant.batteries[i].serial_number/.soc`. No modbus changes.

## Testing

- `tests/test_glance.py`: unit tests for `flow_status` (five states +
  threshold edges); Textual Pilot smoke test with a monkeypatched fake
  `Client` (connect raises; on_mount tolerates it) asserting the three panes
  exist, Glance is the default, and `2`/`3`/`1` switch the active tab.

## Out of scope

Controls view (write surface — own spec), Analyst enrichment (24 h chart,
cell-balance grids), modbus changes.
