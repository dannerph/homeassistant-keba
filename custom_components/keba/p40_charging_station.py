"""P40/P40 Pro charging station wrapper for Home Assistant integration."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from .p40_api import P40ApiClient, P40DeviceInfo, P40SessionInfo

_LOGGER = logging.getLogger(__name__)

# The wallbox logs every REST request in full (restapi + CPM) on a small log partition; when that
# partition fills up the data endpoints start returning 500 until a reboot. So: poll as slowly as
# the official app (15 s), fetch sessions/lmgmt only when they can have changed, back off on errors.
POLL_INTERVAL = 15  # seconds
MAX_BACKOFF = 300  # seconds
SLOW_REFRESH = 600  # seconds, safety refresh of sessions + load management config
ACTIVE_SESSION_STATES = ("INITIATED", "PWM_CHARGING", "BLOCKED")


def next_poll_delay(delay: float, ok: bool) -> float:
    """Base interval after a successful poll, exponential backoff after a failed one."""
    return POLL_INTERVAL if ok else min(delay * 2, MAX_BACKOFF)


def split_sessions(
    sessions: list[P40SessionInfo],
) -> tuple[P40SessionInfo | None, P40SessionInfo | None]:
    """Return (active session, most recent closed session) from a newest-first session list."""
    active = next((s for s in sessions if s.status in ACTIVE_SESSION_STATES), None)
    closed = next((s for s in sessions if s.end_date is not None and s.status == "CLOSED"), None)
    return active, closed


class P40ChargingStation:
    """Wrapper for P40/P40 Pro charging station to match the interface of UDP-based stations."""

    def __init__(self, api_client: P40ApiClient, serial_number: str) -> None:
        """Initialize the P40 charging station."""
        self._api = api_client
        self._serial_number = serial_number
        self._device_info: P40DeviceInfo | None = None
        self._state_cache: dict[str, Any] = {}
        self._callbacks: list = []
        self._polling_task: asyncio.Task | None = None
        self._update_lock = asyncio.Lock()
        self._current_session: P40SessionInfo | None = None
        self._last_completed_session: P40SessionInfo | None = None
        self._max_available_current: int | None = None  # in mA, from load management config
        self._last_state_key: tuple | None = None  # state when sessions were last fetched
        self._next_slow_refresh = 0.0  # monotonic time of the next sessions + lmgmt refresh

    @property
    def device_info(self) -> P40DeviceInfo:
        """Return device information."""
        return self._device_info

    async def initialize(self, device_info: P40DeviceInfo | None = None) -> None:
        """Initialize the charging station."""
        self._device_info = device_info or await self._api.get_device_info()
        await self._update_state(force=True)
        # Start polling for updates
        self._polling_task = asyncio.create_task(self._poll_updates())

    async def _update_state(self, force: bool = False) -> bool:
        """Update the state cache from the API. Returns False if the wallbox didn't answer.

        force: also re-read sessions and load management config (after our own actions).
        """
        async with self._update_lock:
            return await self._update_state_locked(force)

    async def _update_state_locked(self, force: bool) -> bool:
        state = await self._api.get_wallbox_state(self._serial_number)
        if not state:
            return False

        now = time.monotonic()
        slow_due = force or now >= self._next_slow_refresh
        if slow_due:
            self._next_slow_refresh = now + SLOW_REFRESH
            # Store max_available_current in mA, will be converted to A when used
            lmgmt_config = await self._api.get_load_management_config("max_available_current")
            if lmgmt_config and "data" in lmgmt_config:
                self._max_available_current = lmgmt_config["data"]

        state_key = (state.state, state.session_active, state.vehicle_plugged)
        if slow_due or state_key != self._last_state_key:
            sessions = await self._api.get_sessions(self._serial_number, limit=5)
            self._current_session, last_closed = split_sessions(sessions)
            if last_closed:
                self._last_completed_session = last_closed
            # A just-started session may not be in the session list yet: retry on the next poll
            if self._current_session or not state.session_active:
                self._last_state_key = state_key
            _LOGGER.debug(
                "Sessions for %s: current=%s, last completed=%s",
                self._serial_number, self._current_session, self._last_completed_session,
            )

        # Map P40 state to the format expected by entities
        # This mapping ensures compatibility with existing entity code
        
        # Basic state
        self._state_cache["State"] = self._map_state(state.state)
        self._state_cache["State_details"] = self._map_state_details(state.state)
        self._state_cache["Plug"] = 7 if state.vehicle_plugged else 0
        
        # Meter values
        if state.meter:
            # meterValue is total energy (like odometer), convert mWh to kWh
            self._state_cache["E total"] = state.meter.meter_value / 1000000
            # Convert mW to kW (sensor expects kW)
            self._state_cache["P"] = state.meter.total_active_power / 1000000
            # Convert mA to A - currentOffered is what's being offered to the vehicle right now
            self._state_cache["Curr offered"] = state.meter.current_offered / 1000
            # For Curr user (used by Number entity), use max_available_current from load management
            # This is the configured max current, not the current being offered
            if self._max_available_current is not None:
                self._state_cache["Curr user"] = self._max_available_current / 1000
            else:
                # Fallback to currentOffered if load management config not available
                self._state_cache["Curr user"] = state.meter.current_offered / 1000
            self._state_cache["Curr HW"] = state.max_current / 1000
            # Power factor (cos phi * 1000, convert to decimal)
            self._state_cache["PF"] = state.meter.total_power_factor / 1000

            # P40-specific: Temperature in hundredths of degree to degrees
            self._state_cache["Temperature"] = state.meter.temperature / 100

            # P40-specific: Phases supported
            self._state_cache["Phases Supported"] = state.meter.phases_supported

            # Phase currents and voltages
            for i, line in enumerate(state.meter.lines[:3], 1):
                phase = line.get("socketPhase", f"L{i}")
                # Convert mA to A
                self._state_cache[f"I{i}"] = line.get("current", 0) / 1000
                self._state_cache[f"U{i}"] = line.get("voltage", 0)
        
        # Max current and percentage
        self._state_cache["Max curr"] = state.max_current / 1000  # Convert mA to A
        if state.max_current > 0:
            curr_user = self._state_cache.get("Curr user", 0)
            self._state_cache["Max curr %"] = (curr_user / (state.max_current / 1000)) * 100

        # Digital outputs and inputs (optional fields in P40 API)
        self._state_cache["Output"] = 1 if state.x11active else 0
        self._state_cache["Input"] = 1 if state.x12active else 0

        # X2 phase switch
        self._state_cache["X2 phaseSwitch"] = 1 if state.x2active else 0

        # Plug locked status
        self._state_cache["Plug_locked"] = state.permanently_locked

        # Binary sensor states
        self._state_cache["State_on"] = state.state == "CHARGING"
        self._state_cache["Plug_EV"] = state.vehicle_plugged
        self._state_cache["Plug_charging_station"] = state.vehicle_plugged
        self._state_cache["AuthON"] = state.authorization_enabled

        # P40-specific: Session active binary sensor
        self._state_cache["Session Active"] = state.session_active

        # Session info
        self._state_cache["Session ID"] = self._current_session.id if self._current_session else None
        self._state_cache["RFID tag"] = self._current_session.token_id if self._current_session else None
        self._state_cache["RFID class"] = None  # Not available in P40 API
        self._state_cache["E start"] = self._current_session.starting_meter_value / 1000000 if self._current_session else None  # Convert mWh to kWh
        # Convert timestamps from milliseconds to datetime objects (truncate to seconds)
        if self._current_session and self._current_session.start_date:
            self._state_cache["started"] = datetime.fromtimestamp(
                self._current_session.start_date // 1000, tz=timezone.utc
            ).isoformat()
        else:
            self._state_cache["started"] = None
        if self._last_completed_session and self._last_completed_session.end_date:
            self._state_cache["ended"] = datetime.fromtimestamp(
                self._last_completed_session.end_date // 1000, tz=timezone.utc
            ).isoformat()
        else:
            self._state_cache["ended"] = None
        self._state_cache["reason"] = self._last_completed_session.termination_reason if self._last_completed_session else None

        # Session energy in kWh. energyConsumed == live meter - session start meter (verified against
        # the wallbox's own logs), so it stays current without polling the sessions endpoint.
        if self._current_session and state.meter:
            self._state_cache["E pres"] = (
                state.meter.meter_value - self._current_session.starting_meter_value
            ) / 1000000
        elif self._current_session:
            self._state_cache["E pres"] = self._current_session.energy_consumed / 1000000
        else:
            self._state_cache["E pres"] = 0

        _LOGGER.debug(
            "Session state cache: Session ID=%s, started=%s, ended=%s, reason=%s, E pres=%s",
            self._state_cache.get("Session ID"),
            self._state_cache.get("started"),
            self._state_cache.get("ended"),
            self._state_cache.get("reason"),
            self._state_cache.get("E pres"),
        )

        # Authorization - for P40, use session_active to determine if charging is authorized
        # Authreq=1 means "authorization required" (locked), Authreq=0 means "authorized" (unlocked)
        # So we invert session_active: if session is active, we're authorized (Authreq=0)
        self._state_cache["Authreq"] = 0 if state.session_active else 1
        # Also store the actual authorization_enabled setting
        self._state_cache["AuthON"] = state.authorization_enabled

        # P40-specific sensors
        # Raw state from API
        self._state_cache["Raw State"] = state.state
        # Phase used
        self._state_cache["Phase Used"] = state.phase_used
        # Model
        self._state_cache["Model"] = state.model
        # Error code (always set, even if None)
        self._state_cache["Error Code"] = state.error_code
        # Firmware version (always set, even if None)
        self._state_cache["Firmware Version"] = state.firmware_version

        # Notify callbacks
        for callback in self._callbacks:
            try:
                callback()
            except Exception as err:
                _LOGGER.error("Error in callback: %s", err)
        return True

    def _map_state(self, p40_state: str) -> int:
        """Map P40 state to legacy state codes."""
        state_mapping = {
            "IDLE": 0,
            "READY_FOR_CHARGING": 1,
            "CHARGING": 3,
            "SUSPENDED": 4,
            "RECOVER_FROM_ERROR": 5,
            "UNAVAILABLE": 254,
            "OFFLINE": 254,
        }
        return state_mapping.get(p40_state, 0)

    def _map_state_details(self, p40_state: str) -> str:
        """Map P40 state to human-readable string."""
        state_details = {
            "IDLE": "ready",
            "READY_FOR_CHARGING": "ready",
            "CHARGING": "charging",
            "SUSPENDED": "suspended",
            "RECOVER_FROM_ERROR": "error",
            "UNAVAILABLE": "unavailable",
            "OFFLINE": "offline",
            "INSTALLER_MODE": "installer mode",
            "TOKEN_PROGRAMMING_MODE": "token programming",
            "UNRECOVERABLE_ERROR": "error",
            "DEGRADED": "degraded",
        }
        return state_details.get(p40_state, "unknown")

    async def _poll_updates(self) -> None:
        """Poll for state updates, backing off while the wallbox fails."""
        delay: float = POLL_INTERVAL
        while True:
            try:
                await asyncio.sleep(delay)
                ok = await self._update_state()
            except asyncio.CancelledError:
                break
            except Exception as err:  # keep polling whatever happens
                _LOGGER.error("Error polling updates: %s", err)
                ok = False
            if ok and delay != POLL_INTERVAL:
                _LOGGER.info("P40 %s answers again", self._serial_number)
            elif not ok and delay == POLL_INTERVAL:
                _LOGGER.warning(
                    "P40 %s not answering, backing off up to %s s", self._serial_number, MAX_BACKOFF
                )
            delay = next_poll_delay(delay, ok)

    def get_value(self, key: str) -> Any:
        """Get a cached value."""
        return self._state_cache.get(key)

    def add_callback(self, callback) -> None:
        """Add a callback for state updates."""
        self._callbacks.append(callback)

    def remove_callback(self, callback) -> None:
        """Remove a callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    async def start(self, rfid: str | None = None, rfid_class: str | None = None) -> None:
        """Start charging."""
        # P40 uses token_id instead of RFID
        token_id = rfid if rfid else None
        await self._api.start_charging(self._serial_number, token_id)
        # Update state immediately
        await self._update_state(force=True)

    async def stop(self, rfid: str | None = None, rfid_class: str | None = None) -> None:
        """Stop charging."""
        await self._api.stop_charging(self._serial_number)
        # Update state immediately
        await self._update_state(force=True)

    async def enable(self) -> None:
        """Enable charging (same as start without RFID)."""
        await self._api.start_charging(self._serial_number)
        # Update state immediately
        await self._update_state(force=True)

    async def disable(self) -> None:
        """Disable charging (same as stop)."""
        await self._api.stop_charging(self._serial_number)
        # Update state immediately
        await self._update_state(force=True)

    async def unlock_socket(self) -> None:
        """Unlock the charging socket."""
        await self._api.unlock_socket(self._serial_number)
        # Update state immediately
        await self._update_state(force=True)

    async def request_data(self) -> None:
        """Request updated data from the charging station."""
        await self._update_state(force=True)

    async def display(self, message: str) -> None:
        """Display a message on the charging station (not supported on P40)."""
        raise NotImplementedError("P40/P40 Pro does not support display text functionality via API")

    async def set_current_max_permanent(self, current: float) -> None:
        """Set maximum available current permanently."""
        # Convert A to mA
        current_ma = int(current * 1000)
        configs = [{"key": "max_available_current", "value": current_ma}]
        await self._api.set_load_management_config(configs)
        await self._update_state(force=True)

    async def set_current(self, current: float, delay: int | None = None) -> None:
        """Set charging current limit."""
        # Convert A to mA
        current_ma = int(current * 1000)
        configs = [{"key": "max_available_current", "value": current_ma}]
        await self._api.set_load_management_config(configs)
        await self._update_state(force=True)

    async def set_failsafe(self, timeout: int, fallback_value: float) -> None:
        """Configure failsafe mode."""
        # Convert A to mA
        fallback_ma = int(fallback_value * 1000)
        configs = [{"key": "failsafe_current_serial_1", "value": fallback_ma}]
        await self._api.set_load_management_config(configs)
        await self._update_state(force=True)

    async def set_energy(self, energy: float) -> None:
        """Set energy limit - NOT SUPPORTED on P40."""
        raise NotImplementedError("P40/P40 Pro does not support setting energy limits via API")

    async def set_charging_power(self, power: float) -> None:
        """Set charging power - NOT SUPPORTED on P40."""
        raise NotImplementedError("P40/P40 Pro does not support setting power limits via API (only current limits)")

    async def set_output(self, value: int) -> None:
        """Set output value on X2."""
        configs = [{"key": "inst_x2_mode", "value": value}]
        await self._api.set_installer_io_config(configs)
        await self._update_state(force=True)

    async def x2src(self, source: int) -> None:
        """Set X2 source."""
        configs = [{"key": "connector_phase_source", "value": source}]
        await self._api.set_load_management_config(configs)
        await self._update_state(force=True)

    async def x2(self, three_phases: bool) -> None:
        """X2 Phase switch."""
        configs = [{"key": "connector_phase_enable", "value": three_phases}]
        await self._api.set_load_management_config(configs)
        await self._update_state(force=True)

    async def close(self) -> None:
        """Close the charging station connection."""
        if self._polling_task:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass

