"""Support for KEBA charging stations."""
from __future__ import annotations

import logging

from keba_kecontact.chargingstation import ChargingStation, KebaService
from keba_kecontact.connection import KebaKeContact, SetupError, create_keba_connection

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_ENTITY_ID,
    CONF_HOST,
    CONF_NAME,
    Platform,
)
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry, discovery
from homeassistant.helpers.entity import DeviceInfo, Entity, EntityDescription
from homeassistant.helpers.issue_registry import IssueSeverity, async_create_issue
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import slugify

from .const import (
    CHARGING_STATIONS,
    CONF_FS,
    CONF_FS_FALLBACK,
    CONF_FS_PERSIST,
    CONF_FS_TIMEOUT,
    CONF_RFID,
    CONF_RFID_CLASS,
    DATA_HASS_CONFIG,
    DOMAIN,
    KEBA_CONNECTION,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.LOCK,
    Platform.BUTTON,
    Platform.NOTIFY,
    Platform.SENSOR,
    Platform.NUMBER,
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the KEBA charging station component from configuration.yaml."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][DATA_HASS_CONFIG] = config

    if DOMAIN in config:
        async_create_issue(
            hass,
            DOMAIN,
            "deprecated_yaml",
            is_fixable=False,
            severity=IssueSeverity.WARNING,
            translation_key="deprecated_yaml",
        )
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_IMPORT}, data=config[DOMAIN]
            )
        )
    return True


async def _async_set_failsafe(hass: HomeAssistant, entry: ConfigEntry):
    if CONF_FS in entry.options:
        charging_station = hass.data[DOMAIN][CHARGING_STATIONS][entry.entry_id]
        try:
            hass.loop.create_task(
                charging_station.set_failsafe(
                    entry.options[CONF_FS],
                    entry.options[CONF_FS_TIMEOUT],
                    entry.options[CONF_FS_FALLBACK],
                    entry.options[CONF_FS_PERSIST],
                )
            )
        except ValueError as ex:
            _LOGGER.warning("Could not set failsafe mode %s", ex)


def _get_charging_station(
    hass: HomeAssistant, device_id: str
) -> ChargingStation | None:
    # Get and check home assistant device linked to device_id
    device = device_registry.async_get(hass).async_get(device_id)
    if not device:
        _LOGGER.error("Could not find a device for id: %s", device_id)
        return None

    # Get and check config_entry of givne home assistant device
    config_entry = hass.config_entries.async_get_entry(
        next(iter(device.config_entries))
    )
    if config_entry is None:
        _LOGGER.fatal("Config entry for device %s not valid", str(device))
        return None

    # Get and check keba charging station from host in config_entry
    keba = hass.data[DOMAIN][KEBA_CONNECTION]
    host = config_entry.data[CONF_HOST]
    charging_station = keba.get_charging_station(host)
    if charging_station is None:
        _LOGGER.error("Could not find a charging station with host %s", host)
        return None

    return charging_station


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up KEBA charging station from a config entry."""
    keba = await get_keba_connection(hass)
    try:
        charging_station = await keba.setup_charging_station(entry.data[CONF_HOST])
    except SetupError as exc:
        raise ConfigEntryNotReady(f"{entry.data[CONF_HOST]} not reachable") from exc

    hass.data[DOMAIN][CHARGING_STATIONS][entry.entry_id] = charging_station

    # Set failsafe mode at start up of Home Assistant if configured in options
    await _async_set_failsafe(hass, entry)

    # Add update listener for config entry changes (options)
    entry.async_on_unload(entry.add_update_listener(update_listener))

    # Register services to hass
    async def execute_service(call: ServiceCall) -> None:
        """Execute a service for a charging station."""
        device_id: str = str(call.data.get(CONF_DEVICE_ID))
        charging_station: ChargingStation | None = _get_charging_station(
            hass, device_id
        )
        if charging_station is None:
            return

        function_call = getattr(charging_station, call.service)

        additional_args = {}
        if call.service in ["start", "stop"]:
            if (
                CONF_RFID not in call.data
                and CONF_RFID in entry.options
                and entry.options[CONF_RFID] != ""
            ):
                additional_args[CONF_RFID] = entry.options[CONF_RFID]
            if (
                CONF_RFID_CLASS not in call.data
                and CONF_RFID_CLASS in entry.options
                and entry.options[CONF_RFID_CLASS] != ""
            ):
                additional_args[CONF_RFID_CLASS] = entry.options[CONF_RFID_CLASS]

        await function_call(**call.data, **additional_args)

    for service in charging_station.device_info.available_services():
        if service != KebaService.DISPLAY:
            hass.services.async_register(DOMAIN, service.value, execute_service)
        else:
            # set up notify platform, no entry support for notify platform yet,
            # have to use discovery to load platform.
            hass.async_create_task(
                discovery.async_load_platform(
                    hass,
                    Platform.NOTIFY,
                    DOMAIN,
                    {CONF_NAME: DOMAIN, CONF_ENTITY_ID: entry.entry_id},
                    hass.data[DOMAIN][DATA_HASS_CONFIG],
                )
            )

    # Set up all platforms except notify
    await hass.config_entries.async_forward_entry_setups(
        entry, [platform for platform in PLATFORMS if platform != Platform.NOTIFY]
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    keba = hass.data[DOMAIN][KEBA_CONNECTION]

    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, [platform for platform in PLATFORMS if platform != Platform.NOTIFY]
    )

    # Remove notify
    charging_station = keba.get_charging_station(entry.data[CONF_HOST])
    if KebaService.DISPLAY in charging_station.device_info.available_services():
        hass.services.async_remove(
            Platform.NOTIFY, f"{DOMAIN}_{slugify(charging_station.device_info.model)}"
        )

    # Only remove services if it is the last charging station
    if len(hass.data[DOMAIN][CHARGING_STATIONS]) == 1:
        _LOGGER.debug("Removing last charging station, cleanup services and notify")

        for service in charging_station.device_info.available_services():
            if service == KebaService.DISPLAY:
                hass.services.async_remove(Platform.NOTIFY, DOMAIN)
            else:
                hass.services.async_remove(DOMAIN, service.value)

    if unload_ok:
        keba.remove_charging_station(entry.data[CONF_HOST])
        hass.data[DOMAIN][CHARGING_STATIONS].pop(entry.entry_id)

    return unload_ok


async def update_listener(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(config_entry.entry_id)


async def get_keba_connection(hass: HomeAssistant) -> KebaKeContact:
    """Set up internal keba connection (ensure same keba connection instance)."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(CHARGING_STATIONS, {})

    if KEBA_CONNECTION not in hass.data[DOMAIN]:
        hass.data[DOMAIN][KEBA_CONNECTION] = await create_keba_connection(hass.loop)

    return hass.data[DOMAIN][KEBA_CONNECTION]


class KebaBaseEntity(Entity):
    """Common base for Keba charging station entities."""

    _attr_should_poll = False

    def __init__(
        self,
        charging_station: ChargingStation,
        description: EntityDescription,
    ) -> None:
        """Initialize sensor."""
        self._charging_station = charging_station
        self.entity_description = description

        wb_info = self._charging_station.device_info

        self._attr_name = f"{wb_info.manufacturer} {wb_info.model} {description.name}"
        self._attr_unique_id = f"{DOMAIN}-{wb_info.device_id}-{description.key}"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, wb_info.device_id)},
            manufacturer=wb_info.manufacturer,
            model=wb_info.model,
            name=f"{wb_info.manufacturer} {wb_info.model}",
            sw_version=wb_info.sw_version,
            configuration_url=wb_info.webconfigurl,
        )

    def update_callback(self, *args) -> None:
        """Schedule a state update."""
        self.schedule_update_ha_state(True)

    async def async_added_to_hass(self) -> None:
        """Add callback after being added to hass.

        Show latest data after startup.
        """
        self._charging_station.add_callback(self.update_callback)
