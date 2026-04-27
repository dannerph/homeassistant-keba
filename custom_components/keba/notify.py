"""Support for Keba notifications."""

from __future__ import annotations

from typing import Any

from keba_kecontact.charging_station import ChargingStation

from homeassistant.components.notify import ATTR_DATA, BaseNotificationService
from homeassistant.const import ATTR_CONFIG_ENTRY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import CHARGING_STATIONS, DOMAIN


async def async_get_service(
    hass: HomeAssistant,
    config: ConfigType,
    discovery_info: DiscoveryInfoType | None = None,
) -> BaseNotificationService | None:
    """Return the notify service."""
    if discovery_info is None:
        return None

    charging_station = hass.data[DOMAIN][CHARGING_STATIONS].get(
        discovery_info[ATTR_CONFIG_ENTRY_ID]
    )
    if charging_station is None:
        return None

    return KebaNotificationService(charging_station)


class KebaNotificationService(BaseNotificationService):
    """Notification service for KEBA charging stations."""

    def __init__(self, charging_station: ChargingStation) -> None:
        """Initialize the service."""
        self._charging_station = charging_station

    async def async_send_message(self, message: str = "", **kwargs: Any) -> None:
        """Send the message."""
        text = message.replace(" ", "$")

        data = kwargs.get(ATTR_DATA) or {}
        min_time = float(data.get("min_time", 2))
        max_time = float(data.get("max_time", 10))

        try:
            await self._charging_station.display(text, min_time, max_time)
        except NotImplementedError as ex:
            raise ServiceValidationError(
                "Display is not available on selected charging station"
            ) from ex
        except ValueError as ex:
            raise ServiceValidationError(str(ex)) from ex
