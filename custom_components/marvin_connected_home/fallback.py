"""Dry-contact fallback.

Marvin's windows expose momentary dry contacts that work with no network at all.
When the cloud is unreachable those contacts are the only way to move a sash, so
this module drives them via whatever switch entities the user has wired up.

Design constraints worth stating, because they shape everything here:

* The contacts offer a **fixed set of stops**, not continuous positioning. A
  request for 55% becomes the nearest configured stop, and the cover reports the
  stop it actually went to -- not the number that was asked for.
* Contacts are **momentary and edge-triggered**. Marvin's wiring instructions
  specify no minimum duration; the pulse only has to be long enough for the relay
  to actuate.
* With no cloud there is **no position feedback**. Unless the user has fitted
  their own contact sensor, position is reported as unknown. A cover that claims
  60% while actually shut is worse than one admitting it does not know.

The mapping between the API's ``hA*Position`` keys and the physical terminals is
undocumented, so nothing here relies on it: positions come from the user's own
configuration, which they confirm against their own wiring.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.const import ATTR_ENTITY_ID, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant

from .const import (
    CONF_CLOSE_SWITCH,
    CONF_CONTACT_SENSOR,
    CONF_NOTIFY_ON_SWITCHOVER,
    CONF_POSITION_SWITCHES,
    CONF_PULSE_DURATION,
    CONF_STOP_SWITCH,
    CONF_SWITCH_ENTITY,
    CONF_POSITION,
    DEFAULT_NOTIFY_ON_SWITCHOVER,
    DEFAULT_PULSE_DURATION,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ContactStop:
    """One dry-contact terminal and the position it drives to."""

    entity_id: str
    position: int


@dataclass(frozen=True, slots=True)
class FallbackConfig:
    """Per-asset dry-contact wiring, as configured by the user."""

    close_switch: str | None = None
    stop_switch: str | None = None
    stops: tuple[ContactStop, ...] = ()
    contact_sensor: str | None = None
    pulse_duration: float = DEFAULT_PULSE_DURATION
    notify_on_switchover: bool = DEFAULT_NOTIFY_ON_SWITCHOVER

    @property
    def configured(self) -> bool:
        """True when there is at least one way to move the sash."""
        return bool(self.close_switch or self.stops)

    @property
    def can_stop(self) -> bool:
        return self.stop_switch is not None

    @property
    def can_open(self) -> bool:
        """True when some contact drives to a position above closed.

        A close contact alone can only ever shut the window, so offering
        "open" on the strength of it would fire the close relay for an open
        request -- the exact inversion :meth:`resolve` refuses to perform.
        """
        return any(stop.position > 0 for stop in self.stops)

    @classmethod
    def parse(cls, raw: dict[str, Any] | None) -> FallbackConfig:
        raw = raw or {}
        stops = tuple(
            ContactStop(entity_id=entity, position=int(item[CONF_POSITION]))
            for item in raw.get(CONF_POSITION_SWITCHES) or []
            if isinstance(item, dict)
            and (entity := item.get(CONF_SWITCH_ENTITY))
            and item.get(CONF_POSITION) is not None
        )
        try:
            pulse = float(raw.get(CONF_PULSE_DURATION, DEFAULT_PULSE_DURATION))
        except (TypeError, ValueError):
            pulse = DEFAULT_PULSE_DURATION

        return cls(
            close_switch=raw.get(CONF_CLOSE_SWITCH) or None,
            stop_switch=raw.get(CONF_STOP_SWITCH) or None,
            stops=tuple(sorted(stops, key=lambda s: s.position)),
            contact_sensor=raw.get(CONF_CONTACT_SENSOR) or None,
            pulse_duration=max(0.0, pulse),
            notify_on_switchover=bool(
                raw.get(CONF_NOTIFY_ON_SWITCHOVER, DEFAULT_NOTIFY_ON_SWITCHOVER)
            ),
        )

    def resolve(self, target: int) -> ContactStop | None:
        """Return the contact that best serves *target*, or None if unreachable.

        A close request (``0``) uses the close contact when there is one, else
        the nearest stop -- erring toward closed is the right failure mode for
        a window it may be raining on. An *open* request (anything above 0)
        only ever considers stops above 0: snapping 55% to a 60% stop is
        honest degradation, but a request to open must never fire the close
        relay, even when that relay is the nearest contact to the target.
        """
        if target <= 0:
            if self.close_switch:
                return ContactStop(entity_id=self.close_switch, position=0)
            candidates = list(self.stops)
        else:
            candidates = [stop for stop in self.stops if stop.position > 0]
        if not candidates:
            return None
        # Ties go to the lower position, erring toward closing.
        return min(candidates, key=lambda stop: (abs(stop.position - target), stop.position))

    @property
    def available_positions(self) -> tuple[int, ...]:
        positions = [stop.position for stop in self.stops]
        if self.close_switch:
            positions.append(0)
        return tuple(sorted(set(positions)))


async def async_pulse(hass: HomeAssistant, entity_id: str, duration: float) -> None:
    """Momentarily close a contact by toggling *entity_id*.

    A ``duration`` of 0 means the switch pulses itself (many Zigbee relays have a
    momentary mode), so only the on command is sent. Otherwise the switch is
    turned back off, because leaving a Marvin contact held closed is not what the
    hardware expects -- its wiring instructions call for momentary switches.
    """
    await hass.services.async_call(
        "switch", "turn_on", {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    if duration <= 0:
        return
    await asyncio.sleep(duration)
    await hass.services.async_call(
        "switch", "turn_off", {ATTR_ENTITY_ID: entity_id}, blocking=True
    )


def read_contact_sensor(hass: HomeAssistant, entity_id: str | None) -> bool | None:
    """Return True when the sash is closed per the user's own sensor.

    ``None`` means unknown -- no sensor configured, or it is not reporting. The
    caller must surface that as unknown rather than guessing.
    """
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
        return None
    # An `opening`-class binary sensor is on when open, so closed is the inverse.
    return state.state != STATE_ON
