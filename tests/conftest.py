"""Fixtures for Keba integration tests."""

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.keba.const import DOMAIN

from homeassistant.const import CONF_HOST
from pytest_homeassistant_custom_component.common import MockConfigEntry

import pytest

P30_TITLE = "P30"
P30_ID = "12345678"
P30_IP = "192.168.1.11"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):  # pylint: disable=unused-argument
    """Automatically enables custom integrations for tests."""
    yield


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return the default mocked config entry."""
    return MockConfigEntry(
        title=P30_TITLE,
        domain=DOMAIN,
        data={
            CONF_HOST: P30_IP,
        },
        unique_id=P30_ID,
        entry_id="test_entry_id",
    )


@pytest.fixture
def mock_keba() -> Generator[AsyncMock]:
    """Return a mock Keba instance."""
    with (
        patch(
            "custom_components.keba.config_flow.get_keba_connection", autospec=True
        ) as mocked_keba,
        patch("custom_components.keba.get_keba_connection", new=mocked_keba),
    ):
        keba = mocked_keba.return_value

        device_info = MagicMock()
        device_info.model = "P30"
        device_info.device_id = P30_ID
        keba.get_device_info.return_value = device_info

        yield keba
