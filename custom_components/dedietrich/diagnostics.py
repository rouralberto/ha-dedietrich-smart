"""Diagnostics download: the raw cloud data, without credentials."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from . import DeDietrichConfigEntry
from .const import CONF_TOKEN

TO_REDACT = {CONF_EMAIL, CONF_PASSWORD, CONF_TOKEN, "serialNumber", "deviceIndex"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: DeDietrichConfigEntry
) -> dict[str, Any]:
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "data": async_redact_data(asdict(entry.runtime_data.data), TO_REDACT),
    }
