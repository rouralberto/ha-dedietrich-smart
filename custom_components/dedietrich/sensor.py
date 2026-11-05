"""Sensors: heat pump status and energy, thermostat schedule, hot water temperature."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfPressure, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DeDietrichConfigEntry
from .entity import DeDietrichEntity, Kind, dig, has_room_sensor, parse_time

ENERGY_KEYS = ("heating", "hotWater", "cooling")


def _energy(key: str) -> Callable[[dict], float | None]:
    def value(appliance: dict) -> float | None:
        energy = appliance.get("_energy")
        if energy is None:  # not fetched yet
            return None
        return float(energy.get(key) or 0)

    return value


def _cop_today(appliance: dict) -> float | None:
    energy = appliance.get("_energy") or {}
    used = sum(energy.get(f"{k}EnergyConsumed") or 0 for k in ENERGY_KEYS)
    made = sum(energy.get(f"{k}EnergyDelivered") or 0 for k in ENERGY_KEYS)
    return round(made / used, 2) if used else None


def _heat_pump_producer(appliance: dict) -> dict | None:
    producers = dig(appliance.get("_energy"), "producerPerformanceStatistics.producers") or []
    return next(
        (p for p in producers if str(p.get("producerType", "")).startswith("HeatPump")),
        None,
    )


def _scop(appliance: dict) -> float | None:
    return (_heat_pump_producer(appliance) or {}).get("seasonalEfficiency")


@dataclass(frozen=True, kw_only=True)
class DeDietrichSensorDescription(SensorEntityDescription):
    kind: Kind
    value_fn: Callable[[dict], Any]
    exists_fn: Callable[[dict], bool] = lambda item: True


def _has(path: str) -> Callable[[dict], bool]:
    return lambda item: dig(item, path) is not None


def _temp(**kwargs) -> dict:
    return dict(
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        **kwargs,
    )


def _energy_description(key: str, direction: str, **kwargs) -> DeDietrichSensorDescription:
    field = f"{key}Energy{direction}"
    return DeDietrichSensorDescription(
        key=field,
        translation_key=f"{key.lower()}_{direction.lower()}",
        kind="appliance",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=_energy(field),
        exists_fn=lambda a: bool(a.get("capabilityEnergyConsumption")),
        **kwargs,
    )


SENSORS: tuple[DeDietrichSensorDescription, ...] = (
    # --- heat pump --------------------------------------------------------------
    DeDietrichSensorDescription(
        key="outdoor_temperature",
        translation_key="outdoor_temperature",
        kind="appliance",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda a: dig(a, "outdoorTemperatureInformation.applianceOutdoorTemperature"),
        exists_fn=_has("outdoorTemperatureInformation.applianceOutdoorTemperature"),
        **_temp(),
    ),
    DeDietrichSensorDescription(
        key="weather_temperature",
        translation_key="weather_temperature",
        kind="appliance",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda a: dig(a, "outdoorTemperatureInformation.cloudOutdoorTemperature"),
        **_temp(),
    ),
    DeDietrichSensorDescription(
        key="water_pressure",
        translation_key="water_pressure",
        kind="appliance",
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPressure.BAR,
        value_fn=lambda a: a.get("waterPressure"),
        exists_fn=_has("waterPressure"),
    ),
    DeDietrichSensorDescription(
        key="thermal_mode",
        translation_key="thermal_mode",
        kind="appliance",
        value_fn=lambda a: a.get("activeThermalMode"),
    ),
    DeDietrichSensorDescription(
        key="operating_mode",
        translation_key="operating_mode",
        kind="appliance",
        value_fn=lambda a: a.get("operatingMode"),
    ),
    DeDietrichSensorDescription(
        key="status",
        translation_key="status",
        kind="appliance",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda a: a.get("errorStatus"),
    ),
    _energy_description("heating", "Consumed"),
    _energy_description("heating", "Delivered"),
    _energy_description("hotWater", "Consumed"),
    _energy_description("hotWater", "Delivered"),
    DeDietrichSensorDescription(
        key="cop_today",
        translation_key="cop_today",
        kind="appliance",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_cop_today,
        exists_fn=lambda a: bool(a.get("capabilityEnergyConsumption")),
    ),
    DeDietrichSensorDescription(
        key="seasonal_efficiency",
        translation_key="seasonal_efficiency",
        kind="appliance",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_scop,
        exists_fn=lambda a: _heat_pump_producer(a) is not None,
    ),
    # --- thermostat (climate zone) ----------------------------------------------
    DeDietrichSensorDescription(
        key="room_temperature",
        translation_key="room_temperature",
        kind="climate_zone",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda z: z.get("roomTemperature"),
        exists_fn=has_room_sensor,
        **_temp(),
    ),
    DeDietrichSensorDescription(
        key="schedule_setpoint",
        translation_key="schedule_setpoint",
        kind="climate_zone",
        value_fn=lambda z: z.get("currentScheduleSetPoint"),
        **_temp(),
    ),
    DeDietrichSensorDescription(
        key="next_setpoint",
        translation_key="next_setpoint",
        kind="climate_zone",
        value_fn=lambda z: z.get("nextSetpoint"),
        **_temp(),
    ),
    DeDietrichSensorDescription(
        key="next_switch_time",
        translation_key="next_switch_time",
        kind="climate_zone",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda z: parse_time(z.get("nextSwitchTime")),
    ),
    # --- hot water --------------------------------------------------------------
    DeDietrichSensorDescription(
        key="water_temperature",
        translation_key="water_temperature",
        kind="hot_water_zone",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda z: z.get("dhwTemperature"),
        exists_fn=_has("dhwTemperature"),
        **_temp(),
    ),
)

COOLING_SENSORS = (
    _energy_description("cooling", "Consumed"),
    _energy_description("cooling", "Delivered"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DeDietrichConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    data = coordinator.data
    items: dict[Kind, dict[str, dict]] = {
        "appliance": data.appliances,
        "climate_zone": data.climate_zones,
        "hot_water_zone": data.hot_water_zones,
    }
    entities = []
    for description in SENSORS:
        for item_id, item in items[description.kind].items():
            if description.exists_fn(item):
                entities.append(DeDietrichSensor(coordinator, item_id, description))
    for appliance_id, appliance in data.appliances.items():
        if appliance.get("capabilityCooling") and appliance.get("capabilityEnergyConsumption"):
            entities.extend(
                DeDietrichSensor(coordinator, appliance_id, d) for d in COOLING_SENSORS
            )
    async_add_entities(entities)


class DeDietrichSensor(DeDietrichEntity, SensorEntity):
    entity_description: DeDietrichSensorDescription

    def __init__(self, coordinator, item_id: str, description: DeDietrichSensorDescription) -> None:
        self.entity_description = description
        super().__init__(coordinator, description.kind, item_id, description.key)

    def _update_attrs(self) -> None:
        self._attr_native_value = self.entity_description.value_fn(self.item)
