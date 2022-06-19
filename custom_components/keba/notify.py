"""Support for Keba notifications."""
from __future__ import annotations

import logging
from typing import Any, cast

from keba_kecontact.connection import KebaKeContact
from keba_kecontact.wallbox import Wallbox

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
    return KebaNotificationService(keba)


class KebaNotificationService(BaseNotificationService):
    """Notification service for KEBA EV Chargers."""

    def __init__(self, keba: KebaKeContact) -> None:
        """Initialize the service."""
        self.targets: dict[str, Wallbox] = {}
        self.targets.update({w.device_info.model: w for w in keba.get_wallboxes()})

    async def async_send_message(self, message: str = "", **kwargs: Any) -> None:
        """Send the message."""
        for wallbox in kwargs[ATTR_TARGET]:
            wallbox = cast(Wallbox, wallbox)

            i = wallbox.device_info
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

            await wallbox.display(message, min_time, max_time)
