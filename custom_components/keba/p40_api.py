"""REST API client for KEBA P40/P40 Pro charging stations."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Any

import aiohttp
import async_timeout

_LOGGER = logging.getLogger(__name__)

# API timeout in seconds. The wallbox's own CPM timeout is 60 s and the official app waits 120 s;
# aborting early just makes the single-worker REST server do the work twice.
API_TIMEOUT = 30


@dataclass
class P40DeviceInfo:
    """Device information for P40/P40 Pro charging station."""

    device_id: str
    manufacturer: str = "KEBA"
    model: str = ""
    sw_version: str = ""
    webconfigurl: str = ""

    def available_services(self) -> list:
        """Return available services (for compatibility with UDP-based devices)."""
        # P40 devices support basic start/stop services
        return []  # Services are registered differently for P40


@dataclass
class P40MeterInfo:
    """Meter information from P40 charging station."""

    meter_value: int  # mWh
    total_active_power: int  # mW
    total_power_factor: int  # cos phi * 10^3
    phases_supported: int
    current_offered: int  # mA
    temperature: int  # hundredths of degree Celsius
    lines: list[dict[str, Any]]


@dataclass
class P40WallboxState:
    """State information for P40 wallbox."""

    number: int
    serial_number: str
    state: str
    vehicle_plugged: bool
    authorization_enabled: bool
    session_active: bool
    meter: P40MeterInfo | None
    max_phases: int
    max_current: int  # mA
    phase_used: str
    ip_address: str
    model: str
    firmware_version: str | None
    error_code: str | None
    alias: str | None
    # Optional fields that may or may not be present in API response
    x2active: bool = False
    x11active: bool = False
    x12active: bool = False
    permanently_locked: bool = False


@dataclass
class P40SessionInfo:
    """Session information from P40 charging station."""

    id: int  # sessionSequenceId
    wallbox_number: int
    wallbox_serial_number: str
    wallbox_alias: str | None
    status: str
    starting_meter_value: int  # mWh
    start_date: int  # unix timestamp
    energy_consumed: int  # mWh
    energy_consumed_in_kwh: float | None  # kWh
    duration: int | None  # milliseconds
    token_id: str | None  # RFID token
    ending_meter_value: int | None  # mWh
    end_date: int | None  # unix timestamp
    termination_reason: str | None
    transaction_token: str | None


class P40ApiClient:
    """REST API client for KEBA P40/P40 Pro charging stations."""

    def __init__(self, host: str, session: aiohttp.ClientSession | None = None) -> None:
        """Initialize the P40 API client."""
        self._host = host
        self._session = session
        self._own_session = session is None
        self._base_url = f"https://{host}:8443"
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._callbacks: list = []
        # Store credentials for re-authentication
        self._username: str | None = None
        self._password: str | None = None
        self._refresh_lock = asyncio.Lock()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Ensure we have an aiohttp session."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        """Log out (revoke our tokens on the wallbox) and close the API client."""
        await self.logout()
        if self._own_session and self._session:
            await self._session.close()
            self._session = None

    async def login(self, username: str = "admin", password: str = "") -> bool:
        """Login to the P40 charging station."""
        _LOGGER.info("Attempting login to P40 at %s with username=%s", self._base_url, username)
        # Store credentials for token refresh/re-authentication
        self._username = username
        self._password = password

        session = await self._ensure_session()
        url = f"{self._base_url}/v2/jwt/login"

        try:
            async with async_timeout.timeout(API_TIMEOUT):
                _LOGGER.info("Sending login request to %s", url)
                async with session.post(
                    url,
                    json={"username": username, "password": password},
                    ssl=False,  # P40 uses self-signed certificates
                ) as response:
                    _LOGGER.info("Login response status: %s", response.status)
                    if response.status == 200:
                        data = await response.json()
                        self._access_token = data.get("accessToken")
                        self._refresh_token = data.get("refreshToken")
                        _LOGGER.info("Login successful, access_token present: %s", bool(self._access_token))
                        return True
                    else:
                        response_text = await response.text()
                        _LOGGER.error("Login failed with status %s, response: %s", response.status, response_text)
                        return False
        except (asyncio.TimeoutError, aiohttp.ClientError) as err:
            _LOGGER.error("Error during login: %s (type: %s)", err, type(err).__name__, exc_info=True)
            return False

    async def logout(self) -> None:
        """Revoke our access + refresh token on the wallbox, like the official app does."""
        if not self._refresh_token:
            return
        try:
            async with async_timeout.timeout(10):
                await self._request("POST", "/v2/jwt/logout", {"refreshToken": self._refresh_token})
        except asyncio.TimeoutError:
            _LOGGER.debug("Logout from P40 at %s timed out", self._host)
        self._access_token = self._refresh_token = None

    async def _relogin(self) -> bool:
        """Full login with the stored credentials (only when the refresh token is rejected)."""
        if self._username is None:
            return False
        return await self.login(self._username, self._password or "")

    async def _refresh_access_token(self, stale_token: str | None) -> bool:
        """Get a new access token from the refresh token.

        Mirrors the official app: one refresh at a time, keep the refresh token, and log in
        again only when the refresh token itself is rejected. Anything else (nginx rate limit
        503, timeouts, 5xx) is retried on the next poll instead of minting new tokens.
        """
        async with self._refresh_lock:
            if self._access_token != stale_token:
                return True  # another request refreshed while we waited
            if not self._refresh_token:
                return await self._relogin()
            session = await self._ensure_session()
            try:
                async with async_timeout.timeout(API_TIMEOUT):
                    async with session.post(
                        f"{self._base_url}/v2/jwt/refresh",
                        headers={"Authorization": f"Bearer {self._refresh_token}"},
                        ssl=False,
                    ) as response:
                        status = response.status
                        if status == 200:
                            self._access_token = (await response.json()).get("accessToken")
                            return bool(self._access_token)
                        text = await response.text()
            except (asyncio.TimeoutError, aiohttp.ClientError) as err:
                _LOGGER.warning("Token refresh failed (%s), retrying on next poll", err)
                return False
            if status in (401, 422):
                _LOGGER.info("Refresh token rejected (%s), logging in again", status)
                return await self._relogin()
            _LOGGER.warning("Token refresh returned %s, retrying on next poll: %s", status, text)
            return False

    async def _request(
        self, method: str, path: str, json_data: dict | None = None, params: dict | None = None,
        _retry_on_401: bool = True
    ) -> dict | None:
        """Make an authenticated request to the API.

        Args:
            method: HTTP method (GET, POST, etc.)
            path: API path
            json_data: Optional JSON body data
            params: Optional query parameters
            _retry_on_401: Internal flag to prevent infinite retry loops
        """
        _LOGGER.debug("_request called: method=%s, path=%s, has_json_data=%s, has_params=%s",
                     method, path, json_data is not None, params is not None)

        if not self._access_token:
            _LOGGER.error("Not authenticated. Please login first.")
            return None

        _LOGGER.debug("Access token present, making request to %s", path)
        session = await self._ensure_session()
        url = f"{self._base_url}{path}"
        token = self._access_token
        headers = {"Authorization": f"Bearer {token}"}

        try:
            async with async_timeout.timeout(API_TIMEOUT):
                _LOGGER.debug("Sending %s request to %s with params=%s", method, url, params)
                async with session.request(
                    method, url, json=json_data, headers=headers, ssl=False, params=params
                ) as response:
                    _LOGGER.debug("Response from %s: status=%s", path, response.status)
                    if response.status in (200, 201):
                        response_data = await response.json()
                        _LOGGER.debug("Response data from %s: %s", path, response_data)
                        return response_data
                    elif response.status == 401 and _retry_on_401:
                        # Token expired, try to refresh
                        # Normal: the access token lives 15 minutes.
                        response_text = await response.text()
                        _LOGGER.debug("Request to %s got 401: %s. Refreshing token", path, response_text)
                        if await self._refresh_access_token(token):
                            _LOGGER.info("Token refreshed successfully, retrying request to %s", path)
                            # Retry the request with the new token (but don't retry again on 401)
                            return await self._request(method, path, json_data, params, _retry_on_401=False)
                        else:
                            _LOGGER.error("Token refresh failed, cannot complete request to %s", path)
                            return None
                    else:
                        response_text = await response.text()
                        _LOGGER.error(
                            "Request to %s failed with status %s, response: %s",
                            path, response.status, response_text
                        )
                        return None
        except (asyncio.TimeoutError, aiohttp.ClientError) as err:
            _LOGGER.error("Error during request to %s: %s (type: %s)",
                         path, err, type(err).__name__, exc_info=True)
            return None

    async def get_device_info(self) -> P40DeviceInfo | None:
        """Get device information."""
        # Get serial number - Note: /serialnumber returns plain text, not JSON
        # In firmware 2.4.0 (PKG-1.4.0) this endpoint may return HTTP 500;
        # in that case we fall back to the wallbox serial number from /v2/wallboxes.
        session = await self._ensure_session()
        url = f"{self._base_url}/serialnumber"

        serial_number: str | None = None
        try:
            async with async_timeout.timeout(API_TIMEOUT):
                async with session.get(url, ssl=False) as response:
                    if response.status == 200:
                        serial_number = (await response.text()).strip()
                    else:
                        _LOGGER.warning(
                            "Request to /serialnumber failed with status %s, "
                            "falling back to wallbox serial number",
                            response.status,
                        )
        except (asyncio.TimeoutError, aiohttp.ClientError) as err:
            _LOGGER.warning(
                "Error during request to /serialnumber: %s, "
                "falling back to wallbox serial number",
                err,
            )

        # Get version info - Note: /version returns a JSON string (e.g., "2.3.0-SNAPSHOT")
        version = ""
        try:
            async with async_timeout.timeout(API_TIMEOUT):
                async with session.get(f"{self._base_url}/version", ssl=False) as response:
                    if response.status == 200:
                        version = await response.json()  # Returns a JSON string
                    else:
                        _LOGGER.warning(
                            "Request to /version failed with status %s", response.status
                        )
        except (asyncio.TimeoutError, aiohttp.ClientError) as err:
            _LOGGER.warning("Error during request to /version: %s", err)

        # Get wallbox info to get model and (optionally) the serial number fallback
        wallboxes_response = await self._request("GET", "/v2/wallboxes")
        wallboxes: list = []
        if isinstance(wallboxes_response, dict):
            wallboxes = wallboxes_response.get("wallboxes", []) or []
        elif isinstance(wallboxes_response, list):
            wallboxes = wallboxes_response

        model = ""
        if wallboxes:
            model = wallboxes[0].get("model", "")
            if not serial_number:
                serial_number = wallboxes[0].get("serialNumber") or None

        if not serial_number:
            _LOGGER.error(
                "Unable to determine device serial number from /serialnumber or /v2/wallboxes"
            )
            return None

        return P40DeviceInfo(
            device_id=serial_number,
            model=model or "KEBA P40",
            sw_version=version,
            webconfigurl=f"https://{self._host}:8443",
        )

    async def get_wallbox(self, serial_number: str | None = None) -> dict | None:
        """Get wallbox information."""
        _LOGGER.debug("get_wallbox called with serial_number=%s", serial_number)
        if serial_number:
            _LOGGER.debug("Getting specific wallbox: %s", serial_number)
            result = await self._request("GET", f"/v2/wallboxes/{serial_number}")
            _LOGGER.debug("Specific wallbox response: %s", result)
            return result
        else:
            # Get first wallbox
            _LOGGER.debug("Getting all wallboxes from /v2/wallboxes")
            response = await self._request("GET", "/v2/wallboxes")
            _LOGGER.debug("Wallboxes response: %s (type: %s)", response, type(response).__name__)

            # Response is a dict with 'wallboxes' key containing the list
            if response and isinstance(response, dict):
                wallboxes = response.get("wallboxes", [])
                if wallboxes and len(wallboxes) > 0:
                    _LOGGER.debug("Returning first wallbox: %s", wallboxes[0])
                    return wallboxes[0]
            # Fallback: check if response is directly a list (for compatibility)
            elif response and isinstance(response, list) and len(response) > 0:
                _LOGGER.debug("Returning first wallbox from list: %s", response[0])
                return response[0]

            _LOGGER.warning("No wallboxes found in response, response=%s", response)
            return None

    async def get_wallbox_state(self, serial_number: str) -> P40WallboxState | None:
        """Get current wallbox state."""
        data = await self.get_wallbox(serial_number)
        if not data:
            return None

        # Parse meter info
        meter_data = data.get("meter")
        meter = None
        if meter_data:
            meter = P40MeterInfo(
                meter_value=meter_data.get("meterValue", 0),
                total_active_power=meter_data.get("totalActivePower", 0),
                total_power_factor=meter_data.get("totalPowerFactor", 0),
                phases_supported=meter_data.get("phasesSupported", 0),
                current_offered=meter_data.get("currentOffered", 0),
                temperature=meter_data.get("temperature", 0),
                lines=meter_data.get("lines", []),
            )

        return P40WallboxState(
            number=data.get("number", 1),
            serial_number=data.get("serialNumber", serial_number),
            state=data.get("state", "UNKNOWN"),
            vehicle_plugged=data.get("vehiclePlugged", False),
            authorization_enabled=data.get("authorizationEnabled", False),
            session_active=data.get("sessionActive", False),
            meter=meter,
            max_phases=data.get("maxPhases", 3),
            max_current=data.get("maxCurrent", 0),
            phase_used=data.get("phaseUsed", ""),
            ip_address=data.get("ipAddress", ""),
            model=data.get("model", ""),
            firmware_version=data.get("firmwareVersion"),
            error_code=data.get("errorCode"),
            alias=data.get("alias"),
            # Optional fields
            x2active=data.get("x2active", False),
            x11active=data.get("x11active", False),
            x12active=data.get("x12active", False),
            permanently_locked=data.get("permanentlyLocked", False),
        )

    async def start_charging(self, serial_number: str, token_id: str | None = None) -> bool:
        """Start charging."""
        json_data = {}
        if token_id:
            json_data["tokenId"] = token_id

        result = await self._request(
            "POST", f"/v2/wallboxes/{serial_number}/start-charging", json_data
        )
        return result is not None

    async def stop_charging(self, serial_number: str) -> bool:
        """Stop charging."""
        result = await self._request("POST", f"/v2/wallboxes/{serial_number}/stop-charging")
        return result is not None

    async def get_load_management_config(self, key: str | None = None) -> dict | None:
        """Get load management configuration.

        Args:
            key: Specific config key to get, or None to get all configs.
                 Available keys: max_available_current, min_default_current,
                 nominal_voltage, nominal_frequency, failsafe_current_serial_1, etc.

        Returns:
            Configuration value(s) or None on error.
        """
        if key:
            return await self._request("GET", f"/v2/configs/lmgmt/{key}")
        return await self._request("GET", "/v2/configs/lmgmt/")

    async def set_load_management_config(self, configs: list[dict]) -> bool:
        """Set load management configuration.

        Args:
            configs: List of config dicts with 'key' and 'value'.
                     Example: [{"key": "max_available_current", "value": 8000}]
                     Available keys: max_available_current, min_default_current,
                     failsafe_current_serial_1, etc. Values in mA for currents.

        Returns:
            True if successful, False otherwise.
        """
        json_data = {"configs": configs}
        result = await self._request("PUT", "/v2/configs/lmgmt/", json_data)
        return result is not None

    def _parse_session(self, data: dict) -> P40SessionInfo:
        """Parse session data from API response."""
        return P40SessionInfo(
            id=data.get("id", 0),
            wallbox_number=data.get("wallboxNumber", 0),
            wallbox_serial_number=data.get("wallboxSerialNumber", ""),
            wallbox_alias=data.get("wallboxAlias"),
            status=data.get("status", ""),
            starting_meter_value=data.get("startingMeterValue", 0),
            start_date=data.get("startDate", 0),
            energy_consumed=data.get("energyConsumed", 0),
            energy_consumed_in_kwh=data.get("energyConsumedInKwh"),
            duration=data.get("duration"),
            token_id=data.get("tokenId"),
            ending_meter_value=data.get("endingMeterValue"),
            end_date=data.get("endDate"),
            termination_reason=data.get("terminationReason"),
            transaction_token=data.get("transactionToken"),
        )

    async def get_sessions(
        self,
        serial_number: str | None = None,
        limit: int = 10,
        order_field: str = "SESSION_START_DATE",
        order_dir: str = "DESC",
    ) -> list[P40SessionInfo]:
        """Get charging sessions.

        Args:
            serial_number: Filter by wallbox serial number
            limit: Maximum number of sessions to return
            order_field: Field to order by (default: SESSION_START_DATE)
            order_dir: Order direction ASC or DESC (default: DESC)

        Returns:
            List of P40SessionInfo objects
        """
        params = {
            "limit": str(limit),
            "orderField": order_field,
            "orderDir": order_dir,
        }
        if serial_number:
            params["filters"] = f"SOCKET_SERIAL_NUMBER={serial_number}"

        data = await self._request("GET", "/v2/sessions", params=params)
        if not data:
            return []

        # API returns {'sessions': [...]} not a direct list
        sessions_list = data.get("sessions", []) if isinstance(data, dict) else data
        if not isinstance(sessions_list, list):
            return []

        return [self._parse_session(session) for session in sessions_list]

    def add_callback(self, callback) -> None:
        """Add a callback for state updates."""
        self._callbacks.append(callback)

    def remove_callback(self, callback) -> None:
        """Remove a callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    async def start_polling(self, serial_number: str, interval: int = 5) -> None:
        """Start polling for updates (for compatibility with UDP-based implementation)."""
        # This would be implemented to periodically poll the API
        # For now, this is a placeholder
        pass

    async def unlock_socket(self, serial_number: str) -> bool:
        """Unlock the charging socket (only for socket/plug versions, not cable versions).

        Args:
            serial_number: The serial number of the wallbox

        Returns:
            True if successful, False otherwise
        """
        result = await self._request("POST", f"/v2/wallboxes/{serial_number}/unlock")
        return result is not None
