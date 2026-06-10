# GivEnergy CLI

<p align="center"><img src="logo.png" alt="GivEnergy" width="320"></p>

<!-- [![CI](https://img.shields.io/github/checks-status/dewet22/givenergy-cli/main)](https://github.com/dewet22/givenergy-cli/actions?query=branch%3Amain) -->
[![license](https://img.shields.io/github/license/dewet22/givenergy-cli)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

A command-line tool for monitoring and exporting data from GivEnergy inverters over the local network, built on [`givenergy-modbus`](https://github.com/dewet22/givenergy-modbus).

## Requirements

- Python 3.14+
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

Four views, mirroring the layout of the companion Home Assistant dashboards, plus a modbus log panel and a status bar showing connection state and the time of the last refresh:

- **Glance** (default) — headline summary: a flow-status sentence, Solar today / Battery SOC / Home now figures, and a chip row with per-battery SOC and today's import/export.
- **Flow** — three "now" tiles above the animated power-flow topology.
- **Analyst** — the data-heavy widgets: energy balance ledger, Inverter panel (with collapsible Charge/Discharge Slot sections), and Battery panel.
- **Controls** — send commands to the inverter: enable/disable charge & discharge, charge target SOC, SOC reserve, and charge/discharge time slots. Everyday changes apply immediately (mirroring the app); the disruptive operations (reboot, SOC recalibration) require typing a confirmation token. **Read-only unless the TUI is launched with `--allow-writes`.** Single-phase inverters only for now — three-phase and EMS use different write paths that haven't been implemented or hardware-tested, so those models get a notice instead of controls.

The status indicator cycles through `Connecting…` / `Probing…` / `Reconnecting…` / `● Connected` / `● Disconnected` to show the live transport state; an automatic reconnect is attempted whenever the connection drops.

The plant topology is detected once and cached per host, so subsequent launches skip the slow probe and paint near-immediately (a cheap re-check runs in the background to catch hardware changes). Pass `--redetect` to force a full detection and refresh the cache after changing hardware.

| Key       | Action                                                       |
|-----------|--------------------------------------------------------------|
| `1`–`4`   | Switch view: Glance / Flow / Analyst / Controls              |
| `r`       | Refresh now (re-reads instantaneous measurements)            |
| `Shift+R` | Full refresh — also re-reads the holding-register config blocks |
| `l`       | Toggle the modbus log panel                                  |
| `q`       | Quit                                                         |

### `capture` — record wire frames for a bug report

```bash
uv run givenergy-cli --host 192.168.x.x capture --output frames.log --duration 60
```

Connects and records raw redacted Modbus frames for the given duration (default 60 s). Writes one line per frame: a UTC timestamp and the hex payload. Serials are redacted in every frame the library can decode; a frame it can't decode is passed through untouched, so it's worth a quick skim before attaching the file to a public issue.

`--duration` is wall-clock coverage: the capture reconnects automatically across dropped connections and quiet stretches (no register updates for `--reconnect-after` seconds, default 60; `0` disables the quiet check), writing every segment to the one file with a `# … reconnected after Ns gap` comment marker on each resumption.

### `probe` — read an arbitrary register range

```bash
uv run givenergy-cli --host 192.168.x.x probe --type hr --base 4080 --count 60 --device 0x31
```

Issues raw Modbus read requests, bypassing the normal capability-driven polling. Useful for exploring undocumented register blocks — e.g. checking whether HR(4080+) holds battery energy totals on AC-coupled models. Requests are split into 60-register chunks automatically; timeouts are reported per-chunk rather than aborting the whole probe.

| Option | Description | Default |
|--------|-------------|---------|
| `--type` / `-t` | Register bank: `hr` (holding) or `ir` (input) | required |
| `--base` / `-b` | First register address (decimal or `0x`-hex) | required |
| `--count` / `-n` | Number of registers to read | `60` |
| `--device` / `-d` | Modbus device address, decimal or `0x`-hex (e.g. `0x11` inverter, `0x31` AC) | `0x11` |

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

### `mock-server` — serve a fake plant from recorded captures

```bash
uv run givenergy-cli mock-server --capture plant.log
# then, in another terminal:
uv run givenergy-cli --host 127.0.0.1 tui
```

Replays one or more `capture` logs as a faithful in-memory plant, answering a real client's `detect` / `load_config` / `refresh` sequence with synthesized, correct-CRC responses — so you can drive `tui`, `export`, or `probe` against it with no hardware. Reads of an absent register bank return the same error shape real hardware uses. Seed files come from the [`capture`](#capture--record-wire-frames-for-a-bug-report) command.

| Option | Description | Default |
|--------|-------------|---------|
| `--capture` / `-c` | Capture `.log` file(s) to seed from (repeatable) | required |
| `--bind` | Bind address (use `0.0.0.0` to expose on the LAN) | `127.0.0.1` |
| `--port` | Bind port | `8899` |

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
    __main__.py    — Typer entry point (tui / capture / probe / export / inspect / mock-server subcommands)
    app.py         — Textual TUI app
    capture.py     — frame-capture logic for bug reports
    mock.py        — mock-plant server for offline testing
    registers.py   — export, load, and rich-formatted display of register dumps
tests/
    fixtures/      — anonymised plant JSON fixtures (good + bad-enum cases)
```

## Security

A few deliberate properties worth knowing:

- **Read-only by default.** Every command only ever *reads* registers, with one deliberate exception: the TUI's Controls view can issue writes, and only when launched with `--allow-writes`. Without that flag, no code path sends a Modbus write to the inverter.
- **The local Modbus-TCP interface is unauthenticated and unencrypted.** That's a property of the GivEnergy hardware, not something this tool can change: anyone who can reach port 8899 can read from the inverter. Keeping the inverter on a segmented network (VLAN/firewall) is the control that matters, and it's yours to apply.
- **Sharing is redaction-aware.** `export` redacts serial numbers by default (pass `--no-redact` for raw data); `capture` redacts on a best-effort, per-frame basis (see the note above).

To report a vulnerability, see [SECURITY.md](SECURITY.md). A point-in-time audit of the codebase lives in [SECURITY_AUDIT.md](SECURITY_AUDIT.md).

## Dependencies

| Package | Purpose |
|---------|---------|
| `givenergy-modbus` | Modbus TCP client and data model for GivEnergy inverters |
| `textual` | Terminal UI framework |
| `typer` | CLI argument parsing |
| `rich` | Console formatting for `export` / `capture` / `inspect` output |
