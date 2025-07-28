"""Support for KEBA charging station binary sensors."""

from collections.abc import Mapping
from typing import Any

from keba_kecontact.charging_station import ChargingStation
from keba_kecontact.connection import KebaKeContact

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, KEBA_CONNECTION
from .entity import KebaBaseEntity

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
    BinarySensorEntityDescription(
        key="Enable user",
        name="Enable user",
        device_class=BinarySensorDeviceClass.POWER,
    ),
    # optional
    BinarySensorEntityDescription(
        key="Plug_charging_station",
        name="Cable plugged on charging station",
        device_class=BinarySensorDeviceClass.PLUG,
        entity_registry_enabled_default=False,
    ),
    BinarySensorEntityDescription(
        key="Plug_locked",
        name="Cable locked",
        device_class=BinarySensorDeviceClass.LOCK,
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
    BinarySensorEntityDescription(
        key="X2 phaseSwitch",
        name="X2 Phase Switch",
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

    charging_station = keba.get_charging_station(config_entry.data[CONF_HOST])
    entities.extend(
        [
            KebaBinarySensor(charging_station, description)
            for description in SENSOR_TYPES
        ]
    )
    async_add_entities(entities, True)


class KebaBinarySensor(KebaBaseEntity, BinarySensorEntity):
    """The entity class for KEBA charging stations sensors."""

    def __init__(
        self,
        charging_station: ChargingStation,
        description: BinarySensorEntityDescription,
    ) -> None:
        """Initialize the KEBA Sensor."""
        super().__init__(charging_station, description)
        self._attributes: dict[str, str] = {}

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return the state attributes of the binary sensor."""
        return self._attributes

    async def async_update(self) -> None:
        """Get latest cached states from the device."""
        key = self.entity_description.key
        self._attr_is_on = self._charging_station.get_value(key)

        if key == "FS_on":
            self._attr_is_on = not self._attr_is_on
            self._attributes["failsafe_timeout"] = str(
                self._charging_station.get_value("Tmo FS")
            )
            self._attributes["fallback_current"] = str(
                self._charging_station.get_value("Curr FS")
            )
