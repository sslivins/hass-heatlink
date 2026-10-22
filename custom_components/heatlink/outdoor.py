"""Outdoor-unit bookkeeping shared by every HeatLink config entry.

Heads on one outdoor unit share a stable group hash (``ghash``). Entities for
the outdoor unit itself (Tandem, shared off-at-setpoint settings) are created
once, by whichever loaded head claimed the unit first, and read from any
reachable member so they survive that head going offline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import DOMAIN

if TYPE_CHECKING:
    from .runtime import HeatLinkRuntime


def outdoor_signal(ghash: str) -> str:
    return f"{DOMAIN}_outdoor_{ghash}"


class OutdoorUnits:
    """Track which loaded heads belong to which outdoor unit."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._members: dict[str, dict[str, HeatLinkRuntime]] = {}
        self._owner: dict[str, str] = {}

    @callback
    def add(self, runtime: HeatLinkRuntime) -> bool:
        """Register a head; return True when it now owns the outdoor-unit entities."""
        ghash = runtime.ghash
        if ghash is None:
            return False
        self._members.setdefault(ghash, {})[runtime.entry.entry_id] = runtime
        if ghash not in self._owner:
            self._owner[ghash] = runtime.entry.entry_id
        return self._owner[ghash] == runtime.entry.entry_id

    @callback
    def remove(self, runtime: HeatLinkRuntime) -> str | None:
        """Forget a head. Returns the entry that should reload to take ownership."""
        ghash = runtime.ghash
        if ghash is None:
            return None
        members = self._members.get(ghash, {})
        members.pop(runtime.entry.entry_id, None)
        if self._owner.get(ghash) != runtime.entry.entry_id:
            return None
        del self._owner[ghash]
        if not members:
            self._members.pop(ghash, None)
            return None
        heir = min(members.values(), key=lambda r: r.uid)
        return heir.entry.entry_id

    def members(self, ghash: str) -> list[HeatLinkRuntime]:
        return sorted(self._members.get(ghash, {}).values(), key=lambda r: r.uid)

    def best(self, ghash: str) -> HeatLinkRuntime | None:
        """The member with the freshest group view, preferring the owner."""
        members = self._members.get(ghash, {})
        owner = members.get(self._owner.get(ghash, ""))
        candidates = ([owner] if owner else []) + self.members(ghash)
        for rt in candidates:
            if rt.group is not None and rt.group.last_update_success and rt.group.data:
                return rt
        return candidates[0] if candidates else None

    def find_entry_for_ghash(self, ghash: str, exclude_uid: str) -> HeatLinkRuntime | None:
        """A loaded head on ``ghash`` other than ``exclude_uid`` (for peer tokens)."""
        for rt in self.members(ghash):
            if rt.uid != exclude_uid and rt.settings.last_update_success:
                return rt
        return None

    @callback
    def notify(self, ghash: str | None) -> None:
        if ghash is not None:
            async_dispatcher_send(self._hass, outdoor_signal(ghash))

    async def async_refresh(self, ghash: str | None, *, park: bool, group: bool) -> None:
        """Refresh every member after a write to a shared setting."""
        if ghash is None:
            return
        for rt in self.members(ghash):
            if park and rt.park is not None:
                await rt.park.async_request_refresh()
            if group and rt.group is not None:
                await rt.group.async_request_refresh()
            await rt.settings.async_request_refresh()
