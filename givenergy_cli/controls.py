"""Controls view — send inverter write commands from the TUI.

Two layers:

- a small **phase-aware dispatch** that maps a UI intent to the correct
  ``givenergy_modbus.client.commands`` builder for the detected topology
  (single-phase / three-phase / EMS), raising ``UnsupportedControl`` rather
  than mis-writing when an intent doesn't apply; and
- the **widgets** (``ControlsPanel`` + a type-to-confirm ``ConfirmModal``) that
  read current state back from the plant and emit the dispatched command list to
  the app, which executes it.

Everyday controls (enable toggles, charge target, SOC reserve, slot times) apply
immediately, mirroring the Home Assistant dashboard; only the disruptive
operations (reboot, SOC recalibration) go through the confirm modal.
"""

from __future__ import annotations

from datetime import time as dt_time

from givenergy_modbus.client import commands
from givenergy_modbus.model.plant import Plant, PlantCapabilities
from givenergy_modbus.model.slot_map import (
    EMS_SLOTS,
    EXTENDED_SLOTS,
    SINGLE_PHASE_SLOTS,
    THREE_PHASE_SLOTS,
    SlotMap,
)
from givenergy_modbus.pdu import TransparentRequest
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Switch

Requests = list[TransparentRequest]

# Sensible inverter-wide bounds; the library clamps too, but rejecting here keeps
# an obviously-wrong value from ever hitting the wire.
_SOC_MIN, _SOC_MAX = 4, 100


class UnsupportedControl(Exception):
    """The requested control isn't available for the detected device topology."""


# --- phase-aware dispatch -----------------------------------------------------


def slot_map_for(caps: PlantCapabilities) -> SlotMap:
    if caps.is_ems:
        return EMS_SLOTS
    if caps.is_three_phase:
        return THREE_PHASE_SLOTS
    if caps.has_extended_slots:
        return EXTENDED_SLOTS
    return SINGLE_PHASE_SLOTS


def slot_count(caps: PlantCapabilities) -> int:
    return len(slot_map_for(caps).charge_slots)


def enable_charge_cmd(on: bool) -> Requests:
    return commands.set_enable_charge(on)


def enable_discharge_cmd(on: bool) -> Requests:
    return commands.set_enable_discharge(on)


def charge_target_cmd(soc: int, caps: PlantCapabilities) -> Requests:
    if caps.is_ems:
        raise UnsupportedControl("charge target is set per-slot on EMS models")
    if caps.is_three_phase:
        return commands.set_charge_target_3ph(soc)
    return commands.set_charge_target(soc)


def soc_reserve_cmd(val: int, caps: PlantCapabilities) -> Requests:
    if caps.is_ems:
        raise UnsupportedControl("SOC reserve isn't exposed on EMS models")
    if caps.is_three_phase:
        return commands.set_battery_soc_reserve_3ph(val)
    return commands.set_battery_soc_reserve(val)


def charge_slot_start_cmd(
    idx: int, t: dt_time | None, caps: PlantCapabilities
) -> Requests:
    return commands.set_charge_slot_start(idx, t, slot_map_for(caps))


def charge_slot_end_cmd(
    idx: int, t: dt_time | None, caps: PlantCapabilities
) -> Requests:
    return commands.set_charge_slot_end(idx, t, slot_map_for(caps))


def discharge_slot_start_cmd(
    idx: int, t: dt_time | None, caps: PlantCapabilities
) -> Requests:
    return commands.set_discharge_slot_start(idx, t, slot_map_for(caps))


def discharge_slot_end_cmd(
    idx: int, t: dt_time | None, caps: PlantCapabilities
) -> Requests:
    return commands.set_discharge_slot_end(idx, t, slot_map_for(caps))


def reboot_cmd() -> Requests:
    return commands.set_inverter_reboot()


def recalibrate_cmd() -> Requests:
    return commands.set_calibrate_battery_soc()


# --- parsing helpers ----------------------------------------------------------


def parse_soc(text: str) -> int:
    """Parse a SOC percentage in [4, 100]; raise ValueError otherwise."""
    value = int(text)
    if not _SOC_MIN <= value <= _SOC_MAX:
        raise ValueError(f"must be {_SOC_MIN}–{_SOC_MAX}")
    return value


def parse_hhmm(text: str) -> dt_time | None:
    """Parse ``HH:MM`` into a time, or None for an empty field (clears the slot
    endpoint). Raises ValueError on anything else."""
    text = text.strip()
    if not text:
        return None
    h, _, m = text.partition(":")
    hour, minute = int(h), int(m)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("must be HH:MM (00:00–23:59)")
    return dt_time(hour, minute)


def _fmt_time(t: dt_time | None) -> str:
    return t.strftime("%H:%M") if t else ""


# --- confirmation modal for disruptive ops ------------------------------------


class ConfirmModal(ModalScreen[bool]):
    """Type-to-confirm dialog for disruptive commands. Dismisses True only if the
    exact *token* is typed."""

    DEFAULT_CSS = """
    ConfirmModal { align: center middle; }
    ConfirmModal #box {
        width: 60; height: auto; padding: 1 2;
        border: thick $error; background: $surface;
    }
    ConfirmModal #buttons { height: auto; margin-top: 1; }
    ConfirmModal Button { margin-right: 2; }
    """

    def __init__(self, prompt: str, token: str) -> None:
        super().__init__()
        self._prompt = prompt
        self._token = token

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Label(self._prompt)
            yield Label(f"Type [b]{self._token}[/b] to confirm:")
            yield Input(id="token", placeholder=self._token)
            with Horizontal(id="buttons"):
                yield Button("Confirm", variant="error", id="confirm")
                yield Button("Cancel", variant="primary", id="cancel")

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#confirm")
    def _confirm(self) -> None:
        typed = self.query_one("#token", Input).value.strip()
        self.dismiss(typed == self._token)

    @on(Input.Submitted, "#token")
    def _submit(self) -> None:
        self._confirm()


# --- the controls panel -------------------------------------------------------


class ControlsPanel(VerticalScroll):
    """Grouped write controls. Reads current state back from the plant; emits
    dispatched command lists for the app to execute. Inert (no command ever
    emitted) when *allow_writes* is False."""

    DEFAULT_CSS = """
    ControlsPanel { padding: 1 2; }
    ControlsPanel .group-title { text-style: bold; color: $accent; margin-top: 1; }
    ControlsPanel .row { height: 3; }
    ControlsPanel .row Label { width: 22; content-align: left middle; height: 3; }
    ControlsPanel Switch { height: 3; }
    ControlsPanel Input { width: 12; }
    ControlsPanel #writes-hint { color: $warning; margin-bottom: 1; }
    ControlsPanel.-readonly Input, ControlsPanel.-readonly Switch,
    ControlsPanel.-readonly Button { opacity: 50%; }
    """

    class Apply(Message):
        """An everyday control changed — apply *requests* immediately."""

        def __init__(self, requests: Requests, label: str) -> None:
            super().__init__()
            self.requests = requests
            self.label = label

    class Dangerous(Message):
        """A disruptive control was pressed — confirm, then apply."""

        def __init__(
            self, requests: Requests, label: str, token: str, prompt: str
        ) -> None:
            super().__init__()
            self.requests = requests
            self.label = label
            self.token = token
            self.prompt = prompt

    def __init__(self, *, allow_writes: bool, **kwargs) -> None:
        super().__init__(**kwargs)
        self._allow_writes = allow_writes
        self._readonly = not allow_writes
        self._caps: PlantCapabilities | None = None
        # Number of slot editors rendered; fixed at first sight of capabilities.
        self._slots = 0
        # Set while we push read-back values into widgets, so our own updates
        # don't echo back as user-initiated writes.
        self._syncing = False
        self._built = False

    def compose(self) -> ComposeResult:
        if not self._allow_writes:
            yield Label(
                "Read-only — relaunch with [b]--allow-writes[/b] to enable controls.",
                id="writes-hint",
            )
        # The control rows are built once capabilities are known (slot count
        # depends on the model); see _build().
        yield Vertical(id="controls-body")

    def _row(self, label: str, widget) -> Horizontal:
        return Horizontal(Label(label), widget, classes="row")

    def _switch(self, wid: str) -> Switch:
        return Switch(id=wid, disabled=self._readonly)

    def _time_input(self, wid: str) -> Input:
        return Input(id=wid, placeholder="HH:MM", disabled=self._readonly)

    def _build(self, caps: PlantCapabilities) -> None:
        """Construct the control rows for the detected topology (once). Widgets
        are constructed `disabled` in read-only mode — disabling them *after*
        mount wouldn't take, since they aren't queryable until the pump runs."""
        self._caps = caps
        self._slots = slot_count(caps)
        d = self._readonly
        body = self.query_one("#controls-body", Vertical)

        body.mount(Label("Charging", classes="group-title"))
        body.mount(self._row("Enable charge", self._switch("enable-charge")))
        body.mount(self._row("Charge target %", Input(id="charge-target", disabled=d)))
        body.mount(self._row("SOC reserve %", Input(id="soc-reserve", disabled=d)))
        for i in range(1, self._slots + 1):
            body.mount(
                self._row(f"Charge slot {i} start", self._time_input(f"cs{i}-start"))
            )
            body.mount(
                self._row(f"Charge slot {i} end", self._time_input(f"cs{i}-end"))
            )

        body.mount(Label("Discharging", classes="group-title"))
        body.mount(self._row("Enable discharge", self._switch("enable-discharge")))
        for i in range(1, self._slots + 1):
            body.mount(
                self._row(f"Discharge slot {i} start", self._time_input(f"ds{i}-start"))
            )
            body.mount(
                self._row(f"Discharge slot {i} end", self._time_input(f"ds{i}-end"))
            )

        body.mount(Label("Maintenance", classes="group-title"))
        body.mount(
            Horizontal(
                Button("Reboot inverter", variant="error", id="reboot", disabled=d),
                Button(
                    "Recalibrate SOC", variant="error", id="recalibrate", disabled=d
                ),
                classes="row",
            )
        )

        if self._readonly:
            self.add_class("-readonly")
        self._built = True

    # -- read-back sync --------------------------------------------------------

    def refresh_from(self, plant: Plant) -> None:
        inv = plant.inverter
        caps = plant.capabilities
        if not inv.model or caps is None:
            return
        if not self._built:
            self._build(caps)

        self._syncing = True
        try:
            self._sync_switch("enable-charge", inv.enable_charge)
            self._sync_switch("enable-discharge", inv.enable_discharge)
            self._sync_input("charge-target", _str_or_blank(inv.charge_target_soc))
            self._sync_input("soc-reserve", _str_or_blank(inv.battery_soc_reserve))
            for i in range(1, self._slots + 1):
                self._sync_slot("cs", i, getattr(inv, f"charge_slot_{i}", None))
                self._sync_slot("ds", i, getattr(inv, f"discharge_slot_{i}", None))
        finally:
            self._syncing = False

    def _sync_switch(self, wid: str, value) -> None:
        sw = self._maybe(Switch, wid)
        if sw is not None and value is not None and sw.value != bool(value):
            sw.value = bool(value)

    def _sync_input(self, wid: str, value: str) -> None:
        inp = self._maybe(Input, wid)
        # Don't clobber a field the user is mid-edit in.
        if inp is not None and not inp.has_focus and inp.value != value:
            inp.value = value

    def _sync_slot(self, prefix: str, idx: int, slot) -> None:
        start = slot.start if slot is not None else None
        end = slot.end if slot is not None else None
        self._sync_input(f"{prefix}{idx}-start", _fmt_time(start))
        self._sync_input(f"{prefix}{idx}-end", _fmt_time(end))

    def _maybe(self, kind, wid):
        try:
            return self.query_one(f"#{wid}", kind)
        except Exception:
            return None

    # -- user actions → dispatched commands ------------------------------------

    @on(Switch.Changed)
    def _on_switch(self, event: Switch.Changed) -> None:
        if self._syncing or not self._allow_writes or self._caps is None:
            return
        on = event.value
        if event.switch.id == "enable-charge":
            self._emit(enable_charge_cmd(on), f"enable charge = {on}")
        elif event.switch.id == "enable-discharge":
            self._emit(enable_discharge_cmd(on), f"enable discharge = {on}")

    @on(Input.Submitted)
    def _on_input(self, event: Input.Submitted) -> None:
        if not self._allow_writes or self._caps is None:
            return
        wid = event.input.id or ""
        try:
            self._dispatch_input(wid, event.value)
            event.input.remove_class("-invalid")
        except (ValueError, UnsupportedControl) as exc:
            event.input.add_class("-invalid")
            self.app.bell()
            self.notify(f"{wid}: {exc}", severity="warning", timeout=4)

    def _dispatch_input(self, wid: str, value: str) -> None:
        caps = self._caps
        if caps is None:  # guarded by the caller, but keep the type checker happy
            return
        if wid == "charge-target":
            self._emit(
                charge_target_cmd(parse_soc(value), caps), f"charge target {value}%"
            )
        elif wid == "soc-reserve":
            self._emit(soc_reserve_cmd(parse_soc(value), caps), f"SOC reserve {value}%")
        elif wid[:2] in ("cs", "ds") and ("-start" in wid or "-end" in wid):
            kind, _, endpoint = wid.partition("-")
            idx = int(kind[2:])
            t = parse_hhmm(value)
            charge = kind.startswith("cs")
            if endpoint == "start":
                fn = charge_slot_start_cmd if charge else discharge_slot_start_cmd
            else:
                fn = charge_slot_end_cmd if charge else discharge_slot_end_cmd
            verb = "charge" if charge else "discharge"
            self._emit(
                fn(idx, t, caps), f"{verb} slot {idx} {endpoint} = {value or '—'}"
            )

    @on(Button.Pressed, "#reboot")
    def _reboot(self) -> None:
        if self._allow_writes:
            self.post_message(
                self.Dangerous(
                    reboot_cmd(),
                    "reboot inverter",
                    "REBOOT",
                    "Reboot the inverter? It will drop off the network for a few minutes.",
                )
            )

    @on(Button.Pressed, "#recalibrate")
    def _recalibrate(self) -> None:
        if self._allow_writes:
            self.post_message(
                self.Dangerous(
                    recalibrate_cmd(),
                    "recalibrate battery SOC",
                    "CALIBRATE",
                    "Start a battery SOC recalibration? This force-cycles the battery "
                    "and takes many hours.",
                )
            )

    def _emit(self, requests: Requests, label: str) -> None:
        self.post_message(self.Apply(requests, label))


def _str_or_blank(value) -> str:
    return "" if value is None else str(value)
