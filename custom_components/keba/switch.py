"""Support for KEBA charging station switches."""
from typing import Any
from keba_kecontact.charging_station import ChargingStation
from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .const import DOMAIN, KEBA_CONNECTION
from .entity import KebaBaseEntity

SWITCH_TYPES = [
    SwitchEntityDescription(
        key="X2 phaseSwitch",
        name="X2 Phase Switch",
        entity_category=EntityCategory.CONFIG,
    ),
]

async def async_setup_entry(
    hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the KEBA charging station switches from config entry."""
    keba = hass.data[DOMAIN][KEBA_CONNECTION]
    entities: list[KebaSwitch] = []
    charging_station = keba.get_charging_station(config_entry.data["host"])
    entities.extend(
        [KebaSwitch(charging_station, description) for description in SWITCH_TYPES]
    )
    async_add_entities(entities, True)

class KebaSwitch(KebaBaseEntity, SwitchEntity):
    """The entity class for KEBA charging station switches."""

    def __init__(
        self, charging_station: ChargingStation, description: SwitchEntityDescription
    ) -> None:
        """Initialize the KEBA Switch."""
        super().__init__(charging_station, description)

    @property
    def is_on(self) -> bool:
        """Return true if switch is on (X2 = 1)."""
        x2_value = self._charging_station.get_value("X2 phaseSwitch")
        return bool(x2_value == 1) if x2_value is not None else False

    async def async_turn_on(self) -> None:
        """Turn the switch on (set X2 to 1)."""
        if not self._charging_station.device_info.has_phase_switch_x2():
            raise NotImplementedError("Phase Switching is not available for this charging station")

        await self._charging_station.x2(three_phases=True)

    async def async_turn_off(self) -> None:
        """Turn the switch off (set X2 to 0)."""
        if not self._charging_station.device_info.has_phase_switch_x2():
            raise NotImplementedError("Phase Switching is not available for this charging station")

        await self._charging_station.x2(three_phases=False)