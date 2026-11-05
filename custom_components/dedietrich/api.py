"""Client for the BDR Thermea cloud API behind the De Dietrich Smart app."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from datetime import datetime
import hashlib
import json
import logging
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import aiohttp

from .const import (
    API_BASE,
    B2C_BASE,
    B2C_POLICY,
    CLIENT_ID,
    REDIRECT_URI,
    SCOPE,
    SUBSCRIPTION_KEY,
)

_LOGGER = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=30)


class AuthError(Exception):
    """The credentials were rejected, or the token could not be renewed."""


class ApiError(Exception):
    """The API returned an error or could not be reached."""


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


async def _token_request(session: aiohttp.ClientSession, grant: dict) -> dict:
    """Call the B2C token endpoint and stamp the result with its expiry time."""
    async with session.post(
        f"{B2C_BASE}/oauth2/v2.0/token",
        params={"p": B2C_POLICY},
        data={**grant, "client_id": CLIENT_ID},
        timeout=TIMEOUT,
    ) as resp:
        if resp.status in (400, 401):
            # invalid_grant: the refresh token expired or was revoked.
            raise AuthError(f"token endpoint: HTTP {resp.status} {await resp.text()}")
        resp.raise_for_status()
        token = await resp.json()
    token["expires_at"] = time.time() + int(token.get("expires_in", 3600))
    return token


async def async_login(email: str, password: str) -> dict:
    """Log in with the app's email and password, return an OAuth2 token.

    Mimics the app's B2C "self asserted" sign-in page (authorization code + PKCE).
    """
    verifier = secrets.token_urlsafe(64)
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    # The B2C cookies contain characters aiohttp would otherwise quote.
    jar = aiohttp.CookieJar(quote_cookie=False)
    try:
        async with aiohttp.ClientSession(cookie_jar=jar, timeout=TIMEOUT) as session:
            async with session.get(
                f"{B2C_BASE}/oauth2/v2.0/authorize",
                params={
                    "response_type": "code",
                    "client_id": CLIENT_ID,
                    "redirect_uri": REDIRECT_URI,
                    "scope": SCOPE,
                    "state": secrets.token_urlsafe(),
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "p": B2C_POLICY,
                    "brand": "remeha",
                    "lang": "en",
                    "nonce": "defaultNonce",
                    "prompt": "login",
                    "signUp": "False",
                },
            ) as resp:
                resp.raise_for_status()
                request_id = resp.headers["x-request-id"]

            csrf = next((c.value for c in jar if c.key == "x-ms-cpim-csrf"), None)
            if csrf is None:
                raise ApiError("login page did not set a CSRF cookie")
            state = json.dumps({"TID": request_id}, separators=(",", ":"))
            tx = "StateProperties=" + _b64url(state.encode("ascii"))

            async with session.post(
                f"{B2C_BASE}/{B2C_POLICY}/SelfAsserted",
                params={"tx": tx, "p": B2C_POLICY},
                headers={"x-csrf-token": csrf},
                data={
                    "request_type": "RESPONSE",
                    "signInName": email,
                    "password": password,
                },
            ) as resp:
                resp.raise_for_status()
                result = json.loads(await resp.text())
            if str(result.get("status")) != "200":
                raise AuthError(result.get("message") or "sign-in rejected")

            async with session.get(
                f"{B2C_BASE}/{B2C_POLICY}/api/CombinedSigninAndSignup/confirmed",
                params={
                    "rememberMe": "false",
                    "csrf_token": csrf,
                    "tx": tx,
                    "p": B2C_POLICY,
                },
                allow_redirects=False,
            ) as resp:
                location = resp.headers.get("location", "")
            code = parse_qs(urlparse(location).query).get("code")
            if not code:
                raise AuthError("sign-in did not return an authorization code")

            return await _token_request(
                session,
                {
                    "grant_type": "authorization_code",
                    "code": code[0],
                    "redirect_uri": REDIRECT_URI,
                    "code_verifier": verifier,
                },
            )
    except (aiohttp.ClientError, TimeoutError, KeyError, ValueError) as err:
        raise ApiError(f"login failed: {err!r}") from err


class DeDietrichClient:
    """Authenticated access to the BDR Thermea mobile API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        email: str,
        password: str,
        token: dict | None,
        on_token: Callable[[dict], None],
    ) -> None:
        self._session = session
        self._email = email
        self._password = password
        self._token = token
        self._on_token = on_token
        self._lock = asyncio.Lock()

    async def _access_token(self, force_renew: bool = False) -> str:
        async with self._lock:
            token = self._token
            if (
                not force_renew
                and token
                and token.get("expires_at", 0) - 60 > time.time()
            ):
                return token["access_token"]

            new = None
            if token and token.get("refresh_token"):
                try:
                    new = await _token_request(
                        self._session,
                        {
                            "grant_type": "refresh_token",
                            "refresh_token": token["refresh_token"],
                        },
                    )
                    new.setdefault("refresh_token", token["refresh_token"])
                except AuthError as err:
                    _LOGGER.info("Refresh token rejected, signing in again: %s", err)
                except (aiohttp.ClientError, TimeoutError) as err:
                    raise ApiError(f"token refresh failed: {err!r}") from err
            if new is None:
                # Refresh tokens expire eventually; the stored password lets us recover
                # without bothering the user.
                new = await async_login(self._email, self._password)

            self._token = new
            self._on_token(new)
            return new["access_token"]

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict | None = None,
    ) -> Any:
        """Send an API request and return the decoded JSON (or None)."""
        for attempt in (1, 2):
            token = await self._access_token(force_renew=attempt == 2)
            try:
                async with self._session.request(
                    method,
                    API_BASE + path,
                    json=json_body,
                    params=params,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Ocp-Apim-Subscription-Key": SUBSCRIPTION_KEY,
                    },
                    timeout=TIMEOUT,
                ) as resp:
                    if resp.status == 401 and attempt == 1:
                        continue
                    if resp.status == 401:
                        raise AuthError(f"{method} {path}: HTTP 401")
                    body = await resp.text()
                    if resp.status >= 400:
                        raise ApiError(f"{method} {path}: HTTP {resp.status} {body[:300]}")
                    # Commands answer with an empty body.
                    return json.loads(body) if body.strip() else None
            except (aiohttp.ClientError, TimeoutError, ValueError) as err:
                raise ApiError(f"{method} {path}: {err!r}") from err
        return None

    # --- reading -------------------------------------------------------------

    async def dashboard(self) -> dict:
        # The timestamp defeats a cache in front of the API.
        return await self.request(
            "GET", "/homes/dashboard", params={"t": str(int(time.time()))}
        )

    async def technical_details(self, appliance_id: str) -> dict:
        return await self.request("GET", f"/appliances/{appliance_id}/technicaldetails")

    async def energy_for_day(self, appliance_id: str, day: datetime) -> dict:
        start = day.strftime("%Y-%m-%d 00:00:00.000Z")
        end = day.strftime("%Y-%m-%d 23:59:59.999Z")
        return await self.request(
            "GET",
            f"/appliances/{appliance_id}/energyconsumption/daily",
            params={"startDate": start, "endDate": end},
        )

    # --- climate zones (the Smart TC thermostat) -----------------------------

    async def zone_schedule(self, zone_id: str, program: int) -> None:
        await self.request(
            "POST",
            f"/climate-zones/{zone_id}/modes/schedule",
            json_body={"heatingProgramId": program},
        )

    async def zone_activate_program(self, zone_id: str, program: int) -> None:
        await self.request(
            "POST",
            f"/climate-zones/{zone_id}/time-programs/heating/{program}/activate",
        )

    async def zone_manual(self, zone_id: str, setpoint: float) -> None:
        await self.request(
            "POST",
            f"/climate-zones/{zone_id}/modes/manual",
            json_body={"roomTemperatureSetPoint": setpoint},
        )

    async def zone_temporary_override(self, zone_id: str, setpoint: float) -> None:
        """Override the schedule until its next switch time."""
        await self.request(
            "POST",
            f"/climate-zones/{zone_id}/modes/temporary-override",
            json_body={"roomTemperatureSetPoint": setpoint},
        )

    async def zone_off(self, zone_id: str) -> None:
        """Frost protection only."""
        await self.request("POST", f"/climate-zones/{zone_id}/modes/anti-frost")

    async def zone_fireplace(self, zone_id: str, active: bool) -> None:
        await self.request(
            "POST",
            f"/climate-zones/{zone_id}/modes/fireplacemode",
            json_body={"fireplaceModeActive": active},
        )

    # --- domestic hot water ----------------------------------------------------

    async def dhw_mode(self, zone_id: str, mode: str) -> None:
        """mode: schedule | continuous-comfort | anti-frost | boost."""
        await self.request("POST", f"/hot-water-zones/{zone_id}/modes/{mode}")

    async def dhw_comfort_setpoint(self, zone_id: str, value: float) -> None:
        await self.request(
            "POST",
            f"/hot-water-zones/{zone_id}/comfort-setpoint",
            json_body={"comfortSetpoint": value},
        )

    async def dhw_reduced_setpoint(self, zone_id: str, value: float) -> None:
        await self.request(
            "POST",
            f"/hot-water-zones/{zone_id}/reduced-setpoint",
            json_body={"reducedSetpoint": value},
        )
