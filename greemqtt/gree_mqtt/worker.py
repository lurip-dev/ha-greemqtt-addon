"""One supervisor task per unit: keeps it bound, polled and published, forever."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from . import crypto, ha, network
from .device import BindError, DeviceError, DeviceInfo, GreeDevice

if TYPE_CHECKING:
    from .app import App

_LOGGER = logging.getLogger(__name__)

OFFLINE_AFTER = 3  # consecutive failed polls before the unit is shown unavailable
REBIND_EVERY = 3  # consecutive failures after which we drop the key and bind again
RELOCATE_EVERY = 5  # consecutive failures after which we ask discovery to find the unit's new IP
MAX_BACKOFF = 60.0

RECOVERABLE = (
    network.RequestTimeout, crypto.CipherError, BindError, DeviceError, OSError, ValueError, KeyError,
)


class DeviceWorker:
    def __init__(self, info: DeviceInfo, app: "App") -> None:
        self.info = info
        self.app = app
        self.device = GreeDevice(info, timeout=app.config.request_timeout)
        self.lock = asyncio.Lock()
        self.wake = asyncio.Event()
        self.raw: dict[str, Any] = {}
        self.available = False
        self.failures = 0
        self.task: asyncio.Task | None = None

    @property
    def label(self) -> str:
        return f"{self.info.name or self.info.mac or self.info.host} ({self.info.host})"

    def start(self) -> None:
        self.task = asyncio.create_task(self.run(), name=f"gree-{self.info.mac or self.info.host}")

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def run(self) -> None:
        while True:
            try:
                await self._cycle()
                delay = self.app.config.poll_interval
            except RECOVERABLE as err:
                delay = self._failed(err)
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), delay)
            except TimeoutError:
                pass

    async def _cycle(self) -> None:
        async with self.lock:
            if not self.info.mac or not self.info.cipher:
                await self.device.identify()
                _LOGGER.info("Identified %s as %s", self.info.host, self.info.mac)
                self.app.on_identified(self)
            if not self.info.key:
                await self.device.bind()
                self.app.registry.update(self.info)
                self.publish_discovery()
            raw = await self.device.status()
        self._succeeded(raw)

    def _succeeded(self, raw: dict[str, Any]) -> None:
        if self.failures:
            _LOGGER.info("%s is reachable again after %d failed attempt(s)", self.label, self.failures)
        self.failures = 0
        self.raw.update(raw)
        if not self.available:
            self.available = True
            _LOGGER.info("%s online", self.label)
        self.publish_state()

    def _failed(self, err: Exception) -> float:
        self.failures += 1
        level = logging.WARNING if self.failures in (1, OFFLINE_AFTER) else logging.DEBUG
        _LOGGER.log(level, "%s: attempt %d failed: %s", self.label, self.failures, err)
        if isinstance(err, crypto.CipherError) or (
            self.info.key and self.failures % REBIND_EVERY == 0
        ):
            # A key that no longer decrypts (unit reset / re-paired) or a unit that went
            # silent: bind again. Binding uses the generic key, so it also works after a reset.
            self.info.key = None
        if self.failures >= OFFLINE_AFTER and self.available:
            self.available = False
            _LOGGER.warning("%s marked unavailable; will keep retrying", self.label)
            self.publish_availability()
        elif self.failures >= OFFLINE_AFTER and self.info.mac:
            self.publish_availability()
        if self.failures % RELOCATE_EVERY == 0:
            self.app.request_discovery()
        return min(MAX_BACKOFF, 2.0 * 2 ** min(self.failures - 1, 5))

    def set_host(self, host: str) -> None:
        if host != self.info.host:
            _LOGGER.warning("%s moved to %s", self.label, host)
            self.info.host = host
            if self.info.mac:
                self.app.registry.update(self.info)
                self.app.publish(f"{self.app.topics.device(self.info.mac)}/ip", host, retain=True)
            self.wake.set()

    async def command(self, field: str, payload: str) -> None:
        try:
            props = ha.command_props(field, payload)
        except ValueError as err:
            _LOGGER.warning("%s: ignoring command %s=%r: %s", self.label, field, payload, err)
            return
        _LOGGER.info("%s: %s=%s -> %s", self.label, field, payload, props)
        try:
            async with self.lock:
                if not self.info.key:
                    await self.device.bind()
                    self.app.registry.update(self.info)
                await self.device.command(props)
            self.raw.update(props)
            self.publish_state()
        except RECOVERABLE as err:
            _LOGGER.warning("%s: command %s failed: %s", self.label, field, err)
        # Read back what the unit actually did.
        self.wake.set()

    # --- MQTT ---------------------------------------------------------------

    def discovery_messages(self) -> list[tuple[str, str, bool]]:
        if not self.info.mac:
            return []
        return [
            (topic, json.dumps(cfg), True)
            for topic, cfg in ha.discovery_messages(self.app.topics, self.info)
        ]

    def state_messages(self) -> list[tuple[str, str, bool]]:
        mac = self.info.mac
        if not mac:
            return []
        t = self.app.topics
        msgs = [
            (f"{t.device(mac)}/ip", self.info.host, True),
            (t.availability(mac), "online" if self.available else "offline", True),
        ]
        if self.raw:
            state = ha.to_state(self.info, self.raw)
            msgs.append((t.state(mac), json.dumps(state), True))
            if state["current_temperature"] is not None:
                msgs.append((t.current_temperature(mac), str(state["current_temperature"]), True))
        return msgs

    def publish_discovery(self) -> None:
        for msg in self.discovery_messages():
            self.app.publish(*msg)

    def publish_state(self) -> None:
        # State first, then availability, so HA never shows stale values as fresh.
        for msg in sorted(self.state_messages(), key=lambda m: m[0].endswith("availability")):
            self.app.publish(*msg)

    def publish_availability(self) -> None:
        if self.info.mac:
            self.app.publish(
                self.app.topics.availability(self.info.mac),
                "online" if self.available else "offline",
                retain=True,
            )
