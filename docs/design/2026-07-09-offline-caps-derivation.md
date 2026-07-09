# Offline capability derivation from captures — design

- **Date:** 2026-07-09
- **Status:** Design approved; ready to implement (modbus 2.10.3 ships `Plant.from_caches()`).

## Context

`load_capture` (`givenergy_cli/registers.py`) reconstructs a `Plant` from a saved file for the
`inspect` and `shell` commands. It auto-detects two formats:

- **`export` JSON** → `load_plant()`, which deserialises the full plant *including* its
  `PlantCapabilities`, so typed views (`.inverter`, `.ems`, batteries) resolve.
- **`probe --compact` dump** → `_build_plant(caches, capabilities=None)` — **capability-less**.
  Without capabilities the inverter model can't be resolved, so typed views are limited and the
  offline plant is little more than raw register tables.

modbus's #268 epic (shipped 2.9.0, complete 2.9.2) closed this on the library side:
`Plant.from_caches(register_caches)` builds a **fully-typed, fully-capability'd** plant from a cache
set with no wire I/O, deriving capabilities via `_derive_capabilities` — a path a modbus parity test
pins **equal to a live `detect()`** over the same captures. `parse_compact()` already returns the
exact `dict[int, RegisterCache]` that `from_caches` consumes, so adoption is a drop-in.

## Goals

- A `probe --compact` dump that contains the inverter identity block derives full capabilities
  offline, so `inspect`/`shell` expose the same typed views as a live-detected plant.
- The new-hardware probing use case — a partial range dump (e.g. `probe --base 4080`) with no
  identity register — keeps working: raw register tables still render.
- Surgical: one branch in `load_capture`; no new command, flag, or subsystem.

## Non-goals

- The `export` JSON path — already carries capabilities; left untouched.
- Seeding the per-host `capabilities_cache` from a capture (pre-warming a live TUI cold start) —
  considered and dropped as niche.
- `mock-server` — unchanged.

## Design

In `load_capture`, the probe-dump branch changes from an unconditional capability-less build to a
derive-with-narrow-fallback:

```python
caches = parse_compact(text)
if caches:
    try:
        return Plant.from_caches(caches)
    except CommunicationError:
        # No inverter identity register (HR(0)@0x11) in this dump — e.g. a partial
        # range probe of new hardware. Fall back to raw registers, which are the point.
        logger.debug("capture %s lacks an identity register; capabilities not derived", path)
        return _build_plant(caches, capabilities=None)
raise ValueError(f"not a recognised plant export or probe dump: {path}")
```

Only `CommunicationError` — modbus's documented "can't resolve device type" signal — triggers the
fallback. A **malformed** peripheral cache is left to propagate: modbus's offline path deliberately
fails loud on a genuinely corrupt dump, and the CLI honours that rather than masking it.

`CommunicationError` is imported from `givenergy_modbus.exceptions`.

## Error handling

- **Missing identity register** → `CommunicationError` caught → capability-less fallback + debug log.
  Identical user-visible result to today for partial dumps (raw tables), plus a debug breadcrumb.
- **Malformed dump** (a peripheral cache that fails `is_valid`/decode) → the underlying error
  propagates out of `from_caches`. Fail-loud, matching modbus's offline contract; not swallowed.

## Testing

- Probe dump **with** the identity block (0x11 carrying HR(0)) → `from_caches` derives; assert
  `plant.capabilities is not None` and a typed view (`plant.inverter.model`) resolves.
- Partial probe dump **without** 0x11 → `CommunicationError` path → `plant.capabilities is None`,
  raw register decoding still succeeds (no raise).
- Reuse existing capture fixtures; add a trimmed identity-less fixture if none already lacks 0x11.
