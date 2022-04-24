"""Support for KEBA charging stations."""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry, discovery
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.entity import DeviceInfo, Entity, EntityDescription
from homeassistant.util import slugify
from homeassistant.config_entries import ConfigEntry, SOURCE_IMPORT
from homeassistant.const import CONF_NAME, Platform, CONF_DEVICE_ID

from homeassistant.components.notify import DOMAIN as NOTIFY_DOMAIN

from .const import (
    CONF_FS_PERSIST,
    CONF_RFID,
    CONF_RFID_CLASS,
    DOMAIN,
    KEBA_CONNECTION,
    WALLBOXES,
    DATA_HASS_CONFIG,
    CONF_FS,
    CONF_FS_FALLBACK,
    CONF_FS_TIMEOUT,
)

from keba_kecontact.connection import KebaKeContact, SetupError
from keba_kecontact.wallbox import Wallbox


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
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_IMPORT}, data=config[DOMAIN]
            )
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up KEBA charging station from a config entry."""
    keba = await setup_keba_connection(hass)
    try:
        wallbox = await keba.setup_wallbox(entry.data["host"])
    except SetupError as exc:
        raise ConfigEntryNotReady(f"{entry.data['host']} not reachable") from exc

    hass.data[DOMAIN][WALLBOXES][entry.entry_id] = wallbox

    # Set failsafe mode at start up of Home Assistant
    try:
        fs_timeout = entry.options[CONF_FS_TIMEOUT] if entry.options[CONF_FS] else 0
        fs_fallback = entry.options[CONF_FS_FALLBACK]
        fs_persist = entry.options[CONF_FS_PERSIST]
        hass.loop.create_task(wallbox.set_failsafe(fs_timeout, fs_fallback, fs_persist))
    except KeyError:
        _LOGGER.debug(
            "Options for charging station %s not available", wallbox.device_info.model
        )
    except ValueError as ex:
        _LOGGER.warning("Could not set failsafe mode %s", ex)

    # Add update listener for config entry changes (options)
    entry.async_on_unload(entry.add_update_listener(update_listener))

    # Register services to hass
    async def execute_service(call: ServiceCall) -> None:
        """Execute a service for a wallbox."""
        device_id: str | None = call.data.get(CONF_DEVICE_ID)

        wallbox: Wallbox | None = None

        # from device_id to wallbox
        if not (device := device_registry.async_get(hass).async_get(device_id)):
            _LOGGER.error("Could not find a device for id: %s", device_id)
            return
        host = next(iter(device.identifiers))[1]
        if not (wallbox := keba.get_wallbox(host)):
            _LOGGER.error("Could not find a charging station with host %s", host)
            return

        function_call = getattr(wallbox, call.service)

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

    for service in wallbox.device_info.available_services():
        if service == "display":
            # set up notify platform, no entry support for notify platform yet,
            # have to use discovery to load platform.
            hass.async_create_task(
                discovery.async_load_platform(
                    hass,
                    NOTIFY_DOMAIN,
                    DOMAIN,
                    {CONF_NAME: DOMAIN},
                    hass.data[DOMAIN][DATA_HASS_CONFIG],
                )
            )
        else:
            hass.services.async_register(DOMAIN, service, execute_service)

    # Load platforms
    hass.config_entries.async_setup_platforms(
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
    wallbox = keba.get_wallbox(entry.data["host"])
    if "display" in wallbox.device_info.available_services():
        hass.services.async_remove(
            NOTIFY_DOMAIN, f"{DOMAIN}_{slugify(wallbox.device_info.model)}"
        )

    # Only remove services if it is the last wallbox
    if len(hass.data[DOMAIN][WALLBOXES]) == 1:
        _LOGGER.debug("Removing last charging station, cleanup services and notify")

        for service in wallbox.device_info.available_services():
            if service == "dispaly":
                hass.services.async_remove(NOTIFY_DOMAIN, DOMAIN)
            else:
                hass.services.async_remove(DOMAIN, service)

    if unload_ok:
        keba.remove_wallbox(entry.data["host"])
        hass.data[DOMAIN][WALLBOXES].pop(entry.entry_id)

    return unload_ok


async def update_listener(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(config_entry.entry_id)


async def setup_keba_connection(hass: HomeAssistant) -> bool:
    """Set up internal keba connection (ensure same keba connection instance)."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(WALLBOXES, {})

    if KEBA_CONNECTION not in hass.data[DOMAIN]:
        hass.data[DOMAIN][KEBA_CONNECTION] = KebaKeContact(hass.loop)

    return hass.data[DOMAIN][KEBA_CONNECTION]


class KebaBaseEntity(Entity):
    """Common base for Keba Wallbox entities."""

    _attr_should_poll = False

    def __init__(
        self,
        wallbox: Wallbox,
        description: EntityDescription,
    ) -> None:
        """Initialize sensor."""
        self._wallbox = wallbox
        self.entity_description = description

        wb_info = self._wallbox.device_info

        self._attr_name = f"{wb_info.manufacturer} {wb_info.model} {description.name}"
        self._attr_unique_id = (
            f"{DOMAIN}-{wb_info.device_id}-{description.key}"
        )

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, wb_info.device_id)},
            manufacturer=wb_info.manufacturer,
            model=wb_info.model,
            name=f"{wb_info.manufacturer} {wb_info.model}",
            sw_version=wb_info.sw_version,
            configuration_url=wb_info.webconfigurl,
        )

    def update_callback(self, wallbox: Wallbox, data) -> None:
        """Schedule a state update."""
        self.schedule_update_ha_state(True)

    async def async_added_to_hass(self) -> None:
        """Add callback after being added to hass.

        Show latest data after startup.
        """
        self._wallbox.add_callback(self.update_callback)
