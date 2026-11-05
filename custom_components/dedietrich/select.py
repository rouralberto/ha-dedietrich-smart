"""Heating/cooling mode of a heat pump that can cool. It applies to the whole system."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DeDietrichConfigEntry
from .entity import DeDietrichEntity

# option -> the API's operatingMode. The API also accepts "Off" (the whole heat pump
# off); it's left out on purpose: each zone can already be turned off on its own.
OPTIONS = {
    "auto": "AutomaticCoolingHeating",  # heats or cools as the season requires
    "heating": "AutomaticHeating",
    "cooling": "ForcedCooling",
}
MODE_TO_OPTION = {mode: option for option, mode in OPTIONS.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DeDietrichConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        HeatingCoolingMode(coordinator, "appliance", appliance_id, "heating_cooling")
        for appliance_id, appliance in coordinator.data.appliances.items()
        if appliance.get("capabilityCooling")
    )


class HeatingCoolingMode(DeDietrichEntity, SelectEntity):
    _attr_translation_key = "heating_cooling"
    _attr_options = list(OPTIONS)

    def _update_attrs(self) -> None:
        self._attr_current_option = MODE_TO_OPTION.get(self.item.get("operatingMode"))

    async def async_select_option(self, option: str) -> None:
        mode = OPTIONS[option]
        await self.coordinator.async_command(
            self.coordinator.client.set_operating_mode(self.item_id, mode),
            # Also updates the climate entities, which show Heat or Cool by this mode.
            self.item, operatingMode=mode,
        )
