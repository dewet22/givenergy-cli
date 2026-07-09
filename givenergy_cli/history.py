"""In-memory time-series of plant snapshots.

Two parallel rings of recent readings:

- `PlantSnapshot` / power history — instantaneous `p_*` registers, used for
  short-window smoothing of noisy values.
- `EnergyCounters` / energy history — monotonic `e_*` registers, used to
  derive average power over a window by differencing. Free of register-bank
  sampling skew, but resolution is bounded by the 0.1 kWh counter step
  (≈1.2 kW over 5 min, ≈0.2 kW over 30 min).

Neither is persistent: contents are lost on TUI restart.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Generic, TypeVar

from givenergy_modbus.model.plant import Plant

# History takes any record with an `at: datetime` attribute. We can't bound
# the TypeVar to a Protocol of `at: datetime` cleanly: mypy refuses to treat
# slotted frozen dataclasses as Protocol subtypes (a known interaction), so
# we leave T unbound and access `.at` via getattr inside the class.
T = TypeVar("T")


def _first_field(obj: object, *names: str) -> float | None:
    """First non-None attribute among *names*, or None if all absent/None.
    Bridges field renames across givenergy-modbus versions."""
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return None


@dataclass(frozen=True, slots=True)
class PlantSnapshot:
    """One refresh's instantaneous plant state, normalised to display units."""

    at: datetime
    pv: float  # kW
    grid: float  # kW (positive = export to grid, negative = import)
    load: float  # kW
    battery: float  # kW (positive = discharge, negative = charge)
    eps: float  # kW
    soc: int | None  # %
    t_heatsink: float | None  # °C
    t_charger: float | None  # °C
    t_battery: float | None  # °C

    @classmethod
    def from_plant(
        cls, plant: Plant, at: datetime | None = None
    ) -> PlantSnapshot | None:
        inv = plant.inverter
        if not inv.model:
            return None
        return cls(
            at=at or datetime.now(),
            pv=((inv.p_pv1 or 0) + (inv.p_pv2 or 0)) / 1000.0,
            grid=(inv.p_grid_out or 0) / 1000.0,
            load=(inv.p_load_demand or 0) / 1000.0,
            battery=(inv.p_battery or 0) / 1000.0,
            eps=(inv.p_backup or 0) / 1000.0,
            soc=inv.battery_soc,
            t_heatsink=inv.t_inverter_heatsink,
            t_charger=inv.t_charger,
            t_battery=inv.t_battery,
        )

    @property
    def in_total(self) -> float:
        """Power entering the inverter (sources side)."""
        return self.pv + max(0.0, -self.grid) + max(0.0, self.battery)

    @property
    def out_total(self) -> float:
        """Power leaving the inverter (sinks side). ``load`` (p_load_demand)
        already includes the EPS branch — corpus-confirmed — so ``eps`` is
        not added on top."""
        return self.load + max(0.0, self.grid) + max(0.0, -self.battery)

    @property
    def imbalance(self) -> float:
        """Signed ``in_total − out_total``. Includes non-atomic sampling skew
        across modbus register banks; only meaningful when averaged over a
        window of several samples."""
        return self.in_total - self.out_total


@dataclass(frozen=True, slots=True)
class EnergyCounters:
    """One refresh's cumulative energy counters (kWh).

    Most fields are lifetime cumulative; the ``*_day`` ones reset at the
    inverter's local midnight, so a window spanning that boundary will see a
    counter go backwards. `History.power_from_diff` guards against this.
    """

    at: datetime
    e_pv_total: float | None
    e_grid_in_total: float | None
    e_grid_out_total: float | None
    # Despite the name, `e_inverter_in_total` is the cumulative AC-charge
    # counter — it only ticks up while the inverter is charging the battery
    # from the grid (cf. `e_ac_charge_total` on three-phase models). Kept
    # here for future use; it's NOT a useful "total energy in" proxy.
    e_inverter_in_total: float | None
    e_inverter_out_total: float | None
    # `*_day` counters reset at the inverter's local midnight; useful for
    # "today so far" rollups but not for windows that span the boundary.
    e_pv1_day: float | None
    e_pv2_day: float | None
    e_grid_in_day: float | None
    e_grid_out_day: float | None
    # House consumption today. On single-phase this is the library's derived
    # e_consumption_today (the GE-app formula recovered in modbus #174 — the
    # old IR(35) "e_load_day" was a mislabel and actually backs AC-charge);
    # three-phase units meter it natively as e_load_today.
    e_consumption_day: float | None
    e_battery_charge_day: float | None
    e_battery_discharge_day: float | None

    @classmethod
    def from_plant(
        cls, plant: Plant, at: datetime | None = None
    ) -> EnergyCounters | None:
        inv = plant.inverter
        if not inv.model:
            return None
        return cls(
            at=at or datetime.now(),
            e_pv_total=inv.e_pv_total,
            e_grid_in_total=inv.e_grid_in_total,
            e_grid_out_total=inv.e_grid_out_total,
            e_inverter_in_total=inv.e_inverter_in_total,
            # Where cumulative inverter output lives is model-dependent:
            # single-phase hybrids expose it as e_pv_generation_total (IR45/46);
            # AC/All-in-One report None there since modbus 2.10.0, with the real
            # value in e_inverter_out_total; three-phase has a distinct native
            # e_inverter_out_total (IR1362/3) and no e_pv_generation_total. The
            # None-aware _first_field picks whichever the model actually populates.
            e_inverter_out_total=_first_field(
                inv, "e_pv_generation_total", "e_inverter_out_total"
            ),
            e_pv1_day=inv.e_pv1_day,
            e_pv2_day=inv.e_pv2_day,
            e_grid_in_day=inv.e_grid_in_day,
            e_grid_out_day=inv.e_grid_out_day,
            # Field-name bridges across givenergy-modbus versions: the battery
            # day counters became *_today_alt1 in the 2.2 LUT rework (same
            # IR(36)/IR(37) registers); consumption is e_consumption_today
            # (single-phase, derived) or e_load_today (three-phase, metered).
            # Absent fields become None, which every consumer renders as "—".
            e_consumption_day=_first_field(inv, "e_consumption_today", "e_load_today"),
            e_battery_charge_day=_first_field(
                inv, "e_battery_charge_day", "e_battery_charge_today_alt1"
            ),
            e_battery_discharge_day=_first_field(
                inv, "e_battery_discharge_day", "e_battery_discharge_today_alt1"
            ),
        )

    @property
    def pv_day(self) -> float | None:
        """Combined PV today across both strings."""
        if self.e_pv1_day is None and self.e_pv2_day is None:
            return None
        return (self.e_pv1_day or 0.0) + (self.e_pv2_day or 0.0)

    @property
    def cum_in(self) -> float | None:
        """Cumulative energy entering the inverter from all sources
        (PV + grid import + battery discharge). Returns ``None`` if any
        component is missing. Note: `_day` components reset at midnight, so a
        diff that spans midnight will go briefly negative until the day
        counters accumulate past their pre-midnight value."""
        a, b, c = self.e_pv_total, self.e_grid_in_total, self.e_battery_discharge_day
        if a is None or b is None or c is None:
            return None
        return a + b + c

    @property
    def cum_out(self) -> float | None:
        """Cumulative energy leaving the inverter to all sinks (consumption +
        grid export + battery charge). EPS is included in the consumption
        figure. Same midnight-reset caveat as :attr:`cum_in`."""
        a, b, c = (
            self.e_consumption_day,
            self.e_grid_out_total,
            self.e_battery_charge_day,
        )
        if a is None or b is None or c is None:
            return None
        return a + b + c


class History(Generic[T]):
    """Bounded ring buffer of timestamped snapshots."""

    def __init__(self, maxlen: int = 240) -> None:
        self._samples: deque[T] = deque(maxlen=maxlen)

    def __len__(self) -> int:
        return len(self._samples)

    @property
    def latest(self) -> T | None:
        return self._samples[-1] if self._samples else None

    def append(self, snap: T) -> None:
        self._samples.append(snap)

    def window(self, seconds: float) -> list[T]:
        """Snapshots whose timestamp is within ``seconds`` of the most recent.
        Records are expected to expose an ``at: datetime`` attribute."""
        if not self._samples:
            return []
        latest_at: datetime = getattr(self._samples[-1], "at")
        cutoff = latest_at - timedelta(seconds=seconds)
        return [s for s in self._samples if getattr(s, "at") >= cutoff]

    def mean(self, field: str, seconds: float) -> float | None:
        """Mean of ``field`` across the trailing window. Returns ``None`` when
        no usable samples are available."""
        samples = self.window(seconds)
        vals = [getattr(s, field) for s in samples if getattr(s, field) is not None]
        if not vals:
            return None
        return sum(vals) / len(vals)

    def diff(self, field: str, seconds: float) -> tuple[float, float] | None:
        """Raw (delta_value, delta_seconds) across the trailing window.

        Returns ``None`` when the window has fewer than two samples, either
        endpoint is ``None``, or the elapsed time is non-positive.
        """
        samples = self.window(seconds)
        if len(samples) < 2:
            return None
        first = samples[0]
        last = samples[-1]
        v0 = getattr(first, field)
        v1 = getattr(last, field)
        if v0 is None or v1 is None:
            return None
        dt = (getattr(last, "at") - getattr(first, "at")).total_seconds()
        if dt <= 0:
            return None
        return (v1 - v0, dt)

    def power_from_diff(self, field: str, seconds: float) -> float | None:
        """Average power (kW) derived from differencing a cumulative energy
        counter (kWh) across the trailing window. Returns ``None`` when the
        diff is unavailable or the counter went backwards (daily reset).
        """
        d = self.diff(field, seconds)
        if d is None:
            return None
        delta_e, dt = d
        if delta_e < 0:
            return None
        return delta_e / (dt / 3600)


_SPARKLINE_BLOCKS = " ▁▂▃▄▅▆▇█"  # 9 levels: space (0) + 8 block heights


def sparkline_centred(
    values: list[float],
    width: int = 8,
    vmin_neg: float | None = None,
    vmax_pos: float | None = None,
) -> tuple[str, str]:
    """Render a double-height signed sparkline centred at zero.

    Returns ``(top_row, bottom_row)`` — two strings of ``width`` visible
    cells each. Positive values render in the top row using ``▁▂▃▄▅▆▇█``
    (bar grows from the row's bottom upward, away from the baseline that
    sits at the boundary between rows). Negative values render in the
    bottom row using the same lower-block characters wrapped in Rich
    ``[reverse]`` markup — reversing swaps fg/bg so the ink visually moves
    to the top of the cell (the baseline), giving 8-level resolution per
    direction with the bar growing from the baseline downward.

    The non-active row for each cell is a space. Negative cells emit
    ``[reverse]…[/reverse]`` markup; the caller wraps both rows in a colour
    span so the nested reverse picks up that fg as its inverted bg.
    """
    blank = " " * width
    if len(values) < 2:
        return (blank, blank)
    if vmax_pos is None:
        vmax_pos = max(max(values), 0.0)
    if vmin_neg is None:
        vmin_neg = min(min(values), 0.0)
    pos_scale = max(vmax_pos, 1e-3)
    neg_scale = max(-vmin_neg, 1e-3)
    if pos_scale < 1e-3 and neg_scale < 1e-3:
        return (blank, blank)

    if len(values) <= width:
        bucketed = list(values)
        leading_pad = width - len(values)
    else:
        per = len(values) / width
        bucketed = []
        for i in range(width):
            start = int(i * per)
            end = max(start + 1, int((i + 1) * per))
            chunk = values[start:end]
            bucketed.append(sum(chunk) / len(chunk))
        leading_pad = 0

    up = "▁▂▃▄▅▆▇█"  # bottom-up fill, 8 levels
    # Negative side: lower-block chars in reverse order with [reverse]
    # styling. Cap at 7/8 (▁ reversed) so it stays visually distinct from a
    # full-block positive cell.
    down = "▇▆▅▄▃▂▁"
    top: list[str] = [" "] * leading_pad
    bot: list[str] = [" "] * leading_pad
    for v in bucketed:
        if v > 0:
            idx = min(8, int(v / pos_scale * 8) + 1)
            top.append(up[idx - 1])
            bot.append(" ")
        elif v < 0:
            idx = min(7, int(-v / neg_scale * 7) + 1)
            top.append(" ")
            bot.append(f"[reverse]{down[idx - 1]}[/reverse]")
        else:
            top.append(" ")
            bot.append(" ")
    return ("".join(top), "".join(bot))


def sparkline(
    values: list[float],
    width: int = 8,
    vmin: float | None = None,
    vmax: float | None = None,
) -> str:
    """Render a sparkline of ``values`` into exactly ``width`` cells using
    Unicode block characters.

    Buckets larger inputs by averaging adjacent samples. With fewer values
    than ``width``, the sparkline is left-padded with spaces so the latest
    samples sit on the right. Returns all spaces when there's no signal
    (fewer than 2 points, or all values equal).

    Defaults: ``vmin`` is min(values, 0) so a flat-zero series renders blank;
    ``vmax`` is max(values). Pass explicit bounds (e.g. 0/100 for SOC) when
    you want stable scaling across renders.
    """
    if len(values) < 2:
        return " " * width
    if vmin is None:
        vmin = min(min(values), 0.0)
    if vmax is None:
        vmax = max(values)
    span = vmax - vmin
    if span <= 0:
        return " " * width

    if len(values) <= width:
        bucketed = list(values)
        leading_pad = width - len(values)
    else:
        per = len(values) / width
        bucketed = []
        for i in range(width):
            start = int(i * per)
            end = max(start + 1, int((i + 1) * per))
            chunk = values[start:end]
            bucketed.append(sum(chunk) / len(chunk))
        leading_pad = 0

    out = [" "] * leading_pad
    for v in bucketed:
        idx = int((v - vmin) / span * 8)
        out.append(_SPARKLINE_BLOCKS[max(0, min(8, idx))])
    return "".join(out)
