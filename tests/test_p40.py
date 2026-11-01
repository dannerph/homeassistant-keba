"""Tests for the P40 polling/auth behaviour that keeps load (and log volume) on the wallbox low.

Runs without Home Assistant: the two P40 modules are loaded as a stand-alone package so the
integration's __init__.py (which imports homeassistant) is not executed.
Needs: pytest, pytest-asyncio, aiohttp, async_timeout, aioresponses.
"""

import asyncio
import importlib
import pathlib
import sys
import types

import pytest
from aioresponses import aioresponses
from yarl import URL

_pkg = types.ModuleType("keba_p40")
_pkg.__path__ = [str(pathlib.Path(__file__).parents[1] / "custom_components" / "keba")]
sys.modules["keba_p40"] = _pkg
api = importlib.import_module("keba_p40.p40_api")
station = importlib.import_module("keba_p40.p40_charging_station")

BASE = "https://wb:8443"


def _session(status, end_date=None, sid=1):
    return api.P40SessionInfo(
        id=sid, wallbox_number=1, wallbox_serial_number="1", wallbox_alias=None, status=status,
        starting_meter_value=0, start_date=0, energy_consumed=0, energy_consumed_in_kwh=None,
        duration=None, token_id=None, ending_meter_value=None, end_date=end_date,
        termination_reason=None, transaction_token=None,
    )


def _client():
    client = api.P40ApiClient("wb")
    client._access_token, client._refresh_token = "old", "refresh"
    client._username, client._password = "admin", "pw"
    return client


def _calls(m, method, path):
    return m.requests.get((method, URL(BASE + path)), [])


def test_backoff_doubles_to_cap_and_resets():
    delays, d = [], station.POLL_INTERVAL
    for _ in range(6):
        d = station.next_poll_delay(d, ok=False)
        delays.append(d)
    assert delays == [30, 60, 120, 240, 300, 300]
    assert station.next_poll_delay(300, ok=True) == station.POLL_INTERVAL


def test_split_sessions_picks_active_and_last_closed():
    sessions = [_session("PWM_CHARGING", sid=3), _session("CLOSED", end_date=5, sid=2),
                _session("CLOSED", end_date=4, sid=1)]
    active, closed = station.split_sessions(sessions)
    assert (active.id, closed.id) == (3, 2)
    assert station.split_sessions([]) == (None, None)


@pytest.mark.asyncio
async def test_concurrent_refreshes_hit_the_wallbox_once():
    client = _client()
    with aioresponses() as m:
        m.post(BASE + "/v2/jwt/refresh", payload={"accessToken": "new"})
        results = await asyncio.gather(
            client._refresh_access_token("old"), client._refresh_access_token("old")
        )
        assert results == [True, True]
        assert len(_calls(m, "POST", "/v2/jwt/refresh")) == 1
        assert not _calls(m, "POST", "/v2/jwt/login")
    assert client._access_token == "new" and client._refresh_token == "refresh"
    await client._session.close()


@pytest.mark.asyncio
async def test_rate_limited_refresh_does_not_log_in_again():
    client = _client()
    with aioresponses() as m:
        m.post(BASE + "/v2/jwt/refresh", status=503, body="<html>nginx</html>")
        assert await client._refresh_access_token("old") is False
        assert not _calls(m, "POST", "/v2/jwt/login")
    await client._session.close()


@pytest.mark.asyncio
async def test_rejected_refresh_token_logs_in_again():
    client = _client()
    with aioresponses() as m:
        m.post(BASE + "/v2/jwt/refresh", status=401, payload={"msg": "Token has expired"})
        m.post(BASE + "/v2/jwt/login", payload={"accessToken": "a2", "refreshToken": "r2"})
        assert await client._refresh_access_token("old") is True
    assert (client._access_token, client._refresh_token) == ("a2", "r2")
    await client._session.close()


@pytest.mark.asyncio
async def test_close_logs_out_with_refresh_token():
    client = _client()
    with aioresponses() as m:
        m.post(BASE + "/v2/jwt/logout", payload={"acceptStatus": "ACCEPTED"})
        await client.close()
        (call,) = _calls(m, "POST", "/v2/jwt/logout")
        assert call.kwargs["json"] == {"refreshToken": "refresh"}
        assert call.kwargs["headers"]["Authorization"] == "Bearer old"
    assert client._access_token is None and client._refresh_token is None
