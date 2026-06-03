# Release notes

Human-readable notes for each release. The authoritative, machine-readable record
is the set of annotated git tags (`git show v1.4.0`); this file mirrors them in one
place. For releases prior to v1.4.0, see the
[GitHub Releases](https://github.com/dewet22/givenergy-cli/releases) page.

---

# 1.4.0

When 1.0.0 went out alongside givenergy-modbus 2.0, the CLI was essentially three things: a live TUI, a register `export`, and an offline `inspect`. The releases since have been about turning it into a proper field-and-bench toolkit — the kind of thing you reach for when an inverter is misbehaving, or when you want to develop against one without having hardware on the desk. Three new capabilities stand out, and underneath them the CLI has stayed in lockstep with the modbus 2.1 line.

## Poking at registers directly: `probe`

Sometimes you need to ask the inverter a question the library doesn't know how to ask yet. The new `probe` command issues raw holding- or input-register reads over any range you like, bypassing the normal capability-driven polling entirely:

```
givenergy-cli --host 192.168.1.x probe --type hr --base 4080 --count 60 --device 0x31
```

I built this while chasing whether the HR(4080+) block holds battery-energy totals on AC-coupled models — exactly the sort of undocumented corner that's painful to investigate when you'd otherwise have to patch the library to look. It splits large ranges into 60-register chunks automatically, and reports a timeout per chunk rather than abandoning the whole probe, so a partial response still tells you something. A block that doesn't respond at all is treated as a clean "nothing here" rather than an error.

Because Modbus device addresses are conventionally written in hex, `probe` accepts either form transparently — `--device 0x31` and `--device 49` mean the same thing — with sensible bounds checking so a fat-fingered address fails cleanly instead of with a traceback.

## Developing without hardware: `capture` → `mock-server`

The more interesting addition is a two-part pipeline for working offline. `capture` records the raw wire frames of a real session to a log file (the same redacted format that's safe to attach to a bug report). The new `mock-server` then replays that capture as a faithful in-memory plant:

```
# record once, against real hardware
givenergy-cli --host 192.168.1.x capture --output plant.log --duration 60

# then, any time, with no inverter in sight
givenergy-cli mock-server --capture plant.log
givenergy-cli --host 127.0.0.1 tui          # in another terminal
```

The mock answers a real client's own `detect → load_config → refresh` sequence with correct-CRC synthesised responses, so you can point the TUI, an `export`, or a `probe` at it and get the same behaviour you'd see against the captured plant. It binds to localhost by default; pass `--bind 0.0.0.0` to expose it on the LAN for an external client. There's a `--log-level DEBUG` knob that surfaces each incoming request and outgoing response at the PDU level, which is genuinely useful when you're trying to understand a client's request pattern.

What's given me the most confidence in it is pointing the **official GivEnergy Android app** at a running `mock-server` over the LAN: the app connects and reads from it just as it would against the real hardware. Having GivEnergy's own client accept the mock's responses is about as strong a fidelity check as I could hope for, and it's been invaluable for verifying behaviour without needing to be near the physical inverter.

This leans on the `MockPlant` machinery that landed in givenergy-modbus 2.1 — the CLI just makes it a first-class command. It's read-only for now (writes are acknowledged but don't mutate state), and it replays from capture logs rather than the JSON `export` format, so it's most useful for reproducing a known plant's read behaviour rather than as a fully interactive simulator.

## Keeping pace with modbus 2.1

The CLI now tracks the 2.1 library line, which brought a more honest failure model: a poll that only partly succeeds no longer looks like a generic timeout. `export` reports exactly which register reads dropped — useful given how flaky the inverter's local Modbus link can be — while still writing whatever data did come back.

## Everything else

The PyPI listing now carries proper homepage, repository, and issue links, and the README has been reworked to document the full command set in a sensible order.

---

**Full changelog:** 1.0.0 → 1.4.0
