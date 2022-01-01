"""Support for KEBA charging station sensors."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    ELECTRIC_CURRENT_AMPERE,
    ELECTRIC_POTENTIAL_VOLT,
    ENERGY_KILO_WATT_HOUR,
    PERCENTAGE,
    POWER_KILO_WATT,
    TIME_SECONDS,
)

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory

from . import KebaBaseEntity
from .const import DOMAIN, KEBA_CONNECTION

from keba_kecontact.connection import KebaKeContact
from keba_kecontact.wallbox import Wallbox

SENSOR_TYPES = [
    # default
    SensorEntityDescription(key="State_details", name="Status", icon="mdi:ev-station"),
    SensorEntityDescription(
        key="P",
        name="Charging power",
        native_unit_of_measurement=POWER_KILO_WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="Curr user",
        name="Set current",
        native_unit_of_measurement=ELECTRIC_CURRENT_AMPERE,
        device_class=SensorDeviceClass.CURRENT,
    ),
    SensorEntityDescription(
        key="Setenergy",
        name="Energy target",
        native_unit_of_measurement=ENERGY_KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
    ),
    SensorEntityDescription(
        key="E pres",
        name="Session energy",
        native_unit_of_measurement=ENERGY_KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
    ),
    SensorEntityDescription(
        key="E total",
        name="Total energy",
        native_unit_of_measurement=ENERGY_KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    # optional
    SensorEntityDescription(
        key="RFID class",
        name="RFID class",
        icon="mdi:card-account-details-outline",
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="PF",
        name="Power factor",
        device_class=SensorDeviceClass.POWER_FACTOR,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="U1",
        name="Voltage at phase 1",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=ELECTRIC_POTENTIAL_VOLT,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="U2",
        name="Voltage at phase 2",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=ELECTRIC_POTENTIAL_VOLT,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="U3",
        name="Voltage at phase 3",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=ELECTRIC_POTENTIAL_VOLT,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="I1",
        name="Current at phase 1",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=ELECTRIC_CURRENT_AMPERE,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="I2",
        name="Current at phase 2",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=ELECTRIC_CURRENT_AMPERE,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="I3",
        name="Current at phase 3",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=ELECTRIC_CURRENT_AMPERE,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="Max curr",
        name="Maximum current (system)",
        native_unit_of_measurement=ELECTRIC_CURRENT_AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="Max curr %",
        name="Maximum current % (system)",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.CURRENT,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="Curr HW",
        name="Current hardware",
        native_unit_of_measurement=ELECTRIC_CURRENT_AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="Curr timer",
        name="Planned current",
        native_unit_of_measurement=ELECTRIC_CURRENT_AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="Tmo CT",
        name="Time until planned current is set",
        native_unit_of_measurement=TIME_SECONDS,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="Output",
        name="Output",
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="Input",
        name="Input",
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="Error1",
        name="Error1",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="Error2",
        name="Error2",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="State",
        name="State",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="Plug",
        name="Plug",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="Enable sys",
        name="Enable sys",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="Enable user",
        name="Enable user",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="Session ID",
        name="Session ID",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="E start",
        name="E start",
        entity_registry_enabled_default=False,
        native_unit_of_measurement=ENERGY_KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="started",
        name="Session start time",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="ended",
        name="Session end time",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="reason",
        name="Session end reason",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="Sec",
        name="Uptime",
        entity_registry_enabled_default=False,
        native_unit_of_measurement=TIME_SECONDS,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the BMW ConnectedDrive sensors from config entry."""
    keba: KebaKeContact = hass.data[DOMAIN][KEBA_CONNECTION]
    entities: list[KebaSensor] = []

    for wallbox in keba.get_wallboxes():
        entities.extend(
            [KebaSensor(wallbox, description) for description in SENSOR_TYPES]
        )
    async_add_entities(entities, True)


class KebaSensor(KebaBaseEntity, SensorEntity):
    """The entity class for KEBA charging stations sensors."""

    def __init__(
        self,
        wallbox: Wallbox,
        description: SensorEntityDescription,
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
        self._attr_native_value = self._wallbox.get_value(self.entity_description.key)

        if self.entity_description.key == "Curr user":
            self._attributes["max_current_hardware"] = self._wallbox.get_value(
                "Curr HW"
            )
