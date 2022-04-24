"""Config flow for keba integration."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
import logging

from homeassistant import config_entries, core, exceptions
from homeassistant.const import CONF_HOST
from homeassistant.data_entry_flow import FlowResult
from homeassistant.core import callback

from homeassistant.components import network
from ipaddress import ip_network

import homeassistant.helpers.config_validation as cv

from .const import (
    CONF_FS_PERSIST,
    CONF_RFID,
    CONF_RFID_CLASS,
    DOMAIN,
    CONF_FS,
    CONF_FS_TIMEOUT,
    CONF_FS_FALLBACK,
)
from . import setup_keba_connection
from keba_kecontact.connection import SetupError

_LOGGER = logging.getLogger(__name__)

STEP_HOST_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_HOST): str,
    }
)


async def validate_input(
    hass: core.HomeAssistant, data: dict[str, Any]
) -> dict[str, str]:
    """Validate given keba charging station host by setting it up."""
    keba = await setup_keba_connection(hass)
    try:
        device_info = await keba.get_device_info(data[CONF_HOST])
    except SetupError as exc:
        raise CannotConnect from exc

    # Return info that you want to store in the config entry.
    return {"title": device_info.model, "unique_id": device_info.device_id}


class KebaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for keba charging station."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    def __init__(self):
        """Initialize the Denon AVR flow."""
        self._discovered_devices = []

    async def async_step_import(self, import_data):
        """Import keba config from configuration.yaml."""

        return await self.async_step_connect(import_data)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:

            # check if IP address is set manually
            if host := user_input.get(CONF_HOST):
                return await self.async_step_connect(user_input)

            # discovery using keba library
            keba = await setup_keba_connection(self.hass)

            adapters = await network.async_get_adapters(self.hass)
            for adapter in adapters:
                for ip_info in adapter["ipv4"]:
                    local_ip = ip_info["address"]
                    network_prefix = ip_info["network_prefix"]
                    ip_net = ip_network(f"{local_ip}/{network_prefix}", False)
                    discovered_devices = await keba.discover_devices(str(ip_net.broadcast_address))
                    for d in discovered_devices:
                        if d not in self._discovered_devices:
                            self._discovered_devices.append(d)

            # More than one receiver could be discovered by that method
            if len(self._discovered_devices) == 1:
                user_input[CONF_HOST] = self._discovered_devices[0]
                return await self.async_step_connect(user_input)
            if len(self._discovered_devices) > 1:
                # show selection form
                return await self.async_step_select()

            errors["base"] = "no_device_found"

        return self.async_show_form(
            step_id="user", data_schema=STEP_HOST_SCHEMA, errors=errors
        )

    async def async_step_select(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle multiple charging stations found."""
        if user_input is not None:
            return await self.async_step_connect(user_input)

        select_scheme = vol.Schema(
            {
                vol.Required("host"): vol.In(
                    [d for d in self._discovered_devices]
                )
            }
        )

        return self.async_show_form(
            step_id="select", data_schema=select_scheme
        )

    async def async_step_connect(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Connect to the keba charging station."""
        errors: dict[str, str] = {}
        info = None
        try:
            info = await validate_input(self.hass, user_input)
        except CannotConnect:
            errors["base"] = "cannot_connect"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception")
            errors["base"] = "unknown"

        if info:
            await self.async_set_unique_id(info["unique_id"])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_HOST_SCHEMA, errors=errors
        )


    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> KebaOptionsFlow:
        """Return a Keba options flow."""
        return KebaOptionsFlow(config_entry)


class KebaOptionsFlow(config_entries.OptionsFlow):
    """Handle a option flow for keba charging station."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize keba charging station option flow."""
        self.config_entry = config_entry
        self.options = dict(config_entry.options)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        return await self.async_step_wallbox_options()

    async def async_step_wallbox_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="wallbox_options",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_FS,
                        default=self.config_entry.options.get(CONF_FS, False),
                    ): bool,
                    vol.Required(
                        CONF_FS_PERSIST,
                        default=self.config_entry.options.get(CONF_FS_PERSIST, False),
                    ): bool,
                    vol.Required(
                        CONF_FS_TIMEOUT,
                        default=self.config_entry.options.get(CONF_FS_TIMEOUT, 30),
                    ): vol.All(int, vol.Range(min=10, max=600)),
                    vol.Required(
                        CONF_FS_FALLBACK,
                        default=self.config_entry.options.get(CONF_FS_FALLBACK, 6),
                    ): vol.All(int, vol.Range(min=6, max=63)),
                    vol.Optional(
                        CONF_RFID,
                        default=self.config_entry.options.get(CONF_RFID, ""),
                    ): cv.string,
                    vol.Optional(
                        CONF_RFID_CLASS,
                        default=self.config_entry.options.get(CONF_RFID_CLASS, ""),
                    ): cv.string,
                }
            ),
        )


class CannotConnect(exceptions.HomeAssistantError):
    """Error to indicate we cannot connect."""
