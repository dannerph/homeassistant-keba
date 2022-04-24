"""Number entities for keba."""

from homeassistant.components.number import NumberEntity, NumberEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from homeassistant.const import ELECTRIC_CURRENT_AMPERE, CONF_HOST

from . import KebaBaseEntity
from .const import DOMAIN, KEBA_CONNECTION

from keba_kecontact.connection import KebaKeContact
from keba_kecontact.wallbox import Wallbox


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Keba charging station number from config entry."""
    keba: KebaKeContact = hass.data[DOMAIN][KEBA_CONNECTION]
    entities: list[KebaNumber] = []

    wallbox = keba.get_wallbox(config_entry.data[CONF_HOST])
    number_description = NumberEntityDescription(
        key="Curr user",
        name="Charging current",
        unit_of_measurement=ELECTRIC_CURRENT_AMPERE,
    )
    entities.extend([KebaNumber(wallbox, number_description)])
    async_add_entities(entities, True)


class KebaNumber(KebaBaseEntity, NumberEntity):
    """Representation of a MusicCast Number entity."""

    def __init__(
        self,
        wallbox: Wallbox,
        description: NumberEntityDescription,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(wallbox, description)
        self._attr_min_value = 6
        self._attr_max_value = (
            63
            if self._wallbox.get_value("Curr HW") is None
            else self._wallbox.get_value("Curr HW")
        )
        self._attr_step = 1

    @property
    def value(self):
        """Return the current value."""
        return self._wallbox.get_value(self.entity_description.key)

    async def async_update(self):
        """Get latest cached states from the device."""
        self._attr_max_value = self._wallbox.get_value("Curr HW")

    async def async_set_value(self, value: float):
        """Set a new value."""
        await self._wallbox.set_current(value)
