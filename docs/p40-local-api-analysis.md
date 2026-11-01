# KEBA P40: why the integration stops working, and how the official app talks to the wallbox

Date: 2026-10-31. Sources: KEBA eMobility App 5.21.0 (Android, `com.keba.wallbox`) reverse-engineered
with jadx and a JS beautifier, `openapi_v2.4.0.json`, the live Home Assistant logs, and this repo at `91f9b18`.
Only the **local network (REST, port 8443)** channel is covered. BLE and the KEBA cloud are out of scope.

---

## 1. What the HA logs show (live, today)

From HA `system_log` (logger `custom_components.keba.p40_api`):

| Signal | Count | First | Last | Rate |
|---|---|---|---|---|
| `401 Token has expired` → refresh | 540 | 2026-10-26 01:17 | 2026-10-31 16:23 | once every **900 s** |
| `GET /v2/wallboxes/<serial>` / `GET /v2/sessions` → **HTTP 500, empty body** | 93 681 | 2026-10-26 03:17 | still happening | once every **5.1 s** |

What this tells us:

* **The access token lives 15 minutes.** The refresh works every time. No "refresh failed" or "login failed" lines.
  So authentication is not what's broken.
* **The data endpoints fail and the auth endpoints don't.** `/v2/jwt/refresh` keeps returning 200 while
  `/v2/wallboxes/{sn}` and `/v2/sessions` return 500. The HTTP front-end on the wallbox is alive. The service
  behind it (wallbox state, sessions database) is not.
* **The integration has been hitting a broken box every 5 s for 5½ days.** It never backs off, and it logs
  every failure (≈ 94 k ERROR lines).
* The 15-min token life matches the default of Flask-JWT-Extended. The OpenAPI docstrings are also
  Python-style. So the P40 REST server is very likely a Python/Flask app that keeps a **server-side store
  of issued/revoked tokens**. Its own docs say so: `/jwt/logout` "invalidates access and refresh tokens",
  and `/jwt/fresh-login` exists "so that we don't keep generating new refresh tokens".

## 2. How the official app talks to the wallbox (local REST)

The app is Capacitor/Angular. The UI calls a native plugin (`ww.*`), and all local HTTP lives in the Java
plugin `com.keba.wallbox.localplugins.wallboxplugin` (Retrofit + OkHttp).

### 2.1 HTTP client
* **One cached client per (host, serial number)** (`ws0.m`). It is rebuilt only when the host, serial or
  auth mode changes, so TCP/TLS connections are pooled and reused.
* Base URL `https://<ip>:8443/<apiVersion>/` (e.g. `v2/`), taken from `GET /version`.
* Accepts the self-signed certificate (trust-all) unless the user chose verified mode.
* Timeouts: connect/read/write **120 s** (OkHttp builder fields; the field mapping is inferred).
* Every request: `Accept: application/json` and `Authorization: Bearer <accessToken>`.

### 2.2 Authentication lifecycle (the big difference)
| Step | Official app | Our integration (`p40_api.py`) |
|---|---|---|
| Login | `POST jwt/login` **once**, when the user adds the wallbox | On every config-flow validation, every entry setup, every HA restart, every reload, every `ConfigEntryNotReady` retry, and as the fallback whenever a refresh fails |
| Token storage | access + refresh token **persisted** in SharedPreferences, keyed by serial. Reused after app restarts. | In memory only. Lost on every restart or reload. |
| 401 handling | OkHttp `Authenticator` (`ew2.g`), **synchronized**: if another request already refreshed, reuse the new token. Otherwise `POST jwt/refresh` with `Bearer <refreshToken>`, store only the new **access** token, **keep the refresh token**. | Refresh with the refresh token, then fall back to a full new login if anything goes wrong. No lock. |
| Refresh fails | Raise `REST_TOKEN_INVALID` → the UI asks the user to log in again. **The app never logs in by itself.** | Silent new `jwt/login`, which mints yet another refresh token |
| Logout | `POST jwt/logout` with access token in the header and `{"refreshToken": ...}` in the body, when the user disconnects or removes the wallbox. Then deletes the stored tokens. | **Never.** Every refresh token we ever got stays valid on the device until it expires. |

### 2.3 Polling
* Dashboard refresh (`REFRESH_DATA_INTERVAL = 15e3`): **every 15 s**, **only while the page is visible**,
  and **never overlapping** (`isLoadingData` guard). With the app closed there is **no traffic at all**.
* One refresh for a P40 on the LAN:
  * `GET wallboxes/{sn}`
  * `GET sessions/?limit=1&orderField=SESSION_START_DATE&orderDir=DESC` (one session, no filter)
  * a firmware-version read, the enrollment status, a TOR config read, `configs/lmgmt/pvstatus` + `pv_enable`
* After start/stop it polls a bit faster (`CHECK_STATE_CHANGE_ACTION_INTERVAL` 5 s, max 120 s), then goes back to 15 s.
* Start/stop: `POST wallboxes/{sn}/start-charging` / `stop-charging` (the `-sync` variants also exist).

### 2.4 Writes
* Max current: `writeConfiguration(LMGMT, {max_available_current})` → `PUT configs/lmgmt/`.
  This is **the same thing our integration does.** The app never uses `/v2/profiles/chargepointmaxprofilezero`,
  which the `keba-kecontact-p40` library uses.

### 2.5 Load comparison
| | Requests/day |
|---|---|
| HA integration (4 requests every 5 s, 24/7: wallbox, lmgmt config, sessions ×2) | **≈ 69 000**, plus 96 refreshes, plus logins |
| App (≈ 6 requests every 15 s, only while open; say 10 min/day) | **≈ 240** |

That's about **300× more requests**, around the clock, including **two `/v2/sessions` database queries every 5 s**.

## 3. Likely causes (ranked) — superseded by section 7, kept for history

These are inferences: the server firmware wasn't available for analysis. Section 4 says how to tell them apart.

1. **Sustained request load on the wallbox's internal data service** (most likely). `/v2/wallboxes/{sn}` and
   `/v2/sessions` go through the service that talks to the charge controller and the sessions DB. A small leak
   per request (IPC handle, DB cursor/connection, memory) never shows up with the app's usage pattern. At about
   69 k requests/day it runs out after days. That fits: failures start after days, a reboot fixes it, only the
   data endpoints fail, auth keeps working, and the empty-body 500 looks like an unhandled exception.
   Aborted requests make it worse: our 10 s client timeout drops slow responses and asks again 5 s later,
   while the server may still be working on the abandoned request.
2. **Token-store growth from logins we never log out.** Every login creates a server-side refresh token and we
   never revoke it. Each HA restart or reload adds one, and so does every refresh-failure fallback and every
   config-flow run. The vendor's own docs warn against minting refresh tokens. On its own this grows too slowly
   to explain "days", but it's cheap to fix and it's exactly what the app does differently.
3. **Unhandled `/v2/sessions` query cost.** The filtered query (`filters=SOCKET_SERIAL_NUMBER=…`, `limit=5`)
   runs twice per poll. The app asks for `limit=1` with no filter. Cost grows with the size of the sessions table.

Ruled out by the logs: an expired access token or a broken refresh (the refreshes succeed), and certificates or
the network (401s still come back, so the TLS connection works).

## 4. Test now, while the box is still broken

Do these **before** rebooting the wallbox:

1. **Open the official app on the LAN.** If the dashboard shows live data while HA gets 500s, the failure is
   tied to our client (our token or our connection). If the app also fails, the backend is broken for everyone
   → cause 1.
2. **Reload the KEBA integration in HA** (new login, new connection pool). If data comes back, it's a
   client-state problem and the fixes in 5.2 will cure it.
3. Optional, from a shell (replace PASSWORD):
   ```sh
   T=$(curl -sk -X POST https://<ip>:8443/v2/jwt/login -H 'Content-Type: application/json' \
        -d '{"username":"admin","password":"PASSWORD"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["accessToken"])')
   curl -sk -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $T" https://<ip>:8443/v2/wallboxes/<serial>
   curl -sk -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $T" https://<ip>:8443/v2/wallboxes/<serial>/state
   ```
   This needs a login first. The access token is in the login response under `accessToken`.

Write the result into this file. It decides whether 5.1 (load) or 5.2 (auth) is the root cause. Do both anyway.

## 5. Plan for the integration (`custom_components/keba/`) — do this first

The integration does **not** use the `keba-kecontact-p40` library. All P40 traffic goes through
`p40_api.py` and `p40_charging_station.py`, so that's where the fixes go.

### 5.1 Cut the request load (P0)
* `p40_charging_station.py`: set `_polling_interval` to **15 s** (what the app uses). Make it an option
  (15–60 s) if we want it tunable.
* Use **one** `/v2/sessions` call per cycle instead of two. `get_current_session` and the "last completed"
  loop both call `get_sessions(limit=5)`. Fetch once and work out both from that result.
  Only fetch sessions when `state`/`session_active` changed since the last cycle, or every 4th cycle.
* Read `GET /v2/configs/lmgmt/max_available_current` on start, after our own writes, and every ~10 min.
  Not on every poll.
* **Back off on errors:** after a 5xx or a timeout, double the delay (15 s → 30 → 60 … up to 5 min). Reset after
  a success. Log the first failure at ERROR, then DEBUG, then once at INFO when it recovers. (Today: 94 k
  ERROR lines and non-stop pressure on a broken box.)
* Raise the client timeout from 10 s to 30 s so we stop dropping slow responses and re-asking.

### 5.2 Handle the auth lifecycle like the app (P0)
* `p40_api.py._request` / `_refresh_access_token`:
  * Put an `asyncio.Lock` around the refresh (same idea as the app's synchronized `Authenticator`). After taking
    the lock, if the token changed while we waited, just retry with it.
  * On a 401 from the **refresh** call, do one full `login()`. That's acceptable, because HA can't prompt the
    user. Don't log in again for any other error. Today `_refresh_access_token` also logs in again after a
    timeout or a connection error, which mints tokens during outages.
* **Add `logout()`**: `POST /v2/jwt/logout` with the `Bearer <access>` header and body
  `{"refreshToken": <refresh>}`. Call it:
  * in `async_unload_entry`, before `api_client.close()`;
  * in `config_flow.py` after the validation login;
  * before any fallback re-login, on the old token pair.
* Optional (P2): keep the refresh token in the config entry or a `Store` so an HA restart refreshes instead of
  logging in again, like the app does.

### 5.3 Structure (P1)
* Move polling to a `DataUpdateCoordinator`. It gives us the interval, `UpdateFailed` → entities unavailable,
  and logs once instead of every time. It replaces the hand-rolled `_poll_updates` loop and its callbacks.
* Optional: reuse HA's client session (`async_get_clientsession(hass, verify_ssl=False)`) instead of a private
  `aiohttp.ClientSession`. Both keep connections alive; the shared one is one less thing to close.

### 5.4 Workaround (P2)
* Add a **"Restart wallbox" button** (`POST /v2/systems/reboot`, which the app exposes as `performSystemReboot`)
  so users can recover without the web UI. Don't reboot automatically: it interrupts charging.

### 5.5 How we know it worked
* Unit tests for: one sessions call per cycle; the backoff sequence; one refresh when two requests hit 401 at
  the same time; logout called on unload.
* Run on the real box for **≥ 2 weeks** without a wallbox reboot, and watch the `p40_api` error count.

## 6. Notes for `keba-kecontact-p40` (later)

The library (`../keba-kecontact-p40`, `client.py`) isn't used by HA today. If we switch HA over to it later, fix these first:

| Topic | Library today | Change to |
|---|---|---|
| 401 handling | **Full `login()` on every 401**, with no refresh token stored at all. With a 15-min token life that's **96 new logins/day**, each minting a server-side refresh token: the pattern the vendor warns against. | Store `refreshToken`. On a 401 call `/v2/jwt/refresh` under a lock. Only log in again if the refresh returns 401. |
| Logout | none | `async def logout()` → `POST /v2/jwt/logout` with `{"refreshToken": ...}`; also an `async with` / `close()` that calls it |
| Max current | `POST /v2/profiles/chargepointmaxprofilezero` (an OCPP charging profile) | The official app uses `PUT /v2/configs/lmgmt` `max_available_current`. Keep the profile only if we want non-persistent limits, and check on the device whether it gets stored in flash. |
| Sessions | not implemented | `get_sessions(limit=1, orderField=SESSION_START_DATE, orderDir=DESC)`, like the app |
| Timeout | 10 s total | 30 s, and the caller handles backoff |
| API version | hard-coded `/v2` | fine for now; the app reads `GET /version` and builds the base path from it |

## Appendix: where things are in the app
* Retrofit interfaces (jadx, obfuscated): `nz` (jwt/login, logout, logoutAll, fresh-login, change-password),
  `n36` (jwt/refresh), `y48` (wallboxes, sessions, start/stop), `la1` (configs/*), `xd7` (systems/reboot, timesynch).
* Auth: `oz` (login/logout, token store), `ew2.g` (OkHttp `Authenticator`, synchronized refresh),
  `o36.m` (the refresh call), `ws0.m` (client factory: cache, TLS, interceptors, timeouts).
* Token store: `h3` → SharedPreferences, `WallboxTokens(accessToken, refreshToken)` keyed by serial.
* UI polling: `assets/public/337.*.js` (`REFRESH_DATA_INTERVAL = 15e3`, `refreshDataIfNecessary` →
  `isPageVisible()`), `main.*.js` `getWallboxOverviewInfo` / `getLastChargingSession` (`limit: 1`).

## 7. Root-cause investigation (2026-10-31, same day)

### 7.1 What could not be done
* Firmware `KC-P40-SW-V1.5.2.raucb` is an **encrypted RAUC bundle**: the payload is AES-256-CBC, and its key
  sits in a CMS EnvelopedData block for 3 device ECDH keys. It can't be decrypted without a device key
  (on Linux or anywhere). Release notes 1.4.0–1.5.2 mention no REST API stability fix.
* "The official app works" was wrong: the app was using the **KEBA cloud** (OCPP path). On the local network it fails too.

### 7.2 Black-box facts (live wallbox, API 2.5.0)
* The REST server is **Python/Flask + PyJWT** (Werkzeug 404 page, PyJWT error strings; changelog mentions orjson).
  It fronts an internal **CPM** service (`/v2/systems/cpmrest/ping`; changelog 2.1.0: "exception handling with
  correct response for the situation where CPM is down"). Request logs go to `/opt/restapi/logs`.
* In the failed state, from a brand-new client (other IP, fresh login, app headers, refreshed token):
  * `POST jwt/login` 200, `POST jwt/refresh` 200, `GET /version` 200
  * missing / malformed / expired token → correct JSON 401
  * **every** valid-token endpoint (wallboxes, sessions, configs, `cpmrest/ping`, `updates/log`,
    `diagnostics/export`, **`jwt/logout`**) → **500, empty body, no content type, ~50 ms**
  * → the failure happens after JWT verification, in something shared by all authenticated endpoints.
* `/serialnumber` → 500 for every client (a separate known firmware bug).

### 7.3 Timeline from HA long-term statistics (`sensor.keba_p40_temperature`, frozen = API failing)
* Over the last 40 days the API mostly **didn't work**. Working windows start at **00:00 UTC**
  (09-22, 25, 26, 27, 28, 29, 10-12, 10-25, 10-26, 10-31) and end 1–4 h later. Some nights
  (10-13…10-19) have no recovery at all.
* 10-31: recovered 00:00 UTC, failed 02:15–02:20 UTC. 10-26: HA logged in 00:02:29 UTC, failed at
  **02:17:29 UTC = exactly the 9th 15-min token refresh** (7199.6 s after the first one).

### 7.4 Working hypothesis
A **server-side token store with limited capacity**:
1. Every login and refresh adds an entry.
2. When it's full, newly issued tokens are not usable: authenticated requests → empty 500. Logout fails too.
3. A nightly job at 00:00 UTC deletes expired entries (15-min access tokens) → partial recovery.
4. Long-lived refresh tokens from logins that are **never logged out** survive the cleanup, so the free space shrinks
   over time until there's no recovery at all. A reboot clears it ("works for days after reboot").

HA creates 96 access tokens/day and a new refresh token on every restart, reload, setup retry and
refresh fallback, and never logs out. The app logs in once and logs out when it disconnects.
Alternative still open: a daily request quota that resets at 00:00 UTC.
**Decisive test:** `p40_rootcause.py` (in the session scratchpad). Phase A = many requests on one
token; phase B = token churn. Run it with the HA integration disabled, right after a reset.

### 7.5 Consequences for the plan (section 5), if confirmed
* The **token lifecycle** fix (5.2) becomes P0 #1: no re-login loops, logout on unload/reload/config-flow,
  persist and reuse the refresh token across HA restarts.
* Also cut token churn: the access token lives 15 min, so about 96 refreshes/day is the minimum while polling.
  Polling more slowly doesn't reduce that. Only "no new logins" and "logout" do.
* Clean up: the backlog of never-revoked refresh tokens only goes away by reboot (or expiry).
  `POST /v2/jwt/logout-all` (fresh token required) could clear them without a reboot. Test it once.

### 7.6 Test results
* **2026-10-31 17:01–17:41 (after a wallbox reboot, HA integration disabled), `p40_rootcause.py` phase A:**
  8000 × `GET /v2/wallboxes/{sn}` at ~3.4 req/s on one login, 2 refreshes → all 200.
  **Request volume is not the trigger** (HA had made about 6000 requests when it broke on 10-26).
* Same run, phase B: six refreshes ~0.5 s apart succeeded; the 7th returned **503**. That's a rate limit on
  `jwt/refresh`: the API was healthy right after (logout 200). Not the failure mode. The integration must
  treat 503 on refresh as "retry later", **not** as "log in again" (`_refresh_access_token` does the latter today).
* Next: `p40_tokenstore.py refresh` (and then `login`), rate-limit aware, counts tokens until failure.
* **2026-10-31 17:55–19:10, `p40_tokenstore.py refresh`:** 400 refreshes, one every 11 s, each new access
  token used → all 200, no throttling. **Token issuance is not the trigger.** The token-store hypothesis
  (7.4) is **refuted**.
* Still ruled out: request volume on `/v2/wallboxes/{sn}`, token churn, client IP, headers.
  The only HA automation using the wallbox (auto-start on plug-in) fires rarely, so HA's traffic is
  basically the poll loop. Not yet tested: HA's filtered `/v2/sessions` query, the `lmgmt` read,
  one long-lived keep-alive connection, and client-side aborts (10 s timeout).
  Next: `p40_bisect.py` (variants S, L, K, T).
* **2026-10-31 20:39 – 11-01 00:58, `p40_tokenstore.py login 1.5 2000`** (wallbox freshly rebooted, HA integration
  disabled): **2000 logins without logout**, each new access token used once → **all 200**.
  **Login churn is not the trigger either.** The token-store hypothesis is refuted for refreshes (400) and for
  logins (2000, far more than HA could build up between reboots).
  * New finding: an **nginx** front-end rate-limits `jwt/login` (and `jwt/refresh`). After roughly 12 rapid
    calls it returns **503** (nginx HTML page, no `Retry-After`) and recovers in under 65 s
    (166 throttles during the run, ~8 logins/min sustained). The integration must treat 503 as "back off and
    retry", never as "log in again".
  * Side effect: 2000 unrevoked refresh tokens are now on the box (the final logout used an expired
    token, a script bug). **Reboot before the next test.**
* Remaining candidates, all part of HA's poll and not yet exercised by any test: the filtered
  `/v2/sessions` query (`SOCKET_SERIAL_NUMBER`, `limit=5`, 2× per poll), the `lmgmt/max_available_current`
  read, a single long-lived keep-alive connection, client-side aborts (10 s timeout).
  Next: `p40_bisect.py` (variants S, L, K, T) after a reboot.
* **2026-11-01 01:20–03:13 (00:20–02:13 UTC), `p40_bisect.py`** (wallbox freshly rebooted, HA integration disabled):
  S = filtered sessions query ×4000, L = lmgmt read ×2500, K = HA's full poll ×2500 over one keep-alive
  connection, T = like K with 50 sessions requests aborted mid-flight. About 26,500 requests at ~10× HA's rate,
  incl. refreshes → **all 200, nothing reproduced.**
* **Conclusion so far:** no individual request type, request volume, token churn, keep-alive connection or client
  abort breaks a *freshly rebooted* wallbox within hours. HA's failures came after **days of uptime**, then
  recurred daily (recovery at 00:00 UTC, failure 1–4 h later). Most consistent remaining model: a resource on
  the wallbox that grows with uptime and traffic (log storage, sessions DB, memory). A 00:00 UTC housekeeping
  job frees part of it; traffic fills it again within hours. Not yet observable from outside. The diagnostics
  archive (works while healthy) is the next evidence source.

## 8. Evidence from the wallbox's own diagnostics archive (2026-11-01)

Exported from the healthy box via `GET /v2/configs/diagnostics/export` (13 MB; logs back to September).
Firmware 1.5.1 (restapi 2.5.0, cpm 5.6.1, mgmt 1.5.0).

### 8.1 Architecture (from config files)
* nginx (TLS :8443, rate-limits `jwt/*` → 503) → **Hypercorn, 1 worker, Unix socket** → **Quart** app (restapi,
  runs as `www-data`) → blocking `python-requests` → **CPM** (Java/Jetty, `127.0.0.1:7378/cpm`, user `cpm`,
  `CPM_REST_TIMEOUT = 60`). A third service, **mgmt** (C++, root), talks to CPM and the charge controller.
* Logs, all on **`/srv/logging`** (ext4, 1000 M, `errors=remount-ro`, ~5 % reserved for root):
  restapi → `system/restapi/restapi.log` (app + Hypercorn access/error log in the same file, INFO, **full response
  bodies logged**, rotated hourly at :17 and daily at 00:00 UTC, gzip); CPM → `cpm/kemove.log` (**DEBUG: every HTTP
  request/response incl. entities**, log4j2 20 MB/daily rollover, delete when > 100 M); mgmt → `mgmt/` (zlog,
  ~10.5 MB/day, date-named files, no effective cap). `max_days_of_logs = 0` (age-based cleanup disabled).
* A separate security finding was reported to KEBA privately and is intentionally not described here.

### 8.2 What happens at a failure (two independent days, identical sequence)
| | 10-26 (UTC) | 10-31 (UTC) |
|---|---|---|
| HA token refreshes / data OK since | 00:02 login | 00:00 recovery |
| **restapi log stops mid-request** (last `GET:` lines without "Request took") | 01:53:51 | 01:28:20 |
| **CPM log stops** (even its 5-min cloud heartbeats) | 01:53:46 | 01:29:05 |
| HA still gets valid data (no timeouts, no errors) | until 02:17 | until ~02:15 |
| **First 500** = restapi's hourly log rotation | **02:17:29** | **02:15–02:20** |
| restapi log afterwards | empty until 00:00 rotation | empty until reboot |

* Meanwhile **mgmt (root) keeps logging** to the same partition, and CPM keeps answering mgmt's heartbeats
  (40 per 10 min, uninterrupted). **The services don't die. The two non-root services lose the ability to log at
  the same instant**, and the REST API starts failing at its next log rotation.
* restapi has **never logged an error** in any retained log, not even during the days of 500s.
* No kernel FS errors, no remount, no OOM, nothing notable in cron/auth at the stop moments.

### 8.3 Interpretation
* The failure is tied to the box's **logging / log-rotation path**, not to the API logic, auth, or any single request.
  That explains why none of our synthetic tests (all on a freshly rebooted box with ~500 M free) reproduced it,
  and why the timeline aligns with log rotations (failure at :17, recovery at the 00:00 UTC rollover/cleanup, reboot).
* Most consistent mechanism (**not yet proven**): `/srv/logging` reaches the **root-reserved threshold (~95 %)**.
  From then on `www-data` and `cpm` get ENOSPC (logging silently stops) while root's mgmt can still write. When restapi
  then rotates its log it can't reopen it, and the request path fails → handled empty 500. The 00:00 UTC
  rollover deletes old CPM logs (> 100 M rule), which frees a few MB → recovery until traffic refills it.
  A reboot evidently frees more (open-but-deleted files released, tmp state cleared). The archive alone can't show
  the pre-reboot fill level (today 440 M used, ~145 M in files not included in the archive).
* **HA's part:** each HA request is logged in full twice (restapi INFO with the response body, CPM DEBUG with the entity),
  and the sessions queries produce the largest bodies. 69 k requests/day feed exactly the logs on the partition that
  fails. The official app (cloud, or a few local calls per day) never produces that volume.

### 8.4 How to prove it
Re-enable HA unchanged and snapshot `diskspace.txt` via the diagnostics export a few times a day. If `/srv/logging` climbs
to ~95 % right before the next failure, it's proven. (The export itself fails once the API is broken, so the last snapshot
before the failure is the evidence.)

### 8.5 What we can do without changing server software
1. **Integration (P0): cut logged volume.** Poll only `GET /v2/wallboxes/{sn}` (every 15–30 s). Read sessions only
   on a state change / session end, never twice per poll. Read `lmgmt` only at start-up and after our own writes (each read
   also spawns `kemove-config`). Back off on 5xx. That's ≥10× fewer and smaller log lines.
2. **Box configuration via the official API:** `PUT /v2/configs/diagnostics` with `max_days_of_logs` = 7–14 (currently 0 =
   never delete by age), so the box's own housekeeping can free space. Make sure `log_level_debug_enable` stays `false`.
3. Token hygiene (5.2) still applies (logout on unload, back off on 503), but it's not the root cause.
4. Workaround if it still happens: a "Restart wallbox" button (`POST /v2/systems/reboot`). Report to KEBA: data endpoints
   fail when log writing fails; CPM logs full entities at DEBUG by default; mgmt logs are unbounded.

### 8.6 Done (2026-11-01)
* **Wallbox:** `max_days_of_logs` 0 → **7** via `PUT /v2/configs/diagnostics` (verified by reading it back).
* **Integration, branch `fix/p40-reduce-rest-load`:**
  * `p40_charging_station.py`: polls `GET /v2/wallboxes/{sn}` every **15 s** (was: 4 requests every 5 s).
    Sessions: **one** query, only when state/session/plug changes, after our own actions, or every 10 min.
    `lmgmt` read only at start, after our writes, or every 10 min. Session energy = live meter − session start meter
    (verified identical to `energyConsumed`). **Exponential backoff** 15 s → 300 s while the wallbox fails, logged once.
    Updates are serialized (lock), so actions and polls can't overlap.
  * `p40_api.py`: timeout 10 → 30 s. Refresh under a lock, keeping the refresh token. Log in again **only** if the
    refresh token is rejected (401/422); 503 (nginx rate limit), 5xx and timeouts retry on the next poll. **`close()` logs out**
    (`POST /v2/jwt/logout` with the refresh token), so unload, failed setups (retry loop) and config-flow validation revoke their
    tokens. Removed `get_current_session`.
  * `__init__.py`: setup reuses the device info it already fetched.
  * `tests/test_p40.py`: backoff, session split, single refresh under concurrency, no re-login on 503, re-login on 401,
    logout on close. Run with pytest + pytest-asyncio + aioresponses + aiohttp 3.12 (aioresponses doesn't support aiohttp 3.14 yet).
* Expected steady-state load: ~6 k requests/day instead of ~69 k (≈11× fewer), and much less log volume, because the large
  session bodies are no longer fetched every poll.
* Not done: persisting the refresh token across HA restarts (optional), a `DataUpdateCoordinator` refactor, a reboot button.
  Pre-existing bug spotted: `set_output` calls `set_installer_io_config`, which doesn't exist in `p40_api.py`.
