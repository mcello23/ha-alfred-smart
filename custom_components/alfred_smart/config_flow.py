"""Config flow for Alfred Smart."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    AlfredAuthError,
    AlfredConnectionError,
    AlfredRequestError,
    AlfredSmartClient,
    AlfredSmartError,
)
from .const import (
    CONF_ASSET_ID,
    CONF_ASSET_NAME,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .coordinator import AlfredConfigEntry
from .models import Asset, parse_devices

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
        ),
    }
)


class AlfredSmartConfigFlow(ConfigFlow, domain=DOMAIN):
    """Email and password, then the home to control."""

    VERSION = 1

    def __init__(self) -> None:
        """Start a flow."""
        self._credentials: dict[str, str] = {}
        self._client: AlfredSmartClient | None = None
        self._assets: list[Asset] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Log in and look for the homes of the account."""
        errors: dict[str, str] = {}
        if user_input is not None:
            client = AlfredSmartClient(
                async_get_clientsession(self.hass),
                user_input[CONF_EMAIL].strip(),
                user_input[CONF_PASSWORD],
            )
            try:
                await client.login()
                assets = await client.discover_assets()
            except AlfredAuthError:
                errors["base"] = "invalid_auth"
            except AlfredSmartError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error logging in to Alfred Smart")
                errors["base"] = "unknown"
            else:
                self._client = client
                self._credentials = {
                    CONF_EMAIL: user_input[CONF_EMAIL].strip(),
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                }
                self._assets = assets
                if len(assets) == 1:
                    return await self._async_finish(assets[0])
                if len(assets) > 1:
                    return await self.async_step_asset()
                return await self.async_step_asset_manual()

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_asset(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """More than one home: pick one (add the flow again for the others)."""
        if user_input is not None:
            asset = next(a for a in self._assets if a.id == user_input[CONF_ASSET_ID])
            return await self._async_finish(asset)

        options = [SelectOptionDict(value=a.id, label=f"{a.name} ({a.id})") for a in self._assets]
        return self.async_show_form(
            step_id="asset",
            data_schema=vol.Schema(
                {vol.Required(CONF_ASSET_ID): SelectSelector(SelectSelectorConfig(options=options))}
            ),
        )

    async def async_step_asset_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """No listing found: ask for the home code shown by the web app."""
        errors: dict[str, str] = {}
        if user_input is not None:
            asset_id = user_input[CONF_ASSET_ID].strip()
            assert self._client is not None
            try:
                body = await self._client.get_devices_raw(asset_id)
            except AlfredAuthError:
                errors["base"] = "invalid_auth"
            except AlfredConnectionError:
                errors["base"] = "cannot_connect"
            except AlfredRequestError:
                errors[CONF_ASSET_ID] = "invalid_asset"
            else:
                if isinstance(body, dict) and isinstance(body.get("data"), list):
                    found = len(parse_devices(body))
                    _LOGGER.debug("Asset %s lists %s openable devices", asset_id, found)
                    return await self._async_finish(Asset(id=asset_id, name=asset_id))
                errors[CONF_ASSET_ID] = "invalid_asset"

        return self.async_show_form(
            step_id="asset_manual",
            data_schema=vol.Schema({vol.Required(CONF_ASSET_ID): TextSelector()}),
            errors=errors,
        )

    async def _async_finish(self, asset: Asset) -> ConfigFlowResult:
        await self.async_set_unique_id(asset.id)
        self._abort_if_unique_id_configured()
        title = asset.name if asset.name != asset.id else f"Alfred Smart {asset.id}"
        return self.async_create_entry(
            title=title,
            data={
                **self._credentials,
                CONF_ASSET_ID: asset.id,
                CONF_ASSET_NAME: title,
            },
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The password changed or the account was locked."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the password again."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            client = AlfredSmartClient(
                async_get_clientsession(self.hass),
                entry.data[CONF_EMAIL],
                user_input[CONF_PASSWORD],
            )
            try:
                await client.login()
            except AlfredAuthError:
                errors["base"] = "invalid_auth"
            except AlfredSmartError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
            description_placeholders={"email": entry.data[CONF_EMAIL]},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: AlfredConfigEntry) -> OptionsFlow:
        """Polling interval."""
        return AlfredSmartOptionsFlow()


class AlfredSmartOptionsFlow(OptionsFlow):
    """How often to read the device list."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Single step."""
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL])}
            )
        current = self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL,
                            max=MAX_SCAN_INTERVAL,
                            step=1,
                            unit_of_measurement="min",
                            mode=NumberSelectorMode.BOX,
                        )
                    )
                }
            ),
        )
