"""Water heater entity for each domestic hot water (DHW) zone."""

from __future__ import annotations

from typing import Any

from homeassistant.components.water_heater import (
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DeDietrichConfigEntry
from .entity import DeDietrichEntity

_NATIVE = hasattr(WaterHeaterEntity, "native_temperature_unit")  # HA 2026.11+
_T = "_attr_native_" if _NATIVE else "_attr_"

# dhwZoneMode -> operation shown in HA
ZONE_MODE_TO_OPERATION = {
    "Scheduling": "schedule",
    "ContinuousComfort": "comfort",
    "Off": "eco",
    "FrostProtection": "eco",
    "Boost": "boost",
}
# operation -> endpoint below /hot-water-zones/{id}/modes/
OPERATION_TO_ENDPOINT = {
    "schedule": "schedule",
    "comfort": "continuous-comfort",
    "eco": "anti-frost",
    "boost": "boost",
}
OPERATION_TO_ZONE_MODE = {
    "schedule": "Scheduling",
    "comfort": "ContinuousComfort",
    "eco": "Off",
    "boost": "Boost",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DeDietrichConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        HotWater(coordinator, "hot_water_zone", zone_id, None)
        for zone_id in coordinator.data.hot_water_zones
    )


class HotWater(DeDietrichEntity, WaterHeaterEntity):
    """schedule: follow the DHW clock program; comfort: always the comfort temperature;
    eco: always the reduced temperature; boost: heat to comfort once, now."""

    _attr_name = None
    _attr_translation_key = "dhw"
    _attr_supported_features = (
        WaterHeaterEntityFeature.TARGET_TEMPERATURE
        | WaterHeaterEntityFeature.OPERATION_MODE
    )
    _attr_target_temperature_step = 1.0
    if _NATIVE:
        _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    else:
        _attr_temperature_unit = UnitOfTemperature.CELSIUS

    def _update_attrs(self) -> None:
        zone = self.item
        operation = ZONE_MODE_TO_OPERATION.get(zone.get("dhwZoneMode"))
        ranges = zone.get("setPointRanges") or {}
        self._attr_operation_list = ["schedule", "comfort", "eco"] + (
            ["boost"] if zone.get("capabilityBoostMode") else []
        )
        self._attr_current_operation = operation
        if operation == "eco":
            self._attr_min_temp = ranges.get("reducedSetpointMin") or 10.0
            self._attr_max_temp = ranges.get("reducedSetpointMax") or 60.0
        else:
            self._attr_min_temp = ranges.get("comfortSetpointMin") or zone.get("setPointMin") or 40.0
            self._attr_max_temp = ranges.get("comfortSetpointMax") or zone.get("setPointMax") or 65.0
        setattr(self, f"{_T}current_temperature", zone.get("dhwTemperature"))
        setattr(self, f"{_T}target_temperature", zone.get("targetSetpoint"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        zone = self.item or {}
        return {
            "status": zone.get("dhwStatus"),
            "comfort_setpoint": zone.get("comfortSetPoint"),
            "reduced_setpoint": zone.get("reducedSetpoint"),
            "boost_end_time": zone.get("boostModeEndTime"),
        }

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Change the temperature of whichever setpoint the current mode uses."""
        value = kwargs[ATTR_TEMPERATURE]
        client = self.coordinator.client
        if self.current_operation == "eco":
            await self.coordinator.async_command(
                client.dhw_reduced_setpoint(self.item_id, value),
                self.item, reducedSetpoint=value, targetSetpoint=value,
            )
        else:
            await self.coordinator.async_command(
                client.dhw_comfort_setpoint(self.item_id, value),
                self.item, comfortSetPoint=value, targetSetpoint=value,
            )

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        client = self.coordinator.client
        zone = self.item
        if operation_mode == "boost" and zone.get("dhwZoneMode") != "Scheduling":
            # The API only accepts boost from schedule mode.
            await self.coordinator.async_command(client.dhw_mode(self.item_id, "schedule"))
        target = {
            "comfort": zone.get("comfortSetPoint"),
            "boost": zone.get("comfortSetPoint"),
            "eco": zone.get("reducedSetpoint"),
        }.get(operation_mode, zone.get("targetSetpoint"))
        await self.coordinator.async_command(
            client.dhw_mode(self.item_id, OPERATION_TO_ENDPOINT[operation_mode]),
            zone, dhwZoneMode=OPERATION_TO_ZONE_MODE[operation_mode], targetSetpoint=target,
        )
