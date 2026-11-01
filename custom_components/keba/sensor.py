"""Support for KEBA charging station sensors."""

from collections.abc import Mapping
from typing import Any

from keba_kecontact.charging_station import ChargingStation
from keba_kecontact.connection import KebaKeContact

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
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

# Sensors that are only applicable to UDP-based devices (not P40)
UDP_ONLY_SENSORS = {
    "Setenergy",  # Energy target - P40 doesn't support setting energy limits
    "Curr timer",  # Planned current - works differently in P40
    "Tmo CT",  # Time until planned current - not applicable to P40
    "Error1",  # UDP-specific error format
    "Error2",  # UDP-specific error format
    "Enable sys",  # UDP-specific
    "Enable user",  # UDP-specific
    "Sec",  # Uptime - not available in P40 API
    "X2 phaseSwitch source",  # UDP-specific
}

SENSOR_TYPES = [
    # default
    SensorEntityDescription(key="State_details", name="Status", icon="mdi:ev-station"),
    SensorEntityDescription(
        key="P",
        name="Charging power",
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="Curr user",
        name="Set current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
    ),
    SensorEntityDescription(
        key="Setenergy",
        name="Energy target",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
    ),
    SensorEntityDescription(
        key="E pres",  # codespell:ignore pres
        name="Session energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
    ),
    SensorEntityDescription(
        key="E total",
        name="Total energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    # optional
    SensorEntityDescription(
        key="RFID tag",
        name="RFID tag",
        icon="mdi:card-account-details-outline",
        entity_registry_enabled_default=False,
    ),
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
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="U2",
        name="Voltage at phase 2",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="U3",
        name="Voltage at phase 3",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="I1",
        name="Current at phase 1",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="I2",
        name="Current at phase 2",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="I3",
        name="Current at phase 3",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="Max curr",
        name="Maximum current (system)",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="Max curr %",
        name="Maximum current % (system)",
        native_unit_of_measurement=PERCENTAGE,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="Curr HW",
        name="Current hardware",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="Curr timer",
        name="Planned current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="Tmo CT",
        name="Time until planned current is set",
        native_unit_of_measurement=UnitOfTime.SECONDS,
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
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
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
        native_unit_of_measurement=UnitOfTime.SECONDS,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="X2 phaseSwitch source",
        name="X2 phaseSwitch source",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
]

# P40-specific sensors
P40_SENSOR_TYPES = [
    SensorEntityDescription(
        key="Curr offered",
        name="Current Offered",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="Temperature",
        name="Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="Phases Supported",
        name="Phases Supported",
        icon="mdi:sine-wave",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="Phase Used",
        name="Phase Used",
        icon="mdi:sine-wave",
    ),
    SensorEntityDescription(
        key="Raw State",
        name="Raw State",
        icon="mdi:state-machine",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="Error Code",
        name="Error Code",
        icon="mdi:alert-circle",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="Model",
        name="Model",
        icon="mdi:information",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="Firmware Version",
        name="Firmware Version",
        icon="mdi:chip",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Keba charging station sensors from config entry."""
    device_type = config_entry.data.get(CONF_DEVICE_TYPE, DEVICE_TYPE_UDP)

    if device_type == DEVICE_TYPE_P40:
        charging_station = hass.data[DOMAIN][CHARGING_STATIONS][config_entry.entry_id]
        # For P40: use common sensors (excluding UDP-only) plus P40-specific sensors
        sensor_types = [
            desc for desc in SENSOR_TYPES if desc.key not in UDP_ONLY_SENSORS
        ] + P40_SENSOR_TYPES
    else:
        keba: KebaKeContact = hass.data[DOMAIN][KEBA_CONNECTION]
        charging_station = keba.get_charging_station(config_entry.data[CONF_HOST])
        # For UDP: use all common sensors
        sensor_types = SENSOR_TYPES

    entities: list[KebaSensor] = []
    entities.extend(
        [KebaSensor(charging_station, description) for description in sensor_types]
    )
    async_add_entities(entities, True)


class KebaSensor(KebaBaseEntity, SensorEntity):
    """The entity class for KEBA charging stations sensors."""

    def __init__(
        self,
        charging_station: ChargingStation,
        description: SensorEntityDescription,
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
        self._attr_native_value = self._charging_station.get_value(
            self.entity_description.key
        )

        if self.entity_description.key == "Curr user":
            self._attributes["max_current_hardware"] = self._charging_station.get_value(
                "Curr HW"
            )
