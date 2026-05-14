# GivEnergy CLI

<p align="center"><img src="logo.png" alt="GivEnergy" width="320"></p>

<!-- [![CI](https://img.shields.io/github/checks-status/dewet22/givenergy-cli/main)](https://github.com/dewet22/givenergy-cli/actions?query=branch%3Amain) -->
[![license](https://img.shields.io/github/license/dewet22/givenergy-cli)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

A command-line tool for monitoring and exporting data from GivEnergy inverters over the local network, built on [`givenergy-modbus`](https://github.com/dewet22/givenergy-modbus).

## Requirements

- Python 3.13+
- A GivEnergy inverter accessible on the local network

## Installation

```bash
uv sync
```

After `uv sync` the `givenergy-cli` command is available at `.venv/bin/givenergy-cli`, or via `uv run givenergy-cli`.

## Usage

`host` and `port` are shared options on the root command. They can also be supplied via the `GIVENERGY_HOST` / `GIVENERGY_PORT` environment variables.

### `tui` — interactive terminal UI

```bash
uv run givenergy-cli --host 192.168.x.x tui
```

Live Inverter / Power Flow / Battery panels, a modbus log panel, and a status bar showing connection state and the time of the last refresh. The Inverter panel has collapsible Charge Slot and Discharge Slot sections. The status indicator cycles through `Connecting…` / `Probing…` / `Reconnecting…` / `● Connected` / `● Disconnected` to show the live transport state; an automatic reconnect is attempted whenever the connection drops.

| Key       | Action                                                       |
|-----------|--------------------------------------------------------------|
| `r`       | Refresh now (re-reads instantaneous measurements)            |
| `Shift+R` | Full refresh — also re-reads the holding-register config blocks |
| `l`       | Toggle the modbus log panel                                  |
| `q`       | Quit                                                         |

### `export` — dump registers to a portable JSON file

```bash
uv run givenergy-cli --host 192.168.x.x export -o plant.json
```

Connects, runs `detect` to discover the plant topology, loads the holding-register config, fetches the input registers, and writes every register from every discovered device address. Partial captures are still written on timeout, with a warning.

### `inspect` — render an exported plant

```bash
uv run givenergy-cli inspect plant.json
```

Reconstructs the `Plant` from the JSON, then prints the Inverter and Battery model fields plus per-device raw register dumps (decimal + hex). No network required.

## Environment variables

| Variable                     | Subcommand | Default        |
|------------------------------|------------|----------------|
| `GIVENERGY_HOST`             | all        | — (required)   |
| `GIVENERGY_PORT`             | all        | `8899`         |
| `GIVENERGY_REFRESH_INTERVAL` | `tui`      | `15.0`         |
| `GIVENERGY_LOG_LEVEL`        | `tui`      | `INFO`         |

## Project structure

```text
givenergy_cli/
    __init__.py
    __main__.py    — Typer entry point (tui / export / inspect subcommands)
    app.py         — Textual TUI app
    registers.py   — export, load, and rich-formatted display of register dumps
tests/
    fixtures/      — anonymised plant JSON fixtures (good + bad-enum cases)
```

## Dependencies

| Package | Purpose |
|---------|---------|
| `givenergy-modbus` | Modbus TCP client and data model for GivEnergy inverters |
| `textual` | Terminal UI framework |
| `typer` | CLI argument parsing |
| `rich` | Console formatting for `export` / `inspect` output |
