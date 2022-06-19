"""Support for KEBA charging station binary sensors."""
from __future__ import annotations

from keba_kecontact.connection import KebaKeContact
from keba_kecontact.wallbox import Wallbox

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import KebaBaseEntity
from .const import DOMAIN, KEBA_CONNECTION

SENSOR_TYPES = [
    # default
    BinarySensorEntityDescription(
        key="Plug_EV",
        name="Plugged on EV",
        device_class=BinarySensorDeviceClass.PLUG,
    ),
    BinarySensorEntityDescription(
        key="FS_on",
        name="Failsafe mode",
        device_class=BinarySensorDeviceClass.SAFETY,
    ),
    BinarySensorEntityDescription(
        key="State_on",
        name="Charging",
        device_class=BinarySensorDeviceClass.POWER,
    ),
    # optional
    BinarySensorEntityDescription(
        key="Plug_wallbox",
        name="Cable plugged on charging station",
        device_class=BinarySensorDeviceClass.PLUG,
        entity_registry_enabled_default=False,
    ),
    BinarySensorEntityDescription(
        key="Plug_locked",
        name="Cable locked",
        device_class=BinarySensorDeviceClass.PLUG,
        entity_registry_enabled_default=False,
    ),
    BinarySensorEntityDescription(
        key="Authreq",
        name="Authreq",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BinarySensorEntityDescription(
        key="AuthON",
        name="AuthON",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the keba charging station binary sensors from config entry."""
    keba: KebaKeContact = hass.data[DOMAIN][KEBA_CONNECTION]
    entities: list[KebaBinarySensor] = []

    wallbox = keba.get_wallbox(config_entry.data[CONF_HOST])
    entities.extend(
        [KebaBinarySensor(wallbox, description) for description in SENSOR_TYPES]
    )
    async_add_entities(entities, True)


class KebaBinarySensor(KebaBaseEntity, BinarySensorEntity):
    """The entity class for KEBA charging stations sensors."""

    def __init__(
        self,
        wallbox: Wallbox,
        description: BinarySensorEntityDescription,
    ) -> None:
        """Initialize the KEBA Sensor."""
        super().__init__(wallbox, description)
        self._attributes: dict[str, str] = {}

    @property
    def extra_state_attributes(self):
        """Return the state attributes of the binary sensor."""
        return self._attributes

    async def async_update(self):
        """Get latest cached states from the device."""
        key = self.entity_description.key
        self._attr_is_on = self._wallbox.get_value(key)

        if key == "FS_on":
            self._attr_is_on = not self._attr_is_on
            self._attributes["failsafe_timeout"] = str(
                self._wallbox.get_value("Tmo FS")
            )
            self._attributes["fallback_current"] = str(
                self._wallbox.get_value("Curr FS")
            )
