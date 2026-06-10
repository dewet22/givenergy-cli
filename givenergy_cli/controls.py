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


class _CommitInput(Input):
    """An Input that commits on blur as well as on Enter, HA-style: tabbing or
    clicking away applies the typed value rather than letting the next read-back
    revert it. The panel dedups unchanged values, so a no-op blur sends nothing."""

    def on_blur(self) -> None:
        self.post_message(self.Submitted(self, self.value))


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
    ControlsPanel #writes-hint {
        width: 1fr;
        color: $text;
        background: $error;
        text-style: bold;
        text-align: center;
        padding: 1 2;
        margin-bottom: 1;
        border: round $error;
    }
    ControlsPanel.-readonly Input, ControlsPanel.-readonly Switch,
    ControlsPanel.-readonly Button { opacity: 40%; }
    /* Write in flight — tint without disabling, so focus isn't stolen. */
    ControlsPanel .-pending { background: $warning 25%; }
    /* Brief flash on write resolution — a clearer cue than the corner toast. */
    ControlsPanel .-write-ok { background: $success 50%; }
    ControlsPanel .-write-fail { background: $error 50%; }
    ControlsPanel Input.-invalid { border: tall $error; }
    """

    # How long the green/red write-result flash stays on a control.
    _FLASH_SECONDS = 1.2

    class Apply(Message):
        """An everyday control changed — apply *requests* immediately. The
        emitting control is frozen until the write resolves (see freeze/unfreeze)."""

        def __init__(self, requests: Requests, label: str, control_id: str) -> None:
            super().__init__()
            self.requests = requests
            self.label = label
            self.control_id = control_id

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
        # Controls with a write in flight: disabled, and skipped by refresh_from
        # so the optimistic value holds until the write resolves (no flip-back).
        self._pending: set[str] = set()
        # Switches we just set programmatically during a read-back sync. The
        # resulting Switch.Changed arrives asynchronously (after the sync call
        # returns), so a flag can't distinguish it from a user toggle — instead
        # the handler consumes one echo per id here, suppressing a spurious
        # write-back of the value we just read.
        self._programmatic: set[str] = set()
        # Last known real value per input, for change detection — a commit
        # (Enter or blur) whose value matches this sends no write.
        self._last_value: dict[str, str] = {}
        # Number of slot editors rendered; fixed at first sight of capabilities.
        self._slots = 0
        self._built = False

    def compose(self) -> ComposeResult:
        if not self._allow_writes:
            yield Label(
                "⚠  READ-ONLY  —  these controls are disabled.\n"
                "Relaunch with  --allow-writes  to send commands to the inverter.",
                id="writes-hint",
            )
        # The control rows are built once capabilities are known (slot count
        # depends on the model); see _build().
        yield Vertical(id="controls-body")

    def _row(self, label: str, widget) -> Horizontal:
        return Horizontal(Label(label), widget, classes="row")

    def _switch(self, wid: str) -> Switch:
        return Switch(id=wid, disabled=self._readonly)

    def _num_input(self, wid: str) -> _CommitInput:
        return _CommitInput(id=wid, disabled=self._readonly)

    def _time_input(self, wid: str) -> _CommitInput:
        return _CommitInput(id=wid, placeholder="HH:MM", disabled=self._readonly)

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
        body.mount(self._row("Charge target %", self._num_input("charge-target")))
        body.mount(self._row("SOC reserve %", self._num_input("soc-reserve")))
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

        self._sync_switch("enable-charge", inv.enable_charge)
        self._sync_switch("enable-discharge", inv.enable_discharge)
        self._sync_input("charge-target", _str_or_blank(inv.charge_target_soc))
        self._sync_input("soc-reserve", _str_or_blank(inv.battery_soc_reserve))
        for i in range(1, self._slots + 1):
            self._sync_slot("cs", i, getattr(inv, f"charge_slot_{i}", None))
            self._sync_slot("ds", i, getattr(inv, f"discharge_slot_{i}", None))

    def _sync_switch(self, wid: str, value) -> None:
        if wid in self._pending:  # write in flight — hold the optimistic value
            return
        sw = self._maybe(Switch, wid)
        if sw is not None and value is not None and sw.value != bool(value):
            # Mark the echo so _on_switch doesn't treat our own set as a toggle.
            self._programmatic.add(wid)
            sw.value = bool(value)

    def _sync_input(self, wid: str, value: str) -> None:
        if wid in self._pending:
            return
        self._last_value[wid] = value  # the real value, for commit change-detection
        inp = self._maybe(Input, wid)
        # Don't clobber a field the user is mid-edit in.
        if inp is not None and not inp.has_focus and inp.value != value:
            inp.value = value

    # -- write-in-flight freeze ------------------------------------------------

    def freeze(self, control_id: str) -> None:
        """Pin a control's value while its write is on the wire so the 1 s
        read-back doesn't flip it back. Marked visually (a tint) rather than
        disabled — disabling would steal focus from the control just used."""
        self._pending.add(control_id)
        w = self._maybe(None, control_id)
        if w is not None:
            w.add_class("-pending")

    def unfreeze(self, control_id: str) -> None:
        """Release a control once its write has resolved; the next refresh syncs
        it to the inverter's real state (which, on success, is what was set)."""
        self._pending.discard(control_id)
        w = self._maybe(None, control_id)
        if w is not None:
            w.remove_class("-pending")

    def write_finished(self, control_id: str, ok: bool) -> None:
        """Release a frozen control and flash it green (ok) or red (failed) for
        a moment — a more noticeable cue than the corner toast."""
        self.unfreeze(control_id)
        w = self._maybe(None, control_id)
        if w is None:
            return
        flash = "-write-ok" if ok else "-write-fail"
        w.add_class(flash)
        self.set_timer(self._FLASH_SECONDS, lambda: w.remove_class(flash))

    def _sync_slot(self, prefix: str, idx: int, slot) -> None:
        start = slot.start if slot is not None else None
        end = slot.end if slot is not None else None
        self._sync_input(f"{prefix}{idx}-start", _fmt_time(start))
        self._sync_input(f"{prefix}{idx}-end", _fmt_time(end))

    def _maybe(self, kind, wid):
        try:
            if kind is None:
                return self.query_one(f"#{wid}")
            return self.query_one(f"#{wid}", kind)
        except Exception:
            return None

    # -- user actions → dispatched commands ------------------------------------

    @on(Switch.Changed)
    def _on_switch(self, event: Switch.Changed) -> None:
        wid = event.switch.id or ""
        if wid in self._programmatic:  # our own read-back set, not a user toggle
            self._programmatic.discard(wid)
            return
        if not self._allow_writes or self._caps is None:
            return
        on = event.value
        if wid == "enable-charge":
            self._emit(enable_charge_cmd(on), f"enable charge = {on}", wid)
        elif wid == "enable-discharge":
            self._emit(enable_discharge_cmd(on), f"enable discharge = {on}", wid)

    @on(Input.Submitted)
    def _on_input(self, event: Input.Submitted) -> None:
        # Fires on Enter and (via _CommitInput) on blur. Skip an unchanged value
        # so tabbing through fields, or blur-after-Enter, doesn't re-write.
        if not self._allow_writes or self._caps is None:
            return
        wid = event.input.id or ""
        if event.value == self._last_value.get(wid):
            return
        try:
            self._dispatch_input(wid, event.value)
            self._last_value[wid] = event.value
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
                charge_target_cmd(parse_soc(value), caps),
                f"charge target {value}% (also enables charge)",
                wid,
            )
        elif wid == "soc-reserve":
            self._emit(
                soc_reserve_cmd(parse_soc(value), caps), f"SOC reserve {value}%", wid
            )
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
                fn(idx, t, caps), f"{verb} slot {idx} {endpoint} = {value or '—'}", wid
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

    def _emit(self, requests: Requests, label: str, control_id: str) -> None:
        self.post_message(self.Apply(requests, label, control_id))


def _str_or_blank(value) -> str:
    return "" if value is None else str(value)
