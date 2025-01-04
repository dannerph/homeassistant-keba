"""Support for KEBA charging stations."""

import logging

from keba_kecontact import create_keba_connection
from keba_kecontact.charging_station import ChargingStation, KebaService
from keba_kecontact.connection import KebaKeContact, SetupError
import voluptuous as vol

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import CONF_DEVICE_ID, CONF_HOST, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import device_registry as dr
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.issue_registry import IssueSeverity, async_create_issue
from homeassistant.helpers.typing import ConfigType

from .const import (
    CHARGING_STATIONS,
    CONF_RFID,
    CONF_RFID_CLASS,
    DATA_HASS_CONFIG,
    DOMAIN,
    KEBA_CONNECTION,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.LOCK,
    Platform.NOTIFY,
    Platform.NUMBER,
    Platform.SENSOR,
]
CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_HOST): cv.string,
                vol.Optional(CONF_RFID, default="00845500"): cv.string,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


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


def _get_charging_station(
    hass: HomeAssistant, device_id: str
) -> ChargingStation | None:
    # Get and check home assistant device linked to device_id
    device = dr.async_get(hass).async_get(device_id)
    if not device:
        _LOGGER.error("Could not find a device for id: %s", device_id)
        return None

    # Get and check config_entry of given home assistant device
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
        parameters = call.data.copy()
        parameters.pop(CONF_DEVICE_ID)
        try:
            await function_call(**parameters, **additional_args)
        except NotImplementedError as ex:
            raise ServiceValidationError(
                "Service is not available on this charging station"
            ) from ex

    for service in charging_station.device_info.available_services():
        if service != KebaService.DISPLAY:
            hass.services.async_register(DOMAIN, service.value, execute_service)

    # Set up all platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    keba = hass.data[DOMAIN][KEBA_CONNECTION]

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Remove notify
    charging_station = keba.get_charging_station(entry.data[CONF_HOST])
    # if KebaService.DISPLAY in charging_station.device_info.available_services():
    #     hass.services.async_remove(
    #         Platform.NOTIFY, f"{DOMAIN}_{slugify(charging_station.device_info.model)}"
    #     )

    # Only remove services if it is the last charging station
    if len(hass.data[DOMAIN][CHARGING_STATIONS]) == 1:
        _LOGGER.debug("Removing last charging station, cleanup services")

        for service in charging_station.device_info.available_services():
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
