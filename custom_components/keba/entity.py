"""Base for all keba entities."""

from __future__ import annotations

from keba_kecontact.charging_station import ChargingStation

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity, EntityDescription

from .const import DOMAIN


class KebaBaseEntity(Entity):
    """Common base for Keba charging station entities."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self,
        charging_station: ChargingStation,
        description: EntityDescription,
    ) -> None:
        """Initialize sensor."""
        self._charging_station = charging_station
        self.entity_description = description

        cs_info = self._charging_station.device_info

        self._attr_name = f"{cs_info.manufacturer} {cs_info.model} {description.name}"
        self._attr_unique_id = f"{DOMAIN}-{cs_info.device_id}-{description.key}"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, cs_info.device_id)},
            manufacturer=cs_info.manufacturer,
            model=cs_info.model,
            name=f"{cs_info.manufacturer} {cs_info.model}",
            sw_version=cs_info.sw_version,
            configuration_url=cs_info.webconfigurl,
        )

    def update_callback(self, *args) -> None:
        """Schedule a state update."""
        self.schedule_update_ha_state(True)

    async def async_added_to_hass(self) -> None:
        """Add callback after being added to hass.

        Show latest data after startup.
        """
        self._charging_station.add_callback(self.update_callback)
