# givenergy-cli

A terminal UI for monitoring and controlling GivEnergy inverters over the local network, built on [`givenergy-modbus`](https://github.com/dewet22/givenergy-modbus).

## Requirements

- Python 3.13+
- A GivEnergy inverter accessible on the local network

## Installation

```bash
uv sync
```

## Usage

```bash
uv run givenergy-cli
uv run givenergy-cli --host 192.168.x.x
uv run givenergy-cli --host 192.168.x.x --port 8899
```

After `uv sync` the `givenergy-cli` command is available directly inside the venv (`.venv/bin/givenergy-cli`) without the `uv run` prefix.

### Key bindings

| Key | Action |
|-----|--------|
| `r` | Force a full data refresh |
| `c` | Initiate battery SOC calibration |
| `q` | Quit |

## Project structure

```
givenergy_cli/
    __init__.py
    __main__.py   — CLI entry point (Typer); accepts --host / --port
    app.py        — Textual TUI app
```

### `__main__.py`

Typer handles the `--host` / `--port` CLI arguments and passes them into the Textual app, then calls `.run()` to start it.

### `app.py`

Contains four classes:

**`InverterPanel`** — left column. Shows identity and thermal info: model, serial numbers, firmware, status, heatsink/charger temps, uptime.

**`PowerFlowPanel`** — centre column. Shows real-time watts for PV, grid, load, and battery. Grid and battery are signed (`+`/`-`) to indicate import/export and charge/discharge direction.

**`BatteryPanel`** — right column. Shows a live SOC progress bar, plus temp, voltage, current, and battery count.

**`GivEnergyApp`** — the root app. Owns the `Client` instance and orchestrates data flow:
- On mount, fires `_connect_and_refresh()` as a background `@work` task (full register read)
- `set_interval(15, ...)` drives a lightweight partial refresh every 15 seconds while connected
- `_update_panels()` pushes the latest `plant` data into each of the three panels
- Key bindings trigger manual refresh, SOC calibration, and quit

**Data flow:** `Client` → `plant` → each panel's `refresh_from(plant)` → widget labels and progress bar update in place.

Each panel inherits from `Static` and owns its `DEFAULT_CSS` (border, padding, width), keeping layout and style co-located with the widget.

## Dependencies

| Package | Purpose |
|---------|---------|
| `givenergy-modbus` | Modbus TCP client and data model for GivEnergy inverters |
| `textual` | Terminal UI framework |
| `typer` | CLI argument parsing |
