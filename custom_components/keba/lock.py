"""Support for KEBA charging station switch."""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.components.lock import LockEntityDescription, LockEntity

from . import KebaBaseEntity
from .const import CONF_RFID, CONF_RFID_CLASS, DOMAIN, KEBA_CONNECTION

from keba_kecontact.connection import KebaKeContact
from keba_kecontact.wallbox import Wallbox


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the BMW ConnectedDrive sensors from config entry."""
    keba: KebaKeContact = hass.data[DOMAIN][KEBA_CONNECTION]
    entities: list[KebaLock] = []

    for wallbox in keba.get_wallboxes():
        lock_description = LockEntityDescription(key="Authreq", name="Authentication")

        additional_args = {}
        if CONF_RFID in entry.options and entry.options[CONF_RFID] != "":
            additional_args[CONF_RFID] = entry.options[CONF_RFID]
        if CONF_RFID_CLASS in entry.options and entry.options[CONF_RFID_CLASS] != "":
            additional_args[CONF_RFID_CLASS] = entry.options[CONF_RFID_CLASS]

        lock = KebaLock(wallbox, lock_description, additional_args)
        entities.append(lock)
    async_add_entities(entities, True)


class KebaLock(KebaBaseEntity, LockEntity):
    """The entity class for KEBA charging stations sensors."""

    def __init__(
        self,
        wallbox: Wallbox,
        description: LockEntityDescription,
        additional_args=None,
    ) -> None:
        """Initialize the KEBA Sensor."""
        super().__init__(wallbox, description)
        self._additional_args = additional_args if additional_args is not None else {}

    async def async_update(self):
        """Get latest cached states from the device."""
        self._attr_is_locked = self._wallbox.get_value(self.entity_description.key) == 1

    async def async_lock(self, **kwargs):
        """Lock wallbox."""
        await self._wallbox.stop(**self._additional_args)

    async def async_unlock(self, **kwargs):
        """Unlock wallbox."""
        await self._wallbox.start(**self._additional_args)
