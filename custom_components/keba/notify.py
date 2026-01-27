"""Support for Keba notifications."""

from keba_kecontact.charging_station import ChargingStation

from homeassistant.components.notify import NotifyEntity, NotifyEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
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


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the keba entity platform."""
    device_type = config_entry.data.get(CONF_DEVICE_TYPE, DEVICE_TYPE_UDP)

    if device_type == DEVICE_TYPE_P40:
        charging_station = hass.data[DOMAIN][CHARGING_STATIONS][config_entry.entry_id]
    else:
        keba = hass.data[DOMAIN][KEBA_CONNECTION]
        charging_station = keba.get_charging_station(config_entry.data[CONF_HOST])
    async_add_entities(
        [
            KebaNotifyEntity(
                charging_station,
                NotifyEntityDescription(
                    key="display",
                    name="Display",
                    device_class="display",
                ),
            )
        ]
    )


class KebaNotifyEntity(KebaBaseEntity, NotifyEntity):
    """Implement keba notification platform."""

    def __init__(
        self,
        charging_station: ChargingStation,
        description: NotifyEntityDescription,
    ) -> None:
        """Initialize the KEBA Sensor."""
        super().__init__(charging_station, description)

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        """Send the message."""
        try:
            await self._charging_station.display(message)
        except NotImplementedError as ex:
            raise ServiceValidationError(
                "Display is not available on selected charging station"
            ) from ex
