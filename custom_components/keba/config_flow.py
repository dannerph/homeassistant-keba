"""Config flow for Abfallplus integration."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
import logging

from homeassistant import config_entries, core, exceptions
from homeassistant.const import CONF_HOST
from homeassistant.data_entry_flow import FlowResult
from homeassistant.core import callback

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
        vol.Required(CONF_HOST): str,
    }
)


async def validate_input(
    hass: core.HomeAssistant, data: dict[str, Any]
) -> dict[str, str]:
    """Validate given keba charging station host by setting it up."""
    keba = await setup_keba_connection(hass)
    try:
        device_info = await keba.get_device_info(data["host"])
    except SetupError as exc:
        raise CannotConnect from exc

    # Return info that you want to store in the config entry.
    return {"title": device_info.model}


class KebaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Abfallplus."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            info = None
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

            if info:
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_HOST_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> KebaOptionsFlow:
        """Return a BWM ConnectedDrive option flow."""
        return KebaOptionsFlow(config_entry)


class KebaOptionsFlow(config_entries.OptionsFlow):
    """Handle a option flow for BMW ConnectedDrive."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize BMW ConnectedDrive option flow."""
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
