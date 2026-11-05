"""De Dietrich Smart (BDR Thermea cloud) integration for the Smart TC thermostat and heat pump."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import DeDietrichClient
from .const import CONF_TOKEN
from .coordinator import DeDietrichCoordinator
from .entity import appliance_device_info

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.WATER_HEATER,
]

type DeDietrichConfigEntry = ConfigEntry[DeDietrichCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: DeDietrichConfigEntry) -> bool:
    @callback
    def save_token(token: dict) -> None:
        # Refresh tokens rotate: keep the newest one so a restart doesn't need a login.
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_TOKEN: token}
        )

    client = DeDietrichClient(
        async_get_clientsession(hass),
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        entry.data.get(CONF_TOKEN),
        save_token,
    )
    coordinator = DeDietrichCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # Register the heat pumps first: the thermostat and hot water devices point at them.
    devices = dr.async_get(hass)
    for appliance_id in coordinator.data.appliances:
        device = devices.async_get_or_create(
            config_entry_id=entry.entry_id,
            **appliance_device_info(coordinator, appliance_id),
        )
        coordinator.appliance_device_ids[appliance_id] = device.id

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DeDietrichConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
