"""Polls the BDR Thermea dashboard and runs commands for the entities."""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass, field
from datetime import datetime
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import ApiError, AuthError, DeDietrichClient
from .const import DOMAIN, ENERGY_INTERVAL, REFRESH_AFTER_COMMAND, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)


@dataclass
class DeDietrichData:
    """One poll of the dashboard, indexed by id.

    The dicts are the API's own JSON objects. Appliances also carry two extra keys:
    "_technical" (gateway / model details) and "_energy" (today's energy figures, or
    None if not fetched yet).
    """

    appliances: dict[str, dict] = field(default_factory=dict)
    climate_zones: dict[str, dict] = field(default_factory=dict)
    hot_water_zones: dict[str, dict] = field(default_factory=dict)


class DeDietrichCoordinator(DataUpdateCoordinator[DeDietrichData]):
    """Fetches the dashboard every minute and today's energy every 15 minutes."""

    config_entry: ConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: DeDietrichClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client
        self._technical: dict[str, dict] = {}
        self._energy: dict[str, dict] = {}
        self._energy_at: dict[str, datetime] = {}
        self._refresh_soon: CALLBACK_TYPE | None = None
        # appliance id -> device registry id, so zone devices can link to their heat pump.
        self.appliance_device_ids: dict[str, str] = {}

    async def _async_update_data(self) -> DeDietrichData:
        try:
            return await self._fetch()
        except AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ApiError as err:
            raise UpdateFailed(str(err)) from err

    async def _fetch(self) -> DeDietrichData:
        dashboard = await self.client.dashboard()
        _LOGGER.debug("Dashboard: %s", dashboard)
        now = dt_util.now()
        data = DeDietrichData()

        for appliance in dashboard.get("appliances") or []:
            appliance_id = appliance["applianceId"]

            if appliance_id not in self._technical:
                try:
                    self._technical[appliance_id] = await self.client.technical_details(
                        appliance_id
                    )
                except ApiError as err:
                    _LOGGER.warning("No technical details for %s: %s", appliance_id, err)
            appliance["_technical"] = self._technical.get(appliance_id) or {}

            if appliance.get("capabilityEnergyConsumption"):
                await self._update_energy(appliance_id, now)
            appliance["_energy"] = self._energy.get(appliance_id)

            data.appliances[appliance_id] = appliance
            for zone in appliance.get("climateZones") or []:
                data.climate_zones[zone["climateZoneId"]] = zone
            for zone in appliance.get("hotWaterZones") or []:
                data.hot_water_zones[zone["hotWaterZoneId"]] = zone

        return data

    async def _update_energy(self, appliance_id: str, now: datetime) -> None:
        last = self._energy_at.get(appliance_id)
        if last and last.date() == now.date() and now - last < ENERGY_INTERVAL:
            return
        try:
            result = await self.client.energy_for_day(appliance_id, now)
        except ApiError as err:
            _LOGGER.warning("No energy data for %s: %s", appliance_id, err)
            return
        _LOGGER.debug("Energy today: %s", result)
        rows = (result or {}).get("data") or []
        # No row yet just after midnight: report zero so the daily totals reset.
        self._energy[appliance_id] = rows[0] if rows else {}
        self._energy_at[appliance_id] = now

    async def async_command(
        self, command: Awaitable[Any], item: dict | None = None, **changes: Any
    ) -> None:
        """Run a command, show its effect right away, and re-poll a bit later.

        `changes` are written into `item` (a dict inside self.data) so the UI reflects
        the command until the cloud dashboard catches up.
        """
        try:
            await command
        except AuthError as err:
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(f"De Dietrich login expired: {err}") from err
        except ApiError as err:
            raise HomeAssistantError(f"De Dietrich cloud rejected the command: {err}") from err

        if item is not None and changes:
            item.update(changes)
            self.async_update_listeners()

        if self._refresh_soon:
            self._refresh_soon()
        self._refresh_soon = async_call_later(
            self.hass, REFRESH_AFTER_COMMAND, self._async_refresh_soon
        )

    async def _async_refresh_soon(self, _now: datetime) -> None:
        self._refresh_soon = None
        await self.async_request_refresh()

    async def async_shutdown(self) -> None:
        if self._refresh_soon:
            self._refresh_soon()
            self._refresh_soon = None
        await super().async_shutdown()
