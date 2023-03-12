"""Support for KEBA charging station switch."""
from __future__ import annotations

from typing import Any

from keba_kecontact.chargingstation import ChargingStation
from keba_kecontact.connection import KebaKeContact

from homeassistant.components.lock import LockEntity, LockEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import KebaBaseEntity
from .const import CONF_RFID, CONF_RFID_CLASS, DOMAIN, KEBA_CONNECTION


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the keba charging station locks from config entry."""
    keba: KebaKeContact = hass.data[DOMAIN][KEBA_CONNECTION]
    entities: list[KebaLock] = []

    charging_station = keba.get_charging_station(entry.data[CONF_HOST])
    lock_description = LockEntityDescription(key="Authreq", name="Authentication")

    additional_args = {}
    if CONF_RFID in entry.options and entry.options[CONF_RFID] != "":
        additional_args[CONF_RFID] = entry.options[CONF_RFID]
        if CONF_RFID_CLASS in entry.options and entry.options[CONF_RFID_CLASS] != "":
            additional_args[CONF_RFID_CLASS] = entry.options[CONF_RFID_CLASS]

        lock = KebaLock(charging_station, lock_description, additional_args)
        entities.append(lock)
    async_add_entities(entities, True)


class KebaLock(KebaBaseEntity, LockEntity):
    """The entity class for KEBA charging stations sensors."""

    def __init__(
        self,
        charging_station: ChargingStation,
        description: LockEntityDescription,
        additional_args=None,
    ) -> None:
        """Initialize the KEBA Sensor."""
        super().__init__(charging_station, description)
        self._additional_args = additional_args if additional_args is not None else {}

    async def async_update(self) -> None:
        """Get latest cached states from the device."""
        self._attr_is_locked = (
            self._charging_station.get_value(self.entity_description.key) == 1
        )

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock charging station."""
        await self._charging_station.stop(**self._additional_args)

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock charging station."""
        await self._charging_station.start(**self._additional_args)
