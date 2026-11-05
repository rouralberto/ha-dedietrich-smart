"""Climate entity for each climate zone (the Smart TC room thermostat)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_TENTHS, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DeDietrichConfigEntry
from .entity import DeDietrichEntity

# HA 2026.11 renamed the temperature members to native_* (old names removed in 2027.11).
_NATIVE = hasattr(ClimateEntity, "native_temperature_unit")
_T = "_attr_native_" if _NATIVE else "_attr_"

ZONE_MODE_TO_HVAC = {
    "Scheduling": HVACMode.AUTO,
    "TemporaryOverride": HVACMode.AUTO,
    "Manual": HVACMode.HEAT,
    "FrostProtection": HVACMode.OFF,
}
DEMAND_TO_ACTION = {
    "Idle": HVACAction.IDLE,
    "ProducingHeat": HVACAction.HEATING,
    "RequestingHeat": HVACAction.HEATING,
    "ProducingCold": HVACAction.COOLING,
    "RequestingCold": HVACAction.COOLING,
}
# The app's three clock programs.
PRESETS = {"schedule_1": 1, "schedule_2": 2, "schedule_3": 3}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DeDietrichConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        ZoneClimate(coordinator, "climate_zone", zone_id, None)
        for zone_id in coordinator.data.climate_zones
    )


class ZoneClimate(DeDietrichEntity, ClimateEntity):
    """AUTO follows the clock program (changing the temperature overrides it until the
    next switch), HEAT holds a fixed temperature, OFF is frost protection.

    While the heat pump is in forced cooling (see select.py), the fixed-temperature mode
    is shown and set as COOL instead of HEAT: it's the same zone mode, but cooling.
    """

    _attr_name = None
    _attr_translation_key = "zone"
    _attr_preset_modes = list(PRESETS)
    _attr_precision = PRECISION_TENTHS
    _attr_target_temperature_step = 0.5
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    if _NATIVE:
        _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    else:
        _attr_temperature_unit = UnitOfTemperature.CELSIUS

    @property
    def _fixed_mode(self) -> HVACMode:
        """What "hold a fixed temperature" means right now: heating or cooling."""
        if self.appliance.get("operatingMode") == "ForcedCooling":
            return HVACMode.COOL
        return HVACMode.HEAT

    def _update_attrs(self) -> None:
        zone = self.item
        fixed = self._fixed_mode
        self._attr_hvac_modes = [HVACMode.AUTO, fixed, HVACMode.OFF]
        mode = ZONE_MODE_TO_HVAC.get(zone.get("zoneMode"))
        if mode == HVACMode.HEAT:
            mode = fixed
        program = zone.get("activeHeatingClimateTimeProgramNumber")
        self._attr_hvac_mode = mode
        self._attr_hvac_action = (
            HVACAction.OFF if mode == HVACMode.OFF
            else DEMAND_TO_ACTION.get(zone.get("activeComfortDemand"))
        )
        self._attr_preset_mode = f"schedule_{program}" if mode == HVACMode.AUTO else None
        self._attr_min_temp = zone.get("setPointMin") or 5.0
        self._attr_max_temp = zone.get("setPointMax") or 30.0
        setattr(self, f"{_T}current_temperature", zone.get("roomTemperature"))
        setattr(
            self,
            f"{_T}target_temperature",
            None if mode == HVACMode.OFF else zone.get("setPoint"),
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        zone = self.item or {}
        return {
            "zone_mode": zone.get("zoneMode"),
            "control_strategy": zone.get("controlStrategy"),
        }

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        mode = kwargs.get(ATTR_HVAC_MODE) or self.hvac_mode
        if temperature is None:
            if ATTR_HVAC_MODE in kwargs:
                await self.async_set_hvac_mode(kwargs[ATTR_HVAC_MODE])
            return
        client = self.coordinator.client
        if mode == HVACMode.AUTO:
            await self.coordinator.async_command(
                client.zone_temporary_override(self.item_id, temperature),
                self.item, zoneMode="TemporaryOverride", setPoint=temperature,
            )
        else:
            await self.coordinator.async_command(
                client.zone_manual(self.item_id, temperature),
                self.item, zoneMode="Manual", setPoint=temperature,
            )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        zone = self.item
        client = self.coordinator.client
        if hvac_mode == HVACMode.AUTO:
            program = zone.get("activeHeatingClimateTimeProgramNumber") or 1
            await self.coordinator.async_command(
                client.zone_schedule(self.item_id, program),
                zone, zoneMode="Scheduling", setPoint=zone.get("currentScheduleSetPoint"),
            )
        elif hvac_mode in (HVACMode.HEAT, HVACMode.COOL):
            setpoint = (
                zone.get("currentScheduleSetPoint")
                if self.hvac_mode == HVACMode.OFF
                else zone.get("setPoint")
            ) or 20.0
            await self.coordinator.async_command(
                client.zone_manual(self.item_id, setpoint),
                zone, zoneMode="Manual", setPoint=setpoint,
            )
        elif hvac_mode == HVACMode.OFF:
            await self.coordinator.async_command(
                client.zone_off(self.item_id), zone, zoneMode="FrostProtection"
            )

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        program = PRESETS[preset_mode]
        zone = self.item
        client = self.coordinator.client
        await self.coordinator.async_command(client.zone_activate_program(self.item_id, program))
        if zone.get("zoneMode") != "Scheduling":
            await self.coordinator.async_command(client.zone_schedule(self.item_id, program))
        zone.update(activeHeatingClimateTimeProgramNumber=program, zoneMode="Scheduling")
        self.coordinator.async_update_listeners()

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.AUTO)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)
