"""Wires together the device workers, LAN discovery and the MQTT connection."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
from typing import Any

import aiomqtt

from . import __version__, ha, network
from .config import Config, Registry
from .device import DeviceInfo, parse_scan_reply
from .worker import DeviceWorker

_LOGGER = logging.getLogger(__name__)

HA_STATUS_TOPIC = "status"  # under the discovery prefix
FAST_DISCOVERY_INTERVAL = 60.0  # while some unit is missing or offline


def broadcast_addresses() -> list[str]:
    """Broadcast address of every IPv4 interface on the host (add-on runs with host_network)."""
    addrs = {"255.255.255.255"}
    try:
        import ifaddr
    except ImportError:  # pragma: no cover
        return sorted(addrs)
    for adapter in ifaddr.get_adapters():
        name = adapter.nice_name or adapter.name
        if name == "lo" or name.startswith(("docker", "hassio", "veth", "br-")):
            continue
        for ip in adapter.ips:
            if not isinstance(ip.ip, str) or ip.network_prefix >= 31:
                continue
            try:
                net = ipaddress.IPv4Network(f"{ip.ip}/{ip.network_prefix}", strict=False)
            except ValueError:
                continue
            if net.is_loopback or net.is_link_local:
                continue
            addrs.add(str(net.broadcast_address))
    return sorted(addrs)


class App:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.topics = ha.Topics(config.mqtt.base_topic, config.mqtt.discovery_prefix)
        self.registry = Registry(config.data_dir / "devices.json")
        self.workers: list[DeviceWorker] = []
        self._outbox: asyncio.Queue[tuple[str, str, bool]] = asyncio.Queue(maxsize=5000)
        self._connected = False
        self._discovery_wake = asyncio.Event()

    # --- devices --------------------------------------------------------------

    def _initial_devices(self) -> list[DeviceInfo]:
        """Configured units merged with the ones remembered from previous runs."""
        self.registry.load()
        known = dict(self.registry.devices)
        result: list[DeviceInfo] = []
        for opt in self.config.devices:
            stored = known.pop(opt.mac, None) if opt.mac else None
            if stored is None:
                stored = next((d for d in known.values() if d.host == opt.host), None)
                if stored is not None:
                    known.pop(stored.mac, None)
            if stored is not None:
                # Options win, the registry fills in what was learned (key, cipher, mac...).
                stored.host = opt.host
                stored.port = opt.port
                stored.uid = opt.uid
                stored.name = opt.name or stored.name
                stored.temp_offset = opt.temp_offset
                if opt.key:
                    stored.key, stored.cipher = opt.key, opt.cipher or stored.cipher
                elif opt.cipher:
                    stored.cipher = opt.cipher
                result.append(stored)
            else:
                result.append(opt)
        if self.config.auto_add_discovered:
            result.extend(known.values())
        elif known:
            _LOGGER.info(
                "Ignoring %d remembered unit(s) not in options (auto_add_discovered is off)", len(known)
            )
        return result

    def add_worker(self, info: DeviceInfo) -> DeviceWorker:
        worker = DeviceWorker(info, self)
        self.workers.append(worker)
        worker.start()
        _LOGGER.info("Managing %s", worker.label)
        return worker

    def worker_for_mac(self, mac: str) -> DeviceWorker | None:
        return next((w for w in self.workers if w.info.mac == mac), None)

    def on_identified(self, worker: DeviceWorker) -> None:
        """A unit configured by IP told us its MAC."""
        for other in list(self.workers):
            if other is not worker and other.info.mac == worker.info.mac:
                _LOGGER.info("%s is the same unit as %s, merging", worker.label, other.label)
                if not worker.info.key and other.info.key:
                    worker.info.key, worker.info.cipher = other.info.key, other.info.cipher
                self.workers.remove(other)
                asyncio.create_task(other.stop())
        worker.publish_discovery()

    def on_discovered(self, found: DeviceInfo) -> None:
        assert found.mac
        worker = self.worker_for_mac(found.mac)
        if worker:
            worker.set_host(found.host)
            if found.version and found.version != worker.info.version:
                worker.info.version = found.version
            return
        if any(w.info.host == found.host and not w.info.mac for w in self.workers):
            return  # a configured unit that is still identifying itself
        if not self.config.auto_add_discovered:
            _LOGGER.info("Found %s at %s (not added, auto_add_discovered is off)", found.mac, found.host)
            return
        _LOGGER.info("Discovered new unit %s (%s) at %s", found.name, found.mac, found.host)
        self.add_worker(found)

    def request_discovery(self) -> None:
        self._discovery_wake.set()

    async def discovery_loop(self) -> None:
        targets_bcast = self.config.broadcast_addresses or broadcast_addresses()
        _LOGGER.info("Discovery broadcast addresses: %s", ", ".join(targets_bcast))
        while True:
            targets = [(a, network.DEFAULT_PORT) for a in targets_bcast]
            # Unicast scans too: they work where broadcast is filtered (VLANs, mesh Wi-Fi).
            targets += [(w.info.host, w.info.port) for w in self.workers]
            try:
                replies = await network.scan(targets, timeout=3.0)
            except OSError as err:
                _LOGGER.warning("Discovery scan failed: %s", err)
                replies = []
            seen = set()
            for packet, addr in replies:
                found = parse_scan_reply(packet, addr)
                if found and found.mac not in seen:
                    seen.add(found.mac)
                    self.on_discovered(found)
            _LOGGER.debug("Discovery found %d unit(s)", len(seen))
            degraded = any(not w.available for w in self.workers)
            interval = FAST_DISCOVERY_INTERVAL if degraded else self.config.discovery_interval
            self._discovery_wake.clear()
            try:
                await asyncio.wait_for(self._discovery_wake.wait(), interval)
                await asyncio.sleep(1)  # coalesce bursts of requests
            except TimeoutError:
                pass

    # --- MQTT -----------------------------------------------------------------

    def publish(self, topic: str, payload: str, retain: bool = False) -> None:
        """Queue a message. While disconnected messages are dropped: on (re)connect the
        complete state is republished, so nothing is lost that matters."""
        if not self._connected:
            return
        try:
            self._outbox.put_nowait((topic, payload, retain))
        except asyncio.QueueFull:
            _LOGGER.warning("MQTT outbox full, dropping %s", topic)

    def publish_all(self) -> None:
        topic, cfg = ha.bridge_discovery(self.topics)
        self.publish(topic, json.dumps(cfg), True)
        self.publish(self.topics.bridge_availability, "online", True)
        for worker in self.workers:
            for msg in worker.discovery_messages():
                self.publish(*msg)
        for worker in self.workers:
            worker.publish_state()
            if worker.info.mac and not worker.raw:
                worker.publish_availability()

    async def _handle_message(self, msg: aiomqtt.Message) -> None:
        topic = msg.topic.value
        payload = msg.payload.decode() if isinstance(msg.payload, (bytes, bytearray)) else str(msg.payload)
        if topic == f"{self.topics.discovery_prefix}/{HA_STATUS_TOPIC}":
            if payload == "online":
                # Home Assistant restarted: send everything again so every entity comes back.
                _LOGGER.info("Home Assistant came online, republishing all units")
                await asyncio.sleep(2)
                self.publish_all()
            return
        parsed = self.topics.parse_command(topic)
        if not parsed:
            return
        mac, field = parsed
        worker = self.worker_for_mac(mac)
        if worker is None:
            _LOGGER.warning("Command for unknown unit %s", mac)
            return
        asyncio.create_task(worker.command(field, payload))

    async def mqtt_loop(self) -> None:
        cfg = self.config.mqtt
        backoff = 1.0
        while True:
            try:
                async with aiomqtt.Client(
                    hostname=cfg.host,
                    port=cfg.port,
                    username=cfg.username,
                    password=cfg.password,
                    identifier=f"gree-mqtt-{cfg.base_topic.replace('/', '-')}",
                    keepalive=30,
                    will=aiomqtt.Will(self.topics.bridge_availability, "offline", qos=1, retain=True),
                ) as client:
                    _LOGGER.info("Connected to MQTT broker %s:%s", cfg.host, cfg.port)
                    backoff = 1.0
                    self._connected = True
                    while not self._outbox.empty():
                        self._outbox.get_nowait()
                    await client.subscribe(self.topics.command_wildcard, qos=1)
                    await client.subscribe(f"{self.topics.discovery_prefix}/{HA_STATUS_TOPIC}", qos=1)
                    self.publish_all()
                    sender = asyncio.create_task(self._sender(client))
                    try:
                        async for message in client.messages:
                            await self._handle_message(message)
                    except asyncio.CancelledError:
                        # Clean shutdown sends no last will, so say goodbye ourselves.
                        sender.cancel()
                        try:
                            await asyncio.wait_for(
                                client.publish(self.topics.bridge_availability, "offline", qos=1, retain=True),
                                3,
                            )
                        except (aiomqtt.MqttError, TimeoutError):
                            pass
                        raise
                    finally:
                        sender.cancel()
            except aiomqtt.MqttError as err:
                _LOGGER.warning("MQTT connection lost/failed: %s; retrying in %.0fs", err, backoff)
            finally:
                self._connected = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    async def _sender(self, client: aiomqtt.Client) -> None:
        while True:
            topic, payload, retain = await self._outbox.get()
            await client.publish(topic, payload, qos=1, retain=retain)

    async def shutdown(self) -> None:
        for worker in self.workers:
            await worker.stop()

    async def run(self) -> None:
        _LOGGER.info("Gree MQTT Bridge %s starting", __version__)
        for info in self._initial_devices():
            self.add_worker(info)
        if not self.workers:
            _LOGGER.info("No units configured or remembered yet; relying on discovery")
        try:
            await asyncio.gather(self.mqtt_loop(), self.discovery_loop())
        finally:
            await self.shutdown()
