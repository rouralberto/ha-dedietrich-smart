"""Fireplace mode: ignore the thermostat's room temperature (e.g. a stove heats its room)
and keep heating the rest of the house."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DeDietrichConfigEntry
from .entity import DeDietrichEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DeDietrichConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        FireplaceMode(coordinator, "climate_zone", zone_id, "fireplace")
        for zone_id, zone in coordinator.data.climate_zones.items()
        if zone.get("capabilityFirePlaceMode")
    )


class FireplaceMode(DeDietrichEntity, SwitchEntity):
    _attr_translation_key = "fireplace"

    def _update_attrs(self) -> None:
        self._attr_is_on = bool(self.item.get("firePlaceModeActive"))

    async def _set(self, active: bool) -> None:
        await self.coordinator.async_command(
            self.coordinator.client.zone_fireplace(self.item_id, active),
            self.item, firePlaceModeActive=active,
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)
