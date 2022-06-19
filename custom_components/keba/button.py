"""Support for KEBA button entities."""
from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from keba_kecontact.connection import KebaKeContact
from keba_kecontact.wallbox import Wallbox

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import KebaBaseEntity
from .const import DOMAIN, KEBA_CONNECTION


@dataclass
class KebaButtonEntityDescription(ButtonEntityDescription):
    """Class describing Keba button entities."""

    remote_function: Callable[[Wallbox], Coroutine[Any, Any, Any]] | None = None


BUTTON_TYPES: tuple[KebaButtonEntityDescription, ...] = (
    KebaButtonEntityDescription(
        key="request_data",
        icon="mdi:refresh",
        name="Request data",
        remote_function=lambda wallbox: wallbox.request_data(),
    ),
    KebaButtonEntityDescription(
        key="enable",
        icon="mdi:play",
        name="Enable",
        remote_function=lambda wallbox: wallbox.enable(),
    ),
    KebaButtonEntityDescription(
        key="disable",
        icon="mdi:stop",
        name="Disable",
        remote_function=lambda wallbox: wallbox.disable(),
    ),
    KebaButtonEntityDescription(
        key="unlock_socket",
        icon="mdi:ev-plug-type2",
        name="Unlock Socket",
        remote_function=lambda wallbox: wallbox.unlock_socket(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the keba charging station buttons from config entry."""
    keba: KebaKeContact = hass.data[DOMAIN][KEBA_CONNECTION]
    entities: list[KebaButton] = []

    wallbox = keba.get_wallbox(config_entry.data[CONF_HOST])
    entities.extend([KebaButton(wallbox, description) for description in BUTTON_TYPES])
    async_add_entities(entities, True)


class KebaButton(KebaBaseEntity, ButtonEntity):
    """Representation of a keba button."""

    entity_description: KebaButtonEntityDescription

    def __init__(
        self,
        wallbox: Wallbox,
        description: KebaButtonEntityDescription,
    ) -> None:
        """Initialize keba button."""
        super().__init__(wallbox, description)

    async def async_press(self) -> None:
        """Process the button press."""
        if self.entity_description.remote_function:
            await self.entity_description.remote_function(self._wallbox)
