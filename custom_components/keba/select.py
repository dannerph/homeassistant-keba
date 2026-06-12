"""Support for KEBA charging station select entities."""
from typing import Any
from keba_kecontact.charging_station import ChargingStation
from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .const import DOMAIN, KEBA_CONNECTION
from .entity import KebaBaseEntity

SELECT_TYPES = [
    SelectEntityDescription(
        key="X2 phaseSwitch", 
        name="Phase Select",
        entity_category=EntityCategory.CONFIG,
        options=["1", "3"],
    ),
]

async def async_setup_entry(
    hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the KEBA charging station select entities from config entry."""
    keba = hass.data[DOMAIN][KEBA_CONNECTION]
    entities: list[KebaSelect] = []
    charging_station = keba.get_charging_station(config_entry.data["host"])
    entities.extend(
        [KebaSelect(charging_station, description) for description in SELECT_TYPES]
    )
    async_add_entities(entities, True)

class KebaSelect(KebaBaseEntity, SelectEntity):
    """The entity class for KEBA charging station select entities."""

    def __init__(
        self, charging_station: ChargingStation, description: SelectEntityDescription
    ) -> None:
        """Initialize the KEBA Select."""
        super().__init__(charging_station, description)
        self._attr_options = description.options

    @property
    def current_option(self) -> str | None:
        """Return the current selected option."""
        x2_value = self._charging_station.get_value("X2 phaseSwitch")
        if x2_value == 1:
            return "3"
        elif x2_value == 0:
            return "1"
        return None

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        if not self._charging_station.device_info.has_phase_switch_x2():
            raise NotImplementedError("Phase Switching is not available for this charging station")

        # X2 switch
        if option == "3":
            await self._charging_station.x2(three_phases=True)
        elif option == "1":
            await self._charging_station.x2(three_phases=False)