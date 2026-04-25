"""Support for KEBA charging stations."""

import logging

from keba_kecontact import create_keba_connection
from keba_kecontact.charging_station import ChargingStation, KebaService
from keba_kecontact.connection import KebaKeContact, SetupError
import voluptuous as vol

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import CONF_DEVICE_ID, CONF_HOST, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import device_registry as dr
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.issue_registry import IssueSeverity, async_create_issue
from homeassistant.helpers.typing import ConfigType

from .const import (
    CHARGING_STATIONS,
    CONF_DEVICE_TYPE,
    CONF_RFID,
    CONF_RFID_CLASS,
    DATA_HASS_CONFIG,
    DEVICE_TYPE_P40,
    DEVICE_TYPE_UDP,
    DOMAIN,
    KEBA_CONNECTION,
)
from .p40_api import P40ApiClient
from .p40_charging_station import P40ChargingStation

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
        device.primary_config_entry or next(iter(device.config_entries))
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
    device_type = entry.data.get(CONF_DEVICE_TYPE, DEVICE_TYPE_UDP)
    host = entry.data[CONF_HOST]

    # Ensure CHARGING_STATIONS dictionary exists
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(CHARGING_STATIONS, {})

    if device_type == DEVICE_TYPE_P40:
        # Set up P40/P40 Pro charging station
        password = entry.data.get(CONF_PASSWORD, "")
        _LOGGER.debug("Setting up P40 device at %s", host)

        api_client = P40ApiClient(host)
        try:
            _LOGGER.debug("Attempting login to P40 at %s", host)
            if not await api_client.login(password=password):
                _LOGGER.error("Failed to authenticate with P40 at %s", host)
                raise ConfigEntryNotReady(f"Failed to authenticate with P40 at {host}")

            _LOGGER.debug("Login successful, getting device info")
            # Get device info to find serial number
            device_info = await api_client.get_device_info()
            if not device_info:
                _LOGGER.error("Failed to get device info from P40 at %s", host)
                raise ConfigEntryNotReady(f"Failed to get device info from P40 at {host}")

            _LOGGER.debug("Device info retrieved: %s", device_info)

            _LOGGER.debug("Getting wallbox information")
            # Get wallbox to find serial number
            wallbox = await api_client.get_wallbox()
            if not wallbox:
                _LOGGER.error("No wallbox found on P40 at %s", host)
                raise ConfigEntryNotReady(f"No wallbox found on P40 at {host}")

            _LOGGER.debug("Wallbox retrieved: %s", wallbox)
            serial_number = wallbox.get("serialNumber", device_info.device_id)
            _LOGGER.debug("Using serial number: %s", serial_number)

            # Create P40 charging station wrapper
            _LOGGER.debug("Creating P40ChargingStation wrapper")
            charging_station = P40ChargingStation(api_client, serial_number)

            _LOGGER.debug("Initializing P40ChargingStation")
            await charging_station.initialize()

            _LOGGER.info("P40 charging station setup successful for %s", host)

        except Exception as exc:
            _LOGGER.error("Exception during P40 setup for %s: %s (type: %s)",
                         host, str(exc), type(exc).__name__, exc_info=True)
            await api_client.close()
            raise ConfigEntryNotReady(f"{host} not reachable") from exc
    else:
        # Set up UDP-based charging station (legacy)
        keba = await get_keba_connection(hass)
        try:
            charging_station = await keba.setup_charging_station(host)
        except SetupError as exc:
            raise ConfigEntryNotReady(f"{host} not reachable") from exc

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

    # Register services (only for UDP-based devices that have available_services)
    if device_type == DEVICE_TYPE_UDP and hasattr(charging_station.device_info, "available_services"):
        for service in charging_station.device_info.available_services():
            if service != KebaService.DISPLAY:
                hass.services.async_register(DOMAIN, service.value, execute_service)
    elif device_type == DEVICE_TYPE_P40:
        # Register all services for P40
        # Note: set_energy and set_charging_power will raise NotImplementedError
        # as P40 API doesn't support these features
        p40_services = [
            "start",
            "stop",
            "set_current",
            "set_failsafe",
            "set_energy",
            "set_charging_power",
            "set_output",
            "x2src",
            "x2",
        ]
        for service_name in p40_services:
            if not hass.services.has_service(DOMAIN, service_name):
                hass.services.async_register(DOMAIN, service_name, execute_service)

    # Set up all platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    device_type = entry.data.get(CONF_DEVICE_TYPE, DEVICE_TYPE_UDP)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Get charging station
    charging_station = hass.data[DOMAIN][CHARGING_STATIONS].get(entry.entry_id)

    if device_type == DEVICE_TYPE_P40:
        # Clean up P40 charging station
        if charging_station and isinstance(charging_station, P40ChargingStation):
            await charging_station.close()
            # Also close the API client
            if hasattr(charging_station, "_api"):
                await charging_station._api.close()
    else:
        # Clean up UDP-based charging station
        keba = hass.data[DOMAIN][KEBA_CONNECTION]

        # Only remove services if it is the last charging station
        if len(hass.data[DOMAIN][CHARGING_STATIONS]) == 1:
            _LOGGER.debug("Removing last charging station, cleanup services")

            if charging_station and hasattr(charging_station, "device_info"):
                for service in charging_station.device_info.available_services():
                    hass.services.async_remove(DOMAIN, service.value)

        if unload_ok and charging_station:
            keba.remove_charging_station(entry.data[CONF_HOST])

    if unload_ok:
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
