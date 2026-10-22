"""Polling coordinators for one HeatLink head.

Each API resource has its own coordinator so a slow or failing diagnostics
poll never makes the climate entity unavailable.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pyheatlink import HeatLinkAuthError, HeatLinkError, HeatLinkScopeError

from .const import FAILURE_THRESHOLD

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)


class HeatLinkCoordinator[T](DataUpdateCoordinator[T]):
    """Poll one endpoint, tolerating a few transient failures."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        name: str,
        interval: timedelta | None,
        fetch: Callable[[], Awaitable[T]],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{entry.title} {name}",
            update_interval=interval,
        )
        self._fetch = fetch
        self._failures = 0

    async def _async_update_data(self) -> T:
        try:
            data = await self._fetch()
        except HeatLinkAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except HeatLinkScopeError as err:
            # A read-only token can't reach admin endpoints; that is not a
            # reason to ask for a new token.
            raise UpdateFailed(f"Token scope does not allow this: {err}") from err
        except HeatLinkError as err:
            self._failures += 1
            if self.data is not None and self._failures < FAILURE_THRESHOLD:
                _LOGGER.debug("%s: transient failure %d: %s", self.name, self._failures, err)
                return self.data
            raise UpdateFailed(str(err) or type(err).__name__) from err
        self._failures = 0
        return data
