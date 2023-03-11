"""Support for Keba notifications."""
from __future__ import annotations

import logging
from typing import Any, cast

from keba_kecontact.chargingstation import ChargingStation, KebaService

from homeassistant.components.notify import (
    ATTR_DATA,
    ATTR_TARGET,
    BaseNotificationService,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import DOMAIN, KEBA_CONNECTION

_LOGGER = logging.getLogger(__name__)


async def async_get_service(
    hass: HomeAssistant,
    config: ConfigType,
    discovery_info: DiscoveryInfoType | None = None,
) -> KebaNotificationService:
    """Return the notify service."""

    keba = hass.data[DOMAIN][KEBA_CONNECTION]
    targets = {
        w.device_info.model: w
        for w in keba.get_charging_stations()
        if KebaService.DISPLAY in w.device_info.available_services()
    }
    return KebaNotificationService(targets)


class KebaNotificationService(BaseNotificationService):
    """Notification service for KEBA EV Chargers."""

    charging_station_targets: dict[str, ChargingStation] = {}

    def __init__(self, targets: dict[str, ChargingStation]) -> None:
        """Initialize the service."""
        self.charging_station_targets = targets

    @property
    def targets(self) -> dict[str, Any] | None:
        """Return a dictionary of registered targets."""
        return self.charging_station_targets

    async def async_send_message(self, message: str = "", **kwargs: Any) -> None:
        """Send the message."""
        for charging_station in kwargs[ATTR_TARGET]:
            charging_station = cast(ChargingStation, charging_station)

            i = charging_station.device_info
            _LOGGER.debug(
                "Sending message '%s' to %s %s (Serial: %s)",
                message,
                i.manufacturer,
                i.model,
                i.device_id,
            )

            # Extract params from data dict
            data = kwargs[ATTR_DATA] or {}
            min_time = float(data.get("min_time", 2))
            max_time = float(data.get("max_time", 10))

            await charging_station.display(message, min_time, max_time)
