"""Tests for the De Dietrich Smart integration, against a fake BDR Thermea cloud."""

from __future__ import annotations

import copy
from datetime import timedelta
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMockResponse,
)

from custom_components.dedietrich.api import ApiError, AuthError, DeDietrichClient
from custom_components.dedietrich.const import API_BASE, B2C_BASE, DOMAIN

DASHBOARD = json.loads(
    (Path(__file__).parent / "fixtures" / "dashboard.json").read_text()
)
APPLIANCE = "a0000000-0000-0000-0000-000000000001"
ZONE = "c0000000-0000-0000-0000-000000000001"
CIRCUIT = "c0000000-0000-0000-0000-000000000002"  # no room unit
DHW = "d0000000-0000-0000-0000-000000000001"
TECHNICAL = {
    "applianceName": "IDU2WH",
    "internetConnectedGateways": [
        {"deviceTypeName": "RU2", "name": "RU2", "hardwareVersion": "9",
         "softwareVersion": "2.28.6", "serialNumber": "SN123", "controlledZoneName": None}
    ],
}
ENERGY = {
    "data": [
        {
            "heatingEnergyConsumed": 5.0,
            "heatingEnergyDelivered": 23.0,
            "hotWaterEnergyConsumed": 1.0,
            "hotWaterEnergyDelivered": 3.0,
            "coolingEnergyConsumed": 0,
            "coolingEnergyDelivered": 0,
            "producerPerformanceStatistics": {
                "producers": [
                    {"producerType": "HeatPumpAirSource", "seasonalEfficiency": 3.9}
                ]
            },
        }
    ]
}
TOKEN = {"access_token": "a", "refresh_token": "r", "expires_at": 9e12}
HEATING_CURVE = {
    "slope": 1.5, "minimumSlope": 0.0, "maximumSlope": 4.0, "incrementSlope": 0.1,
    "baseSetpoint": 15.0, "minimumBaseSetpoint": 15.0, "maximumBaseSetpoint": 35.0,
    "incrementBaseSetpoint": 0.5, "maxFlowTemperature": 35.0,
}


class FakeCloud:
    """Answers GETs from fixtures and records every command."""

    def __init__(self) -> None:
        self.dashboard = copy.deepcopy(DASHBOARD)
        self.calls: list[tuple] = []
        self.fail: Exception | None = None
        self.curves = {z["climateZoneId"]: dict(HEATING_CURVE)
                       for z in self.dashboard["appliances"][0]["climateZones"]}
        self.curve_reads = 0

    async def request(self, method, path, *, json_body=None, params=None):
        if self.fail:
            raise self.fail
        if path.endswith("/heating-curve"):
            zone_id = path.split("/")[2]
            if zone_id not in self.curves:
                raise ApiError(f"{method} {path}: HTTP 404", 404)
            if method == "GET":
                self.curve_reads += 1
                return dict(self.curves[zone_id])
            self.curves[zone_id].update(json_body)  # the real cloud applies it at once
        if method == "GET":
            if path == "/homes/dashboard":
                return copy.deepcopy(self.dashboard)
            if path.endswith("/technicaldetails"):
                return TECHNICAL
            if "/energyconsumption/daily" in path:
                return ENERGY
            raise ApiError(f"unexpected GET {path}")
        self.calls.append((method, path, json_body))
        return None


@pytest.fixture
def cloud():
    fake = FakeCloud()

    async def request(self, method, path, **kwargs):
        return await fake.request(method, path, **kwargs)

    with patch.object(DeDietrichClient, "request", request):
        yield fake


async def setup_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="me@example.com",
        data={CONF_EMAIL: "me@example.com", CONF_PASSWORD: "pw", "token": TOKEN},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.fixture
async def entry(hass: HomeAssistant, cloud: FakeCloud) -> MockConfigEntry:
    return await setup_entry(hass)


async def later(hass: HomeAssistant, seconds: int) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


def eid(hass: HomeAssistant, platform: str, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)
    assert entity_id, f"no {platform} entity {unique_id}"
    return entity_id


def state(hass: HomeAssistant, platform: str, unique_id: str):
    return hass.states.get(eid(hass, platform, unique_id))


async def call(hass: HomeAssistant, domain: str, service: str, entity_id: str, **data):
    await hass.services.async_call(
        domain, service, {"entity_id": entity_id, **data}, blocking=True
    )


# --- entities --------------------------------------------------------------------


async def test_entities(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert entry.state is ConfigEntryState.LOADED

    climate = state(hass, "climate", ZONE)
    assert climate.entity_id == "climate.salon"
    assert climate.state == "auto"
    assert climate.attributes["current_temperature"] == 19.8
    assert climate.attributes["temperature"] == 20.5
    assert climate.attributes["hvac_action"] == "heating"
    assert climate.attributes["preset_mode"] == "schedule_1"
    assert climate.attributes["min_temp"] == 5.0

    water = state(hass, "water_heater", DHW)
    assert water.entity_id == "water_heater.hot_water"
    assert water.state == "schedule"
    assert water.attributes["current_temperature"] == 47.5
    assert water.attributes["temperature"] == 55.0
    assert water.attributes["operation_list"] == ["schedule", "comfort", "eco", "boost"]

    assert state(hass, "sensor", f"{APPLIANCE}_outdoor_temperature").state == "9.5"
    assert state(hass, "sensor", f"{APPLIANCE}_water_pressure").state == "1.7"
    assert state(hass, "sensor", f"{APPLIANCE}_heatingEnergyConsumed").state == "5.0"
    assert state(hass, "sensor", f"{APPLIANCE}_heatingEnergyDelivered").state == "23.0"
    assert state(hass, "sensor", f"{APPLIANCE}_cop_today").state == "4.33"
    assert state(hass, "sensor", f"{APPLIANCE}_seasonal_efficiency").state == "3.9"
    assert state(hass, "sensor", f"{ZONE}_room_temperature").state == "19.8"
    assert state(hass, "sensor", f"{ZONE}_next_switch_time").state == "2026-11-05T22:00:00+00:00"
    assert state(hass, "sensor", f"{DHW}_water_temperature").state == "47.5"
    assert state(hass, "binary_sensor", f"{APPLIANCE}_online").state == "on"
    assert state(hass, "binary_sensor", f"{APPLIANCE}_holiday").state == "off"
    assert state(hass, "binary_sensor", f"{APPLIANCE}_water_pressure_problem").state == "off"
    assert state(hass, "binary_sensor", f"{ZONE}_demand").state == "on"
    assert state(hass, "binary_sensor", f"{DHW}_dhw_heating").state == "on"
    assert state(hass, "switch", f"{ZONE}_fireplace").state == "off"
    assert state(hass, "number", f"{DHW}_dhw_comfort").state == "55.0"
    assert state(hass, "number", f"{DHW}_dhw_reduced").state == "40.0"

    # The appliance can cool -> cooling energy sensors.
    assert state(hass, "sensor", f"{APPLIANCE}_coolingEnergyConsumed").state == "0.0"

    devices = dr.async_get(hass)
    thermostat = devices.async_get_device_by_identifier((DOMAIN, ZONE), entry.entry_id)
    assert (thermostat.model, thermostat.sw_version) == ("RU2", "2.28.6")
    assert devices.async_get(thermostat.via_device_id).name == "IDU2WH"


async def test_circuit_without_room_unit(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """A heating circuit without its own thermostat: controllable, but no room readings."""
    climate = state(hass, "climate", CIRCUIT)
    assert climate.state == "off"
    assert climate.attributes["current_temperature"] is None
    assert state(hass, "sensor", f"{CIRCUIT}_next_setpoint").state == "17.0"

    registry = er.async_get(hass)
    assert not registry.async_get_entity_id("sensor", DOMAIN, f"{CIRCUIT}_room_temperature")
    assert not registry.async_get_entity_id("switch", DOMAIN, f"{CIRCUIT}_fireplace")

    circuit = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, CIRCUIT), entry.entry_id)
    assert (circuit.model, circuit.sw_version, circuit.serial_number) == (
        "Heating circuit", None, None)


async def test_circuit_renamed_in_app(hass: HomeAssistant, entry, cloud: FakeCloud) -> None:
    """A rename in the app reaches the device and its entities without a reload, but
    doesn't override a name given in Home Assistant."""
    devices = dr.async_get(hass)
    room = devices.async_get_device_by_identifier((DOMAIN, ZONE), entry.entry_id)
    circuit = devices.async_get_device_by_identifier((DOMAIN, CIRCUIT), entry.entry_id)
    devices.async_update_device(circuit.id, name_by_user="Basement")

    zones = cloud.dashboard["appliances"][0]["climateZones"]
    zones[0]["name"] = "Suelo Radiante"
    zones[1]["name"] = "CIRCA 1"
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert devices.async_get(room.id).name == "Suelo Radiante"
    assert state(hass, "climate", ZONE).attributes["friendly_name"] == "Suelo Radiante"
    assert (
        state(hass, "sensor", f"{ZONE}_room_temperature").attributes["friendly_name"]
        == "Suelo Radiante Room temperature"
    )
    assert eid(hass, "climate", ZONE) == "climate.salon"  # entity ids are kept
    circuit = devices.async_get(circuit.id)
    assert (circuit.name, circuit.name_by_user) == ("CIRCA 1", "Basement")
    assert state(hass, "climate", CIRCUIT).attributes["friendly_name"] == "Basement"


async def test_offline_appliance(hass: HomeAssistant, entry, cloud: FakeCloud) -> None:
    cloud.dashboard["appliances"][0]["applianceOnline"] = False
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert state(hass, "climate", ZONE).state == "unavailable"
    assert state(hass, "binary_sensor", f"{APPLIANCE}_online").state == "off"


async def test_short_cloud_outage_keeps_last_data(
    hass: HomeAssistant, entry, cloud: FakeCloud, freezer
) -> None:
    async def poll_after(minutes: float) -> None:
        freezer.tick(timedelta(minutes=minutes))
        await later(hass, 0)

    room = eid(hass, "climate", ZONE)
    cloud.fail = ApiError("GET /homes/dashboard: TimeoutError()")
    # Up to 5 minutes without an answer: the last data stays.
    for _ in range(4):
        await poll_after(1)
        assert hass.states.get(room).state == "auto"
    # A good poll starts the grace period over.
    cloud.fail = None
    await poll_after(1)
    cloud.fail = ApiError("GET /homes/dashboard: TimeoutError()")
    await poll_after(4)
    assert hass.states.get(room).state == "auto"
    # Older than 5 minutes: unavailable, until the cloud answers again.
    await poll_after(1.5)
    assert hass.states.get(room).state == "unavailable"
    cloud.fail = None
    await poll_after(1)
    assert hass.states.get(room).state == "auto"


# --- commands ----------------------------------------------------------------------


async def test_climate_commands(hass: HomeAssistant, entry, cloud: FakeCloud) -> None:
    climate = eid(hass, "climate", ZONE)
    base = f"/climate-zones/{ZONE}"

    # AUTO: a new temperature overrides the schedule until its next switch.
    await call(hass, "climate", "set_temperature", climate, temperature=21.5)
    assert cloud.calls[-1] == ("POST", f"{base}/modes/temporary-override",
                               {"roomTemperatureSetPoint": 21.5})
    assert hass.states.get(climate).attributes["temperature"] == 21.5

    await call(hass, "climate", "set_hvac_mode", climate, hvac_mode="heat")
    assert cloud.calls[-1] == ("POST", f"{base}/modes/manual", {"roomTemperatureSetPoint": 21.5})
    assert hass.states.get(climate).state == "heat"

    await call(hass, "climate", "set_temperature", climate, temperature=22)
    assert cloud.calls[-1] == ("POST", f"{base}/modes/manual", {"roomTemperatureSetPoint": 22})

    await call(hass, "climate", "set_preset_mode", climate, preset_mode="schedule_2")
    assert cloud.calls[-2:] == [
        ("POST", f"{base}/time-programs/heating/2/activate", None),
        ("POST", f"{base}/modes/schedule", {"heatingProgramId": 2}),
    ]
    assert hass.states.get(climate).attributes["preset_mode"] == "schedule_2"

    await call(hass, "climate", "turn_off", climate)
    assert cloud.calls[-1] == ("POST", f"{base}/modes/anti-frost", None)
    assert hass.states.get(climate).state == "off"

    await call(hass, "climate", "set_hvac_mode", climate, hvac_mode="auto")
    assert cloud.calls[-1] == ("POST", f"{base}/modes/schedule", {"heatingProgramId": 2})

    # A few seconds later the dashboard is polled again and wins over the optimistic
    # state (the fake cloud ignores commands, so it is back to its fixture values).
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=10))
    await hass.async_block_till_done()
    assert hass.states.get(climate).attributes["preset_mode"] == "schedule_1"


async def test_hot_water_commands(hass: HomeAssistant, entry, cloud: FakeCloud) -> None:
    water = eid(hass, "water_heater", DHW)
    base = f"/hot-water-zones/{DHW}"

    await call(hass, "water_heater", "set_operation_mode", water, operation_mode="boost")
    assert cloud.calls[-1] == ("POST", f"{base}/modes/boost", None)

    await call(hass, "water_heater", "set_operation_mode", water, operation_mode="eco")
    assert cloud.calls[-1] == ("POST", f"{base}/modes/anti-frost", None)
    assert hass.states.get(water).attributes["temperature"] == 40.0

    # In eco the target is the reduced setpoint.
    await call(hass, "water_heater", "set_temperature", water, temperature=35)
    assert cloud.calls[-1] == ("POST", f"{base}/reduced-setpoint", {"reducedSetpoint": 35})

    await call(hass, "water_heater", "set_operation_mode", water, operation_mode="comfort")
    await call(hass, "water_heater", "set_temperature", water, temperature=52)
    assert cloud.calls[-1] == ("POST", f"{base}/comfort-setpoint", {"comfortSetpoint": 52})

    # Boost is only accepted from schedule mode.
    await call(hass, "water_heater", "set_operation_mode", water, operation_mode="boost")
    assert cloud.calls[-2:] == [
        ("POST", f"{base}/modes/schedule", None),
        ("POST", f"{base}/modes/boost", None),
    ]

    await call(hass, "number", "set_value", eid(hass, "number", f"{DHW}_dhw_comfort"), value=50)
    assert cloud.calls[-1] == ("POST", f"{base}/comfort-setpoint", {"comfortSetpoint": 50.0})

    await call(hass, "switch", "turn_on", eid(hass, "switch", f"{ZONE}_fireplace"))
    assert cloud.calls[-1] == ("POST", f"/climate-zones/{ZONE}/modes/fireplacemode",
                               {"fireplaceModeActive": True})

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=10))
    await hass.async_block_till_done()


async def test_heating_curve(hass: HomeAssistant, entry, cloud: FakeCloud) -> None:
    slope = eid(hass, "number", f"{ZONE}_heating_curve_slope")
    base = eid(hass, "number", f"{ZONE}_heating_curve_base")
    attrs = hass.states.get(slope).attributes
    assert hass.states.get(slope).state == "1.5"
    assert (attrs["min"], attrs["max"], attrs["step"]) == (0.0, 4.0, 0.1)
    assert hass.states.get(base).state == "15.0"
    assert hass.states.get(base).attributes["unit_of_measurement"] == "°C"
    assert state(hass, "sensor", f"{ZONE}_max_flow_temperature").state == "35.0"
    # Every circuit has its own curve, including the one without a thermostat.
    assert state(hass, "number", f"{CIRCUIT}_heating_curve_slope").state == "1.5"

    # Both values go into every write; the one not being changed keeps its value.
    await call(hass, "number", "set_value", slope, value=1.2000000000000002)
    assert cloud.calls[-1] == ("POST", f"/climate-zones/{ZONE}/heating-curve",
                               {"slope": 1.2, "baseSetpoint": 15.0})
    assert hass.states.get(slope).state == "1.2"
    await call(hass, "number", "set_value", base, value=20)
    assert cloud.calls[-1] == ("POST", f"/climate-zones/{ZONE}/heating-curve",
                               {"slope": 1.2, "baseSetpoint": 20.0})

    # After a change the curve is read again on the next poll, which confirms it.
    reads = cloud.curve_reads
    await later(hass, 10)
    assert cloud.curve_reads == reads + 1
    assert (hass.states.get(slope).state, hass.states.get(base).state) == ("1.2", "20.0")
    # Otherwise it's only read every 15 minutes, not on every one-minute poll.
    await later(hass, 70)
    assert cloud.curve_reads == reads + 1


async def test_zone_without_heating_curve(hass: HomeAssistant, cloud: FakeCloud, freezer) -> None:
    del cloud.curves[CIRCUIT]
    await setup_entry(hass)
    registry = er.async_get(hass)
    assert not registry.async_get_entity_id("number", DOMAIN, f"{CIRCUIT}_heating_curve_slope")
    assert not registry.async_get_entity_id("sensor", DOMAIN, f"{CIRCUIT}_max_flow_temperature")
    assert registry.async_get_entity_id("number", DOMAIN, f"{ZONE}_heating_curve_slope")
    # A 404 means "no heating curve here": not asked again on later polls.
    reads = cloud.curve_reads
    freezer.tick(timedelta(minutes=16))
    await later(hass, 0)
    assert cloud.curve_reads == reads + 1  # only ZONE's 15-minute refresh


async def test_heating_cooling(hass: HomeAssistant, entry, cloud: FakeCloud) -> None:
    select = eid(hass, "select", f"{APPLIANCE}_heating_cooling")
    climate = eid(hass, "climate", ZONE)
    assert hass.states.get(select).state == "auto"
    assert hass.states.get(select).attributes["options"] == ["auto", "heating", "cooling"]
    assert hass.states.get(climate).attributes["hvac_modes"] == ["auto", "heat", "off"]

    await call(hass, "select", "select_option", select, option="cooling")
    assert cloud.calls[-1] == ("POST", f"/appliances/{APPLIANCE}/operatingmode",
                               {"operatingMode": "ForcedCooling"})
    assert hass.states.get(select).state == "cooling"
    # While cooling, the fixed-temperature mode is Cool instead of Heat.
    assert hass.states.get(climate).attributes["hvac_modes"] == ["auto", "cool", "off"]
    await call(hass, "climate", "set_hvac_mode", climate, hvac_mode="cool")
    assert cloud.calls[-1] == ("POST", f"/climate-zones/{ZONE}/modes/manual",
                               {"roomTemperatureSetPoint": 20.5})
    assert hass.states.get(climate).state == "cool"

    await call(hass, "select", "select_option", select, option="heating")
    assert cloud.calls[-1] == ("POST", f"/appliances/{APPLIANCE}/operatingmode",
                               {"operatingMode": "AutomaticHeating"})
    assert hass.states.get(climate).state == "heat"
    await later(hass, 10)


async def test_heat_only_system_has_no_cooling_switch(hass: HomeAssistant, cloud: FakeCloud) -> None:
    cloud.dashboard["appliances"][0].update(capabilityCooling=False, operatingMode="AutomaticHeating")
    await setup_entry(hass)
    assert not er.async_get(hass).async_get_entity_id(
        "select", DOMAIN, f"{APPLIANCE}_heating_cooling")
    assert state(hass, "climate", ZONE).attributes["hvac_modes"] == ["auto", "heat", "off"]


async def test_rejected_command(hass: HomeAssistant, entry, cloud: FakeCloud) -> None:
    cloud.fail = ApiError("HTTP 400")
    with pytest.raises(HomeAssistantError):
        await call(hass, "climate", "turn_off", eid(hass, "climate", ZONE))
    assert state(hass, "climate", ZONE).state == "auto"


async def test_auth_failure_starts_reauth(hass: HomeAssistant, cloud: FakeCloud) -> None:
    cloud.fail = AuthError("refresh and login rejected")
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="me@example.com",
        data={CONF_EMAIL: "me@example.com", CONF_PASSWORD: "old", "token": TOKEN},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == ["reauth"]


# --- config flow -----------------------------------------------------------------


async def test_config_flow(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM

    with patch("custom_components.dedietrich.config_flow.async_login",
               AsyncMock(side_effect=AuthError("no"))):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EMAIL: "me@example.com", CONF_PASSWORD: "bad"})
    assert result["errors"] == {"base": "invalid_auth"}

    with patch("custom_components.dedietrich.config_flow.async_login",
               AsyncMock(side_effect=ApiError("down"))):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EMAIL: "me@example.com", CONF_PASSWORD: "pw"})
    assert result["errors"] == {"base": "cannot_connect"}

    with (
        patch("custom_components.dedietrich.config_flow.async_login",
              AsyncMock(return_value=TOKEN)) as login,
        patch("custom_components.dedietrich.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EMAIL: " Me@Example.com ", CONF_PASSWORD: "pw"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_EMAIL: "me@example.com", CONF_PASSWORD: "pw", "token": TOKEN}
    login.assert_awaited_once_with("me@example.com", "pw")


async def test_reauth_flow(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    new_token = {**TOKEN, "access_token": "b"}
    with patch("custom_components.dedietrich.config_flow.async_login",
               AsyncMock(return_value=new_token)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "new"})
        await hass.async_block_till_done()
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new"
    assert entry.data["token"] == new_token


# --- token handling --------------------------------------------------------------


def _client(hass, token, saved):
    return DeDietrichClient(async_get_clientsession(hass), "me@example.com", "pw", token, saved.append)


async def test_expired_token_is_refreshed(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post(f"{B2C_BASE}/oauth2/v2.0/token",
                        json={"access_token": "new", "refresh_token": "r2", "expires_in": 3600})
    saved: list[dict] = []
    client = _client(hass, {"access_token": "old", "refresh_token": "r1", "expires_at": 0}, saved)
    assert await client._access_token() == "new"
    assert saved[0]["refresh_token"] == "r2"
    assert saved[0]["expires_at"] > dt_util.utcnow().timestamp()


async def test_rejected_refresh_logs_in_again(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post(f"{B2C_BASE}/oauth2/v2.0/token", status=400, text="invalid_grant")
    saved: list[dict] = []
    client = _client(hass, {"access_token": "old", "refresh_token": "r1", "expires_at": 0}, saved)
    with patch("custom_components.dedietrich.api.async_login",
               AsyncMock(return_value={"access_token": "fresh", "expires_at": 9e12})) as login:
        assert await client._access_token() == "fresh"
    login.assert_awaited_once_with("me@example.com", "pw")
    assert saved == [{"access_token": "fresh", "expires_at": 9e12}]


async def test_401_renews_token_and_retries(hass: HomeAssistant, aioclient_mock) -> None:
    seen: list[str] = []

    async def dashboard(method, url, data):
        seen.append("x")  # one entry per API call
        return AiohttpClientMockResponse(method, url, status=401 if len(seen) == 1 else 200,
                                         json={"appliances": []})

    aioclient_mock.get(f"{API_BASE}/homes/dashboard", side_effect=dashboard)
    aioclient_mock.post(f"{B2C_BASE}/oauth2/v2.0/token",
                        json={"access_token": "new", "refresh_token": "r2", "expires_in": 3600})
    saved: list[dict] = []
    client = _client(hass, {"access_token": "old", "refresh_token": "r1", "expires_at": 9e12}, saved)
    assert await client.dashboard() == {"appliances": []}
    assert len(seen) == 2 and saved[0]["access_token"] == "new"
