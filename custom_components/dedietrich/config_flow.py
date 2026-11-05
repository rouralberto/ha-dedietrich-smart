"""Config flow: sign in with the De Dietrich Smart app's email and password."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import ApiError, AuthError, async_login
from .const import CONF_TOKEN, DOMAIN

_LOGGER = logging.getLogger(__name__)

EMAIL = TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username"))
PASSWORD = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
)


async def _try_login(email: str, password: str) -> tuple[dict | None, dict[str, str]]:
    try:
        return await async_login(email, password), {}
    except AuthError:
        return None, {"base": "invalid_auth"}
    except ApiError as err:
        _LOGGER.warning("Login failed: %s", err)
        return None, {"base": "cannot_connect"}
    except Exception:
        _LOGGER.exception("Unexpected error during login")
        return None, {"base": "unknown"}


class DeDietrichConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip().lower()
            await self.async_set_unique_id(email)
            self._abort_if_unique_id_configured()
            token, errors = await _try_login(email, user_input[CONF_PASSWORD])
            if token:
                return self.async_create_entry(
                    title=email,
                    data={
                        CONF_EMAIL: email,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_TOKEN: token,
                    },
                )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_EMAIL): EMAIL, vol.Required(CONF_PASSWORD): PASSWORD}
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            token, errors = await _try_login(
                entry.data[CONF_EMAIL], user_input[CONF_PASSWORD]
            )
            if token:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD], CONF_TOKEN: token},
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD}),
            description_placeholders={"email": entry.data[CONF_EMAIL]},
            errors=errors,
        )
