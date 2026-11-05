"""Numbers: hot water comfort/eco temperatures, and each zone's heating curve."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DeDietrichConfigEntry
from .entity import DeDietrichEntity

# key -> (dashboard field, setPointRanges prefix)
DHW_SETPOINTS = {
    "dhw_comfort": ("comfortSetPoint", "comfortSetpoint"),
    "dhw_reduced": ("reducedSetpoint", "reducedSetpoint"),
}

# key -> (heating curve field, suffix of its minimum*/maximum*/increment* fields)
HEATING_CURVE = {
    "heating_curve_slope": ("slope", "Slope"),
    "heating_curve_base": ("baseSetpoint", "BaseSetpoint"),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DeDietrichConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    data = coordinator.data
    entities: list[NumberEntity] = [
        DhwSetpoint(coordinator, "hot_water_zone", zone_id, key)
        for zone_id in data.hot_water_zones
        for key in DHW_SETPOINTS
    ]
    entities += [
        HeatingCurveNumber(coordinator, "climate_zone", zone_id, key)
        for zone_id, zone in data.climate_zones.items()
        if zone.get("_heating_curve")
        for key in HEATING_CURVE
    ]
    async_add_entities(entities)


class DhwSetpoint(DeDietrichEntity, NumberEntity):
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_step = 1.0
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator, kind, item_id: str, key: str) -> None:
        self._field, self._range = DHW_SETPOINTS[key]
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


class HeatingCurveNumber(DeDietrichEntity, NumberEntity):
    """Weather compensation: the colder it is outside, the warmer the water the heat
    pump sends to this circuit. Slope = how much warmer per degree colder; base = the
    flow temperature the curve starts from."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator, kind, item_id: str, key: str) -> None:
        self._field, self._limits = HEATING_CURVE[key]
        self._attr_translation_key = key
        if self._field == "baseSetpoint":
            self._attr_device_class = NumberDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        super().__init__(coordinator, kind, item_id, key)

    @property
    def _curve(self) -> dict:
        return (self.item or {}).get("_heating_curve") or {}

    @property
    def available(self) -> bool:
        return super().available and bool(self._curve)

    def _update_attrs(self) -> None:
        curve = self._curve
        self._attr_native_value = curve.get(self._field)
        self._attr_native_min_value = curve.get(f"minimum{self._limits}", 0.0)
        self._attr_native_max_value = curve.get(f"maximum{self._limits}", 100.0)
        self._attr_native_step = curve.get(f"increment{self._limits}") or 0.1

    async def async_set_native_value(self, value: float) -> None:
        curve = self._curve
        value = round(value, 2)  # the UI's step can produce 1.2000000000000002
        # The API wants both values on every write.
        values = {"slope": curve["slope"], "baseSetpoint": curve["baseSetpoint"]}
        values[self._field] = value
        await self.coordinator.async_command(
            self.coordinator.client.set_heating_curve(
                self.item_id, values["slope"], values["baseSetpoint"]
            ),
            # `curve` is the coordinator's cached copy, so the new value sticks
            # until the curve is read again.
            curve, **{self._field: value},
        )
        self.coordinator.refetch_heating_curve(self.item_id)
