"""Config flow for keba integration."""

from __future__ import annotations

import asyncio
from ipaddress import ip_network
import logging
from typing import Any

import aiohttp
from keba_kecontact.connection import SetupError
import voluptuous as vol

from homeassistant import core, exceptions
from homeassistant.components import network
from homeassistant.config_entries import (
    CONN_CLASS_LOCAL_POLL,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv

from . import get_keba_connection
from .const import CONF_RFID, CONF_RFID_CLASS, DOMAIN
from .p40_api import P40ApiClient

_LOGGER = logging.getLogger(__name__)

CONF_DEVICE_TYPE = "device_type"
DEVICE_TYPE_UDP = "udp"
DEVICE_TYPE_P40 = "p40"

STEP_HOST_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_HOST): str,
        vol.Optional(CONF_PASSWORD): str,
    }
)


async def detect_device_type(host: str) -> str:
    """Detect if the device is P40 (REST API) or older (UDP)."""
    _LOGGER.debug("Attempting to detect device type for host: %s", host)

    # Try to connect to P40 REST API endpoint
    # Note: /serialnumber returns plain text, not JSON
    try:
        async with aiohttp.ClientSession() as session:
            async with asyncio.timeout(3):
                _LOGGER.debug("Trying P40 REST API endpoint: https://%s:8443/serialnumber", host)
                async with session.get(
                    f"https://{host}:8443/serialnumber",
                    ssl=False,
                ) as response:
                    _LOGGER.debug("P40 API response status: %s", response.status)
                    if response.status == 200:
                        # Successfully connected to P40 API
                        serial = await response.text()
                        _LOGGER.info("Detected P40 device at %s (serial: %s)", host, serial.strip())
                        return DEVICE_TYPE_P40
                    elif response.status == 401:
                        # Auth required, but API exists
                        _LOGGER.info("Detected P40 device at %s (auth required)", host)
                        return DEVICE_TYPE_P40
                    else:
                        _LOGGER.debug("Unexpected status %s, assuming UDP device", response.status)
    except asyncio.TimeoutError as err:
        _LOGGER.debug("P40 detection timeout for %s: %s", host, err)
    except aiohttp.ClientError as err:
        _LOGGER.debug("P40 detection client error for %s: %s", host, err)
    except Exception as err:
        _LOGGER.debug("P40 detection failed for %s: %s (type: %s)", host, err, type(err).__name__)

    # Default to UDP-based device
    _LOGGER.info("Defaulting to UDP device type for %s", host)
    return DEVICE_TYPE_UDP


async def validate_input(
    hass: core.HomeAssistant, data: dict[str, Any]
) -> dict[str, str]:
    """Validate given keba charging station host by setting it up."""
    host = data[CONF_HOST]
    _LOGGER.debug("Validating input for host: %s", host)

    # Detect device type
    device_type = await detect_device_type(host)
    _LOGGER.debug("Device type detected: %s", device_type)

    if device_type == DEVICE_TYPE_P40:
        # P40/P40 Pro device - use REST API
        # For P40, we need a password. If not provided, try empty string
        password = data.get(CONF_PASSWORD, "")
        _LOGGER.debug("P40 device - attempting login with password: %s", "***" if password else "(empty)")

        api_client = P40ApiClient(host)
        try:
            # Try to login
            _LOGGER.debug("Calling api_client.login()...")
            login_result = await api_client.login(password=password)
            _LOGGER.debug("Login result: %s", login_result)

            if not login_result:
                _LOGGER.error("Login failed for P40 device at %s", host)
                raise CannotConnect("Failed to authenticate with P40 device")

            _LOGGER.debug("Login successful, getting device info...")
            device_info = await api_client.get_device_info()
            _LOGGER.debug("Device info retrieved: %s", device_info)

            if not device_info:
                _LOGGER.error("Failed to get device info from P40 at %s", host)
                raise CannotConnect("Failed to get device info from P40")

            await api_client.close()
            _LOGGER.info("P40 validation successful for %s (model: %s, id: %s)",
                        host, device_info.model, device_info.device_id)

            return {
                "title": device_info.model,
                "unique_id": device_info.device_id,
                "device_type": DEVICE_TYPE_P40,
            }
        except Exception as exc:
            _LOGGER.error("Exception during P40 validation for %s: %s (type: %s)",
                         host, str(exc), type(exc).__name__)
            await api_client.close()
            raise CannotConnect from exc
    else:
        # UDP-based device - use existing keba_kecontact library
        keba = await get_keba_connection(hass)
        try:
            device_info = await keba.get_device_info(host)
        except SetupError as exc:
            raise CannotConnect from exc

        # Return info that you want to store in the config entry.
        return {
            "title": device_info.model,
            "unique_id": device_info.device_id,
            "device_type": DEVICE_TYPE_UDP,
        }


class KebaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for keba charging station."""

    VERSION = 1
    CONNECTION_CLASS = CONN_CLASS_LOCAL_POLL

    def __init__(self) -> None:
        """Initialize the Keba flow."""
        self._discovered_devices: list[str] = []

    async def async_step_import(self, import_data) -> ConfigFlowResult:
        """Import keba config from configuration.yaml."""
        return await self.async_step_connect(import_data)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            # check if IP address is set manually
            if user_input.get(CONF_HOST):
                return await self.async_step_connect(user_input)

            # discovery using keba library
            keba = await get_keba_connection(self.hass)

            adapters = await network.async_get_adapters(self.hass)
            for adapter in adapters:
                for ip_info in adapter["ipv4"]:
                    local_ip = ip_info["address"]
                    network_prefix = ip_info["network_prefix"]
                    ip_net = ip_network(f"{local_ip}/{network_prefix}", False)
                    discovered_devices = await keba.discover_devices(
                        str(ip_net.broadcast_address)
                    )
                    for device in discovered_devices:
                        if device not in self._discovered_devices:
                            self._discovered_devices.append(device)

            # More than one charging station could be discovered by that method
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
    ) -> ConfigFlowResult:
        """Handle multiple charging stations found."""
        if user_input is not None:
            return await self.async_step_connect(user_input)

        select_scheme = vol.Schema(
            {vol.Required("host"): vol.In(list(self._discovered_devices))}
        )

        return self.async_show_form(step_id="select", data_schema=select_scheme)

    async def async_step_connect(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Connect to the keba charging station."""
        errors: dict[str, str] = {}
        info = None

        if user_input:
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
                # Store device type in config entry data
                entry_data = user_input.copy()
                entry_data[CONF_DEVICE_TYPE] = info.get("device_type", DEVICE_TYPE_UDP)
                return self.async_create_entry(title=info["title"], data=entry_data)

        return self.async_show_form(
            step_id="user", data_schema=STEP_HOST_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> KebaOptionsFlow:
        """Return a Keba options flow."""
        return KebaOptionsFlow()


class KebaOptionsFlow(OptionsFlow):
    """Handle a option flow for keba charging station."""

    # No __init__ needed - framework provides self.config_entry automatically

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
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
