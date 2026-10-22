"""Forward a head's activity log to the Home Assistant event bus."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from pyheatlink import HeatLinkError, HeatLinkScopeError

from .const import (
    DOMAIN,
    EVENT_ACTOR_NAMES,
    EVENT_CATEGORY_NAMES,
    EVENT_HEATLINK,
    EVENTS_INTERVAL,
)

if TYPE_CHECKING:
    from .runtime import HeatLinkRuntime

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
FETCH_LIMIT = 100


class EventPoller:
    """Poll ``/api/events`` and fire one ``heatlink_event`` per new entry.

    The last forwarded ``seq`` is stored, so events logged while Home
    Assistant couldn't reach the head are delivered once it can, and nothing
    is replayed after a Home Assistant restart.
    """

    def __init__(self, runtime: HeatLinkRuntime) -> None:
        self._rt = runtime
        self._store: Store[dict[str, Any]] = Store(
            runtime.hass, STORAGE_VERSION, f"{DOMAIN}.events_{runtime.uid}"
        )
        self._seq: int | None = None
        self._unsub: CALLBACK_TYPE | None = None

    async def async_start(self) -> None:
        stored = await self._store.async_load()
        if stored and isinstance(stored.get("seq"), int):
            self._seq = stored["seq"]
        await self.async_poll()
        self._unsub = async_track_time_interval(
            self._rt.hass,
            self._async_tick,
            EVENTS_INTERVAL,
            name=f"{DOMAIN} events {self._rt.uid}",
            cancel_on_shutdown=True,
        )

    def async_stop(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    async def _async_tick(self, _now: datetime) -> None:
        await self.async_poll()

    async def async_poll(self) -> None:
        try:
            log = await self._rt.client.get_events(since=self._seq or 0, limit=FETCH_LIMIT)
        except HeatLinkScopeError:
            _LOGGER.debug("%s: token can't read the activity log", self._rt.name)
            self.async_stop()
            return
        except HeatLinkError as err:
            _LOGGER.debug("%s: activity log poll failed: %s", self._rt.name, err)
            return
        if self._seq is None or log.seq < self._seq:
            # First run, or the head's log was wiped: start from now rather
            # than replaying history.
            await self._async_save(log.seq)
            return
        new = sorted((e for e in log.events if e.seq > self._seq), key=lambda e: e.seq)
        for event in new:
            self._rt.hass.bus.async_fire(
                EVENT_HEATLINK,
                {
                    "device_id": self._rt.device_id,
                    "uid": self._rt.uid,
                    "name": self._rt.name,
                    "seq": event.seq,
                    "timestamp": event.ts or None,
                    "category": EVENT_CATEGORY_NAMES.get(event.category, str(event.category)),
                    "actor": EVENT_ACTOR_NAMES.get(event.actor, str(event.actor)),
                    "user": event.name or None,
                    "message": event.message,
                },
            )
        latest = max([log.seq, *(e.seq for e in new)])
        if latest != self._seq:
            await self._async_save(latest)

    async def _async_save(self, seq: int) -> None:
        self._seq = seq
        await self._store.async_save({"seq": seq})
