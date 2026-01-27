"""Support for KEBA button entities."""

import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from keba_kecontact.charging_station import ChargingStation
from keba_kecontact.connection import KebaKeContact

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CHARGING_STATIONS,
    CONF_DEVICE_TYPE,
    DEVICE_TYPE_P40,
    DEVICE_TYPE_UDP,
    DOMAIN,
    KEBA_CONNECTION,
)
from .entity import KebaBaseEntity

_LOGGER = logging.getLogger(__name__)


def _is_socket_version(model: str) -> bool:
    """Check if the P40 model is a socket/plug version (S0) vs cable version (C6).

    Model code pattern: KC-P40-[Leistung][Region]-[Anschluss][Phasen][Zähler]-...
    - C6 = Cable attached (no socket unlock)
    - S0 = Socket/plug version (has socket unlock feature)
    """
    if not model:
        return False
    # Look for S0 in the connection type position (after the power/region code)
    # Example: KC-P40-16EU0-S0S1AM00-... (socket version)
    # Example: KC-P40-16EU0-C6S3AE00-... (cable version)
    parts = model.split("-")
    if len(parts) >= 4:
        connection_part = parts[3]  # e.g., "S0S1AM00" or "C6S3AE00"
        return connection_part.startswith("S0")
    return False


@dataclass(frozen=True)
class KebaButtonEntityDescription(ButtonEntityDescription):
    """Class describing Keba button entities."""

    remote_function: Callable[[ChargingStation], Coroutine[Any, Any, Any]] | None = None


# Common button types for all devices
BUTTON_TYPES: tuple[KebaButtonEntityDescription, ...] = (
    KebaButtonEntityDescription(
        key="request_data",
        icon="mdi:refresh",
        name="Request data",
        entity_category=EntityCategory.DIAGNOSTIC,
        remote_function=lambda charging_station: charging_station.request_data(),
    ),
    KebaButtonEntityDescription(
        key="enable",
        icon="mdi:play",
        name="Enable",
        remote_function=lambda charging_station: charging_station.enable(),
    ),
    KebaButtonEntityDescription(
        key="disable",
        icon="mdi:stop",
        name="Disable",
        remote_function=lambda charging_station: charging_station.disable(),
    ),
)

# P40 button types - only request_data (Enable/Disable handled by Lock entity)
P40_BUTTON_TYPES: tuple[KebaButtonEntityDescription, ...] = (
    KebaButtonEntityDescription(
        key="request_data",
        icon="mdi:refresh",
        name="Request data",
        entity_category=EntityCategory.DIAGNOSTIC,
        remote_function=lambda charging_station: charging_station.request_data(),
    ),
)

# Socket unlock button - only for socket versions
UNLOCK_SOCKET_BUTTON = KebaButtonEntityDescription(
    key="unlock_socket",
    icon="mdi:ev-plug-type2",
    name="Unlock Socket",
    remote_function=lambda charging_station: charging_station.unlock_socket(),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the keba charging station buttons from config entry."""
    device_type = config_entry.data.get(CONF_DEVICE_TYPE, DEVICE_TYPE_UDP)

    if device_type == DEVICE_TYPE_P40:
        charging_station = hass.data[DOMAIN][CHARGING_STATIONS][config_entry.entry_id]
        # For P40: use P40_BUTTON_TYPES (no Enable/Disable - handled by Lock entity)
        # Add unlock_socket only for socket versions (S0)
        button_types = list(P40_BUTTON_TYPES)
        model = charging_station.get_value("Model") or ""
        if _is_socket_version(model):
            _LOGGER.debug("P40 model %s is a socket version, adding unlock button", model)
            button_types.append(UNLOCK_SOCKET_BUTTON)
        else:
            _LOGGER.debug("P40 model %s is a cable version, skipping unlock button", model)
    else:
        keba: KebaKeContact = hass.data[DOMAIN][KEBA_CONNECTION]
        charging_station = keba.get_charging_station(config_entry.data[CONF_HOST])
        # For UDP: use all buttons including unlock_socket
        button_types = list(BUTTON_TYPES) + [UNLOCK_SOCKET_BUTTON]

    entities: list[KebaButton] = []
    entities.extend(
        [KebaButton(charging_station, description) for description in button_types]
    )
    async_add_entities(entities, True)


class KebaButton(KebaBaseEntity, ButtonEntity):
    """Representation of a keba button."""

    entity_description: KebaButtonEntityDescription

    def __init__(
        self,
        charging_station: ChargingStation,
        description: KebaButtonEntityDescription,
    ) -> None:
        """Initialize keba button."""
        super().__init__(charging_station, description)

    async def async_press(self) -> None:
        """Process the button press."""
        if self.entity_description.remote_function:
            await self.entity_description.remote_function(self._charging_station)
