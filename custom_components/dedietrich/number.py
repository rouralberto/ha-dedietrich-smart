"""Comfort and eco (reduced) hot water temperatures."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DeDietrichConfigEntry
from .entity import DeDietrichEntity

# key -> (dashboard field, setPointRanges prefix)
SETPOINTS = {
    "dhw_comfort": ("comfortSetPoint", "comfortSetpoint"),
    "dhw_reduced": ("reducedSetpoint", "reducedSetpoint"),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DeDietrichConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        DhwSetpoint(coordinator, "hot_water_zone", zone_id, key)
        for zone_id in coordinator.data.hot_water_zones
        for key in SETPOINTS
    )


class DhwSetpoint(DeDietrichEntity, NumberEntity):
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_step = 1.0
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator, kind, item_id: str, key: str) -> None:
        self._field, self._range = SETPOINTS[key]
        self._attr_translation_key = key
        super().__init__(coordinator, kind, item_id, key)

    def _update_attrs(self) -> None:
        zone = self.item
        ranges = zone.get("setPointRanges") or {}
        self._attr_native_value = zone.get(self._field)
        self._attr_native_min_value = ranges.get(f"{self._range}Min") or 10.0
        self._attr_native_max_value = ranges.get(f"{self._range}Max") or 65.0

    async def async_set_native_value(self, value: float) -> None:
        client = self.coordinator.client
        command = (
            client.dhw_comfort_setpoint(self.item_id, value)
            if self._field == "comfortSetPoint"
            else client.dhw_reduced_setpoint(self.item_id, value)
        )
        await self.coordinator.async_command(command, self.item, **{self._field: value})
