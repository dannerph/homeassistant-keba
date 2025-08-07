"""Test the Keba config flow."""

from unittest.mock import AsyncMock

from custom_components.keba.config_flow import CannotConnect
from custom_components.keba.const import DOMAIN
import pytest

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import P30_IP, P30_TITLE


@pytest.mark.usefixtures("mock_keba", "mock_config_entry")
async def test_config_flow(
    hass: HomeAssistant,
) -> None:
    """Test the Keba config flow."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}

    # Simulate user input
    user_input = {CONF_HOST: P30_IP}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == P30_TITLE
    assert result["data"][CONF_HOST] == P30_IP


async def test_config_flow_errors(hass: HomeAssistant, mock_keba: AsyncMock) -> None:
    """Test error handling in the Keba config flow."""
    # Simulate a connection error
    mock_keba.get_device_info.side_effect = CannotConnect("Connection error")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    user_input = {CONF_HOST: P30_IP}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    mock_keba.get_device_info.side_effect = None

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
