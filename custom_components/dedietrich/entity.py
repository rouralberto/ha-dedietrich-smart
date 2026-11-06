"""Base entity: one device per appliance (heat pump), climate zone and hot water zone."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, MANUFACTURER
from .coordinator import DeDietrichCoordinator

Kind = Literal["appliance", "climate_zone", "hot_water_zone"]

# .NET's DateTime.MinValue, the API's way of saying "no time".
_NO_TIME_YEAR = 1


def parse_time(value: str | None) -> datetime | None:
    parsed = dt_util.parse_datetime(value) if value else None
    if parsed is None or parsed.year == _NO_TIME_YEAR:
        return None
    return parsed


def dig(data: dict | None, path: str):
    """Look up a dotted path such as "outdoorTemperatureInformation.cloudOutdoorTemperature"."""
    for key in path.split("."):
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def appliance_device_info(coordinator: DeDietrichCoordinator, appliance_id: str) -> DeviceInfo:
    appliance = coordinator.data.appliances[appliance_id]
    technical = appliance.get("_technical") or {}
    return DeviceInfo(
        identifiers={(DOMAIN, appliance_id)},
        manufacturer=MANUFACTURER,
        name=technical.get("applianceName") or appliance.get("applianceType") or "Heat pump",
        model=technical.get("applianceName"),
    )


def has_room_sensor(zone: dict) -> bool:
    """Whether a thermostat measures this zone (circuits without one report null)."""
    return zone.get("roomTemperature") is not None


def _room_unit(data, zone: dict) -> dict | None:
    """The internet gateway (the Smart TC, reported as e.g. "RU2") if it is this zone's
    thermostat, else None. One gateway serves the whole system but sits in one zone."""
    gateways = (data.appliances[zone["applianceId"]].get("_technical") or {}).get(
        "internetConnectedGateways"
    ) or []
    named = [g for g in gateways if g.get("controlledZoneName")]
    if named:
        return next((g for g in named if g["controlledZoneName"] == zone.get("name")), None)
    if not has_room_sensor(zone):
        return None
    return gateways[0] if gateways else {}


def _device_info(coordinator: DeDietrichCoordinator, kind: Kind, item_id: str) -> DeviceInfo:
    data = coordinator.data
    if kind == "appliance":
        return appliance_device_info(coordinator, item_id)

    if kind == "climate_zone":
        zone = data.climate_zones[item_id]
        gateway = _room_unit(data, zone)
        if gateway is None:
            # A heating circuit run on the heating curve alone, without a thermostat.
            return DeviceInfo(
                identifiers={(DOMAIN, item_id)},
                manufacturer=MANUFACTURER,
                name=zone.get("name") or "Heating circuit",
                model="Heating circuit",
                hw_version=None,
                sw_version=None,
                serial_number=None,
                via_device_id=coordinator.appliance_device_ids.get(zone["applianceId"]),
            )
        return DeviceInfo(
            identifiers={(DOMAIN, item_id)},
            manufacturer=MANUFACTURER,
            name=zone.get("name") or "Thermostat",
            model=gateway.get("name") or "Smart TC",
            hw_version=gateway.get("hardwareVersion"),
            sw_version=gateway.get("softwareVersion"),
            serial_number=gateway.get("serialNumber"),
            via_device_id=coordinator.appliance_device_ids.get(zone["applianceId"]),
        )

    zone = data.hot_water_zones[item_id]
    return DeviceInfo(
        identifiers={(DOMAIN, item_id)},
        manufacturer=MANUFACTURER,
        name="Hot water" if zone.get("name") in (None, "", "DHW") else zone["name"],
        model="Domestic hot water",
        via_device_id=coordinator.appliance_device_ids.get(zone["applianceId"]),
    )


def device_names(coordinator: DeDietrichCoordinator) -> dict[str, str]:
    """Each device's name as the cloud reports it now, keyed by its identifier."""
    data = coordinator.data
    items: list[tuple[Kind, dict]] = [
        ("appliance", data.appliances),
        ("climate_zone", data.climate_zones),
        ("hot_water_zone", data.hot_water_zones),
    ]
    return {
        item_id: _device_info(coordinator, kind, item_id)["name"]
        for kind, source in items
        for item_id in source
    }


class DeDietrichEntity(CoordinatorEntity[DeDietrichCoordinator]):
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: DeDietrichCoordinator, kind: Kind, item_id: str, key: str | None
    ) -> None:
        super().__init__(coordinator)
        self.kind = kind
        self.item_id = item_id
        self._attr_unique_id = f"{item_id}_{key}" if key else item_id
        self._attr_device_info = _device_info(coordinator, kind, item_id)
        self._update_attrs()

    @property
    def item(self) -> dict | None:
        data = self.coordinator.data
        source = {
            "appliance": data.appliances,
            "climate_zone": data.climate_zones,
            "hot_water_zone": data.hot_water_zones,
        }[self.kind]
        return source.get(self.item_id)

    @property
    def appliance(self) -> dict:
        item = self.item or {}
        if self.kind == "appliance":
            return item
        return self.coordinator.data.appliances.get(item.get("applianceId"), {})

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.item is not None
            and self.appliance.get("applianceOnline", True) is not False
        )

    def _update_attrs(self) -> None:
        """Copy values from the coordinator data into _attr_* members."""

    def _handle_coordinator_update(self) -> None:
        if self.item is not None:
            self._update_attrs()
        super()._handle_coordinator_update()
