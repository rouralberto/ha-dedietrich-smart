"""Binary sensors: cloud connection, holiday mode, water pressure, heat demand."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DeDietrichConfigEntry
from .entity import DeDietrichEntity, Kind, dig

IDLE = (None, "Idle")


@dataclass(frozen=True, kw_only=True)
class DeDietrichBinaryDescription(BinarySensorEntityDescription):
    kind: Kind
    value_fn: Callable[[dict], bool | None]
    exists_fn: Callable[[dict], bool] = lambda item: True


BINARY_SENSORS = (
    DeDietrichBinaryDescription(
        key="online",
        translation_key="online",
        kind="appliance",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda a: a.get("applianceOnline"),
    ),
    DeDietrichBinaryDescription(
        key="holiday",
        translation_key="holiday",
        kind="appliance",
        value_fn=lambda a: dig(a, "holidaySchedule.active"),
    ),
    DeDietrichBinaryDescription(
        key="water_pressure_problem",
        translation_key="water_pressure_problem",
        kind="appliance",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda a: a.get("waterPressureOK") is False,
        exists_fn=lambda a: a.get("waterPressure") is not None,
    ),
    DeDietrichBinaryDescription(
        key="demand",
        translation_key="demand",
        kind="climate_zone",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda z: z.get("activeComfortDemand") not in IDLE,
    ),
    DeDietrichBinaryDescription(
        key="dhw_heating",
        translation_key="dhw_heating",
        kind="hot_water_zone",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda z: z.get("dhwStatus") == "ProducingHeat",
    ),
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
    async_add_entities(
        DeDietrichBinarySensor(coordinator, item_id, description)
        for description in BINARY_SENSORS
        for item_id, item in items[description.kind].items()
        if description.exists_fn(item)
    )


class DeDietrichBinarySensor(DeDietrichEntity, BinarySensorEntity):
    entity_description: DeDietrichBinaryDescription

    def __init__(self, coordinator, item_id: str, description: DeDietrichBinaryDescription) -> None:
        self.entity_description = description
        super().__init__(coordinator, description.kind, item_id, description.key)

    @property
    def available(self) -> bool:
        if self.entity_description.key == "online":
            # Must stay available to report that the appliance is offline.
            return self.coordinator.last_update_success and self.item is not None
        return super().available

    def _update_attrs(self) -> None:
        self._attr_is_on = self.entity_description.value_fn(self.item)
