"""Event entities for keba charging stations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from keba_kecontact.charging_station import ChargingStation
from keba_kecontact.connection import KebaKeContact

from homeassistant.components.event import EventEntity, EventEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, KEBA_CONNECTION
from .entity import KebaBaseEntity

EVENT_SESSION_STARTED = "started"
EVENT_SESSION_ENDED = "ended"

SESSION_EVENT_DESCRIPTION = EventEntityDescription(
    key="session",
    translation_key="session",
    name="Charging session",
    icon="mdi:ev-plug-type2",
    event_types=[EVENT_SESSION_STARTED, EVENT_SESSION_ENDED],
)


@dataclass(frozen=True)
class SessionSnapshot:
    """Session fields of the latest report 100 of a charging station."""

    session_id: int
    rfid_tag: str | None
    rfid_class: str | None
    ended: bool
    reason: int | None
    energy: float | None


def _rfid_value(value: Any) -> str | None:
    """Return an RFID value, or None if the station reports no card (all zeros)."""
    if value is None:
        return None
    text = str(value)
    if text.strip("0") == "":
        return None
    return text


def session_snapshot(data: dict[str, Any]) -> SessionSnapshot | None:
    """Build a session snapshot from the charging station data, if available."""
    session_id = data.get("Session ID")
    if session_id is None:
        return None
    reason = data.get("reason")
    return SessionSnapshot(
        session_id=int(session_id),
        rfid_tag=_rfid_value(data.get("RFID tag")),
        rfid_class=_rfid_value(data.get("RFID class")),
        ended=str(data.get("ended", "0")) != "0",
        reason=int(reason) if reason is not None else None,
        energy=data.get("E pres"),  # codespell:ignore pres
    )


def session_events(
    previous: SessionSnapshot | None, current: SessionSnapshot
) -> list[tuple[str, dict[str, Any]]]:
    """Return the session events between two snapshots, oldest first.

    The first snapshot only sets the baseline, so a running session does not fire
    "started" again after a restart of Home Assistant.
    """
    if previous is None:
        return []

    events: list[tuple[str, dict[str, Any]]] = []
    new_session = current.session_id != previous.session_id

    if new_session:
        events.append(
            (
                EVENT_SESSION_STARTED,
                {
                    "session_id": current.session_id,
                    "rfid_tag": current.rfid_tag,
                    "rfid_class": current.rfid_class,
                },
            )
        )

    if current.ended and (new_session or not previous.ended):
        events.append(
            (
                EVENT_SESSION_ENDED,
                {
                    "session_id": current.session_id,
                    "rfid_tag": current.rfid_tag,
                    "rfid_class": current.rfid_class,
                    "reason": current.reason,
                    "energy": current.energy,
                },
            )
        )

    return events


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Keba charging station events from config entry."""
    keba: KebaKeContact = hass.data[DOMAIN][KEBA_CONNECTION]
    charging_station = keba.get_charging_station(config_entry.data[CONF_HOST])
    if not charging_station.device_info.is_data_logger_integrated():
        return
    async_add_entities([KebaSessionEvent(charging_station, SESSION_EVENT_DESCRIPTION)])


class KebaSessionEvent(KebaBaseEntity, EventEntity):
    """Fires when a charging session starts or ends, including the RFID tag."""

    def __init__(
        self,
        charging_station: ChargingStation,
        description: EventEntityDescription,
    ) -> None:
        """Initialize the session event entity."""
        super().__init__(charging_station, description)
        self._last_snapshot: SessionSnapshot | None = None

    async def async_update(self) -> None:
        """Compare the latest session data with the last one and fire events."""
        current = session_snapshot(self._charging_station.get_value())
        if current is None:
            return

        events = session_events(self._last_snapshot, current)
        self._last_snapshot = current

        for index, (event_type, attributes) in enumerate(events):
            if index > 0:
                # Write the previous event, otherwise only the last one is visible.
                self.async_write_ha_state()
            self._trigger_event(event_type, attributes)
