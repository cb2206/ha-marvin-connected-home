"""Cover platform — window sashes, with dry-contact failover."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from marvin_connected_home import MarvinConnectionError, MarvinError

from . import MarvinConfigEntry
from .const import DOMAIN, PATH_CLOUD, PATH_DRY_CONTACT, PATH_UNAVAILABLE
from .coordinator import MarvinCoordinator
from .entity import MarvinAssetEntity, MarvinHouseEntity
from .fallback import FallbackConfig, async_pulse, read_contact_sensor

# Commands are independent REST calls to a multi-tenant cloud that handles
# concurrency itself, and serialising them would also serialise their 15-second
# timeouts during an outage -- the moment the fallback path most needs speed.
# Relay pulses have their own per-window lock.
PARALLEL_UPDATES = 0

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: MarvinConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    house = coordinator.data
    if house is None:
        return

    entities: list[CoverEntity] = [
        MarvinSashCover(coordinator, asset.asset_id)
        for asset in house.assets
        # Capability-gated: skylights and privacy glass may have no sash at all.
        if (device := asset.primary) is not None and device.capabilities.sash
    ]
    if entities:
        entities.append(MarvinHouseCover(coordinator))
    async_add_entities(entities)


def _error(key: str, **placeholders: str) -> HomeAssistantError:
    """A user-facing error, translated through ``strings.json``."""
    return HomeAssistantError(
        translation_domain=DOMAIN, translation_key=key, translation_placeholders=placeholders
    )


class MarvinSashCover(MarvinAssetEntity, CoverEntity):
    """One window sash.

    Normally driven through the cloud, which gives continuous 0-100% positioning
    and live feedback. If the cloud is unreachable and the user has wired the dry
    contacts, commands fall back to those -- at the cost of only reaching the
    fixed stops, and of losing position feedback unless they also fitted a
    contact sensor.
    """

    _attr_device_class = CoverDeviceClass.WINDOW
    _attr_name = None

    def __init__(self, coordinator: MarvinCoordinator, asset_id: str) -> None:
        super().__init__(coordinator, asset_id, "sash")
        #: True while a repair issue for this window's degradation is open.
        self._notified_degraded = False
        self._last_contact_position: int | None = None
        # Serialises relay pulses. Without it, two commands in quick succession
        # (an automation plus a dashboard press) interleave their on/sleep/off
        # sequences across different relays -- behaviour the hardware's wiring
        # spec does not define.
        self._pulse_lock = asyncio.Lock()

    # -- control path ---------------------------------------------------

    @property
    def _fallback(self) -> FallbackConfig:
        return self.coordinator.fallback_for(self._asset_id)

    @property
    def _degraded(self) -> bool:
        """True when the cloud cannot serve this window."""
        return not self.coordinator.device_reachable(self._asset_id)

    @property
    def control_path(self) -> str:
        if not self._degraded:
            return PATH_CLOUD
        return PATH_DRY_CONTACT if self._fallback.configured else PATH_UNAVAILABLE

    @property
    def available(self) -> bool:
        # Deliberately not calling super(): a window with working dry contacts is
        # still controllable when the cloud is down, so marking it unavailable
        # would hide a control path the user explicitly configured.
        if self.control_path == PATH_UNAVAILABLE:
            return False
        return self.coordinator.last_update_success or self._fallback.configured

    @property
    def assumed_state(self) -> bool:
        # On the contacts there is no position feedback beyond an optional
        # sensor, so what is reported is inferred from the last pulse. Saying
        # so makes the UI offer both open and close instead of hiding one.
        return self._degraded

    @property
    def supported_features(self) -> CoverEntityFeature:
        if not self._degraded:
            return (
                CoverEntityFeature.OPEN
                | CoverEntityFeature.CLOSE
                | CoverEntityFeature.SET_POSITION
                | CoverEntityFeature.STOP
            )
        # On contacts, each feature exists only if a contact can actually serve
        # it: close needs any contact (erring toward closed is safe), open needs
        # a stop above 0 -- a close contact alone must not masquerade as open --
        # positioning snaps to the configured stops, and stop only exists if
        # terminal 2 was wired.
        fallback = self._fallback
        features = CoverEntityFeature(0)
        if fallback.configured:
            features |= CoverEntityFeature.CLOSE
        if fallback.can_open:
            features |= CoverEntityFeature.OPEN | CoverEntityFeature.SET_POSITION
        if fallback.can_stop:
            features |= CoverEntityFeature.STOP
        return features

    # -- state ----------------------------------------------------------

    @property
    def current_cover_position(self) -> int | None:
        if not self._degraded:
            device = self.device
            return device.sash_position if device else None

        # No cloud means no position feedback. Report the last stop we drove to
        # only when an external contact sensor corroborates it is not closed;
        # otherwise admit we do not know rather than inventing a number.
        closed = read_contact_sensor(self.hass, self._fallback.contact_sensor)
        if closed is None:
            return None
        if closed:
            return 0
        return self._last_contact_position

    @property
    def is_closed(self) -> bool | None:
        if not self._degraded:
            device = self.device
            if device is None or device.sash_open is None:
                return None
            return not device.sash_open
        return read_contact_sensor(self.hass, self._fallback.contact_sensor)

    @property
    def is_opening(self) -> bool:
        return False if self._degraded else self._travelling(opening=True)

    @property
    def is_closing(self) -> bool:
        return False if self._degraded else self._travelling(opening=False)

    def _travelling(self, *, opening: bool) -> bool:
        """Infer travel direction by comparing current and target position.

        Both stream live during movement, so this reflects real motion rather
        than an optimistic guess after a command.
        """
        device = self.device
        if device is None:
            return False
        current, target = device.sash_position, device.target_sash_position
        if current is None or target is None or current == target:
            return False
        return target > current if opening else target < current

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        device = self.device
        attributes: dict[str, Any] = {"control_path": self.control_path}
        if device is not None:
            attributes["target_position"] = device.target_sash_position
            attributes["locked"] = device.locked
        if self._degraded:
            fallback = self._fallback
            attributes["available_positions"] = list(fallback.available_positions)
            # Inferred means "the last stop we drove to", not a measurement. A
            # 0 backed by the contact sensor reading closed is measured, so it
            # is not flagged.
            closed = read_contact_sensor(self.hass, fallback.contact_sensor)
            attributes["position_is_inferred"] = (
                closed is False and self._last_contact_position is not None
            )
        return attributes

    @callback
    def _handle_coordinator_update(self) -> None:
        # The cloud is back: retire the repair issue on the next state push
        # rather than waiting for the user's next command.
        if self._notified_degraded and not self._degraded:
            self._clear_degraded_issue()
        super()._handle_coordinator_update()

    # -- commands -------------------------------------------------------

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._async_move(100)

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._async_move(0)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        await self._async_move(int(kwargs[ATTR_POSITION]))

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop travel.

        The cloud has no stop command; the Marvin app re-issues the current
        position as the target, and so do we. The dry contacts *do* have a real
        stop terminal, so when degraded that is used directly if wired.
        """
        if self._degraded:
            fallback = self._fallback
            if not fallback.can_stop:
                raise _error("stop_no_contact")
            async with self._pulse_lock:
                await async_pulse(
                    self.hass, fallback.stop_switch, fallback.pulse_duration
                )
            return

        device = self.device
        if device is None or device.sash_position is None:
            raise _error("stop_position_unknown")
        await self._async_move(device.sash_position, allow_fallback=False)

    async def _async_move(self, position: int, *, allow_fallback: bool = True) -> None:
        """Move the sash, preferring the cloud and falling back to contacts."""
        if not self._degraded:
            try:
                await self.coordinator.client.async_set_sash_position(self._asset_id, position)
            except MarvinConnectionError as err:
                if not (allow_fallback and self._fallback.configured):
                    raise _error("cloud_unreachable", error=str(err)) from err
                # The cloud vanished mid-command -- or only seemed to. A timed-out
                # request may still have been delivered, and pulsing a relay on
                # top of it would hand the sash two conflicting targets. So
                # confirm the outage first: if a poll gets through, the cloud is
                # up and this was a transient failure to report, not an outage
                # to fall back on.
                await self.coordinator.async_refresh()
                if self.coordinator.last_update_success:
                    raise _error("cloud_command_failed", error=str(err)) from err
                _LOGGER.debug("Cloud command failed and the outage is confirmed: %s", err)
            except MarvinError as err:
                # An auth or per-command rejection is not a connectivity problem;
                # the contacts would not fix it and might mask a real fault.
                raise _error(
                    "move_failed", name=self.name or "window", error=str(err)
                ) from err
            else:
                self._clear_degraded_issue()
                # The cloud moved the window, so any position remembered from a
                # past contact pulse no longer describes reality. Clearing it
                # keeps a *later* outage from reporting a stop driven before
                # this command as if the sash were still there.
                self._last_contact_position = None
                return

        if not allow_fallback:
            raise _error("stop_cloud_unreachable")
        await self._async_move_via_contacts(position)

    async def _async_move_via_contacts(self, position: int) -> None:
        fallback = self._fallback
        stop = fallback.resolve(position)
        if stop is None:
            if position > 0 and fallback.configured:
                raise _error("fallback_close_only")
            raise _error("fallback_not_configured")

        async with self._pulse_lock:
            await async_pulse(self.hass, stop.entity_id, fallback.pulse_duration)
        self._last_contact_position = stop.position
        self.async_write_ha_state()

        if stop.position != position:
            _LOGGER.info(
                "%s: dry contacts offer %s; %d%% requested, drove to %d%%",
                self.entity_id,
                fallback.available_positions,
                position,
                stop.position,
            )

        if fallback.notify_on_switchover and not self._notified_degraded:
            self._notified_degraded = True
            self._create_degraded_issue(fallback)

    # -- repair issue ---------------------------------------------------

    @property
    def _issue_id(self) -> str:
        return f"degraded_{self._asset_id}"

    def _create_degraded_issue(self, fallback: FallbackConfig) -> None:
        # Blaming the network for a dead session would send the user off to
        # check their router when the fix is signing in again, so the two
        # causes get different issues.
        key = (
            "using_dry_contacts_reauth"
            if self.coordinator.auth_failed
            else "using_dry_contacts"
        )
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=key,
            translation_placeholders={
                "window": self.name or self.entity_id or "window",
                "positions": ", ".join(f"{p}%" for p in fallback.available_positions),
            },
        )

    def _clear_degraded_issue(self) -> None:
        if self._notified_degraded:
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
        self._notified_degraded = False


class MarvinHouseCover(MarvinHouseEntity, CoverEntity):
    """Every sash in the house, as one entity.

    The API accepts a house id in place of an asset id and broadcasts, which is
    what the Marvin app's "airflow" control does. One call beats N.

    Deliberately no position or state: it aggregates windows that can disagree,
    and reporting one number for all of them would be a lie. It is write-only,
    and cloud-only -- a house-wide broadcast has no dry-contact equivalent.

    The only entity here disabled by default, and not for tidiness: one press
    moves every window in the house, which is easy to do by accident and hard
    to undo if it is raining.
    """

    _attr_device_class = CoverDeviceClass.WINDOW
    _attr_translation_key = "all_windows"
    _attr_supported_features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
    _attr_entity_registry_enabled_default = False
    # Write-only, so the UI must not hide one of the two buttons based on a
    # state it does not have.
    _attr_assumed_state = True

    def __init__(self, coordinator: MarvinCoordinator) -> None:
        super().__init__(coordinator, "all_windows")

    @property
    def is_closed(self) -> bool | None:
        return None

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._async_broadcast(100)

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._async_broadcast(0)

    async def _async_broadcast(self, position: int) -> None:
        try:
            await self.coordinator.client.async_set_sash_position(
                self.coordinator.house_id, position
            )
        except MarvinError as err:
            raise _error("house_command_failed", error=str(err)) from err
