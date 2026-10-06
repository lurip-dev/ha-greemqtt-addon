"""End-to-end: emulated units + a real MQTT broker (set MQTT_TEST_PORT to run)."""

import asyncio
import json
import os
import socket

import aiomqtt
import pytest

from fake_gree import FakeGree
from gree_mqtt import crypto, worker
from gree_mqtt.app import App
from gree_mqtt.config import Config, MqttConfig
from gree_mqtt.device import DeviceInfo

PORT = int(os.environ.get("MQTT_TEST_PORT", "18830"))


def _broker_up() -> bool:
    try:
        socket.create_connection(("127.0.0.1", PORT), timeout=1).close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _broker_up(), reason="no MQTT broker on MQTT_TEST_PORT")


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(worker, "MAX_BACKOFF", 0.5)


def make_config(tmp_path, devices, base="gree_test"):
    return Config(
        mqtt=MqttConfig(host="127.0.0.1", port=PORT, base_topic=base, discovery_prefix=f"{base}_ha"),
        poll_interval=0.5,
        discovery_interval=2,
        broadcast_addresses=["127.0.0.250"],
        devices=devices,
        request_timeout=0.3,
        data_dir=tmp_path,
    )


class Watcher:
    """Collects retained/live messages under a prefix."""

    def __init__(self, *prefixes):
        self.prefixes = prefixes
        self.msgs: dict[str, str] = {}
        self.counts: dict[str, int] = {}
        self.ready = asyncio.Event()

    async def run(self):
        async with aiomqtt.Client("127.0.0.1", PORT) as client:
            self.client = client
            for p in self.prefixes:
                await client.subscribe(f"{p}/#")
            self.ready.set()
            async for m in client.messages:
                self.msgs[m.topic.value] = m.payload.decode()
                self.counts[m.topic.value] = self.counts.get(m.topic.value, 0) + 1

    async def wait(self, topic, value=None, timeout=15):
        async def check():
            while topic not in self.msgs or (value is not None and self.msgs[topic] != value):
                await asyncio.sleep(0.05)
        await asyncio.wait_for(check(), timeout)


async def start_app(cfg):
    app = App(cfg)
    task = asyncio.create_task(app.run())
    return app, task


async def stop(task):
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def clear_retained(base):
    """Wipe retained topics from earlier runs."""
    seen = []
    async with aiomqtt.Client("127.0.0.1", PORT) as c:
        await c.subscribe(f"{base}/#")
        await c.subscribe(f"{base}_ha/#")
        try:
            async with asyncio.timeout(0.5):
                async for m in c.messages:
                    seen.append(m.topic.value)
        except TimeoutError:
            pass
        for t in seen:
            await c.publish(t, b"", retain=True)


async def test_restart_reconnects_every_unit_from_registry(tmp_path):
    base = "gree_t1"
    await clear_retained(base)
    a = FakeGree("127.0.0.2", "c8f742000001", crypto.ECB)
    b = FakeGree("127.0.0.3", "c8f742000002", crypto.GCM, temsen=23)
    await a.start(); await b.start()
    w = Watcher(base, f"{base}_ha")
    wt = asyncio.create_task(w.run())
    await w.ready.wait()
    try:
        cfg = make_config(tmp_path, [DeviceInfo(host="127.0.0.2"), DeviceInfo(host="127.0.0.3", name="Sypialnia")], base)
        app, task = await start_app(cfg)
        await w.wait(f"{base}/c8f742000001/availability", "online")
        await w.wait(f"{base}/c8f742000002/availability", "online")
        await w.wait(f"{base}_ha/climate/gree_c8f742000002/climate/config")
        cfg_b = json.loads(w.msgs[f"{base}_ha/climate/gree_c8f742000002/climate/config"])
        assert cfg_b["device"]["name"] == "Sypialnia"
        assert w.msgs[f"{base}/c8f742000001/current_temperature"] == "24.0"
        assert w.msgs[f"{base}/c8f742000002/current_temperature"] == "23.0"

        # Command from HA reaches the unit.
        await w.client.publish(f"{base}/c8f742000001/set/hvac_mode", "cool")
        await w.client.publish(f"{base}/c8f742000001/set/target_temperature", "21")
        for _ in range(100):
            if a.state["Pow"] == 1 and a.state["SetTem"] == 21:
                break
            await asyncio.sleep(0.05)
        assert a.state["Pow"] == 1 and a.state["SetTem"] == 21
        await w.wait(f"{base}/c8f742000001/state")
        await stop(task)
        await w.wait(f"{base}/bridge/availability", "offline")  # last will
        binds = (a.binds, b.binds)

        # Restart with *no* devices in options and broadcast unreachable: the registry alone
        # must bring every unit back, without needing to bind again.
        cfg2 = make_config(tmp_path, [], base)
        app2, task2 = await start_app(cfg2)
        await w.wait(f"{base}/bridge/availability", "online")
        statuses = (a.statuses, b.statuses)
        for _ in range(100):
            if a.statuses > statuses[0] and b.statuses > statuses[1]:
                break
            await asyncio.sleep(0.05)
        assert a.statuses > statuses[0] and b.statuses > statuses[1]
        assert (a.binds, b.binds) == binds
        assert all(wk.available for wk in app2.workers)
        await stop(task2)
    finally:
        a.stop(); b.stop(); wt.cancel()


async def test_unit_offline_at_start_and_after_key_reset(tmp_path):
    base = "gree_t2"
    await clear_retained(base)
    a = FakeGree("127.0.0.4", "c8f742000004", crypto.ECB)
    w = Watcher(base, f"{base}_ha")
    wt = asyncio.create_task(w.run())
    await w.ready.wait()
    cfg = make_config(tmp_path, [DeviceInfo(host="127.0.0.4", mac="c8f742000004")], base)
    app, task = await start_app(cfg)
    try:
        await asyncio.sleep(2)  # unit is powered off while the add-on starts
        assert not app.workers[0].available
        await a.start()
        await w.wait(f"{base}/c8f742000004/availability", "online", timeout=20)

        # Unit re-paired / reset: old key no longer works -> must re-bind by itself.
        a.key = FakeGree.new_key()
        await w.wait(f"{base}/c8f742000004/availability", "offline", timeout=20)
        await w.wait(f"{base}/c8f742000004/availability", "online", timeout=20)
        assert a.binds >= 2

        # IP change: the unit moves; discovery (unicast/broadcast) cannot see it at the old IP,
        # but once it answers at a new address it is picked up.
        a.stop()
        a.host = "127.0.0.5"
        await a.start()
        app.config.broadcast_addresses = []  # not used after start; emulate discovery reply instead
        from gree_mqtt.device import DeviceInfo as DI
        app.on_discovered(DI(host="127.0.0.5", mac="c8f742000004", cipher=crypto.ECB))
        await w.wait(f"{base}/c8f742000004/ip", "127.0.0.5")
        await w.wait(f"{base}/c8f742000004/availability", "online", timeout=20)
    finally:
        await stop(task)
        a.stop(); wt.cancel()


async def test_home_assistant_restart_republishes_discovery(tmp_path):
    base = "gree_t3"
    await clear_retained(base)
    a = FakeGree("127.0.0.6", "c8f742000006", crypto.ECB)
    await a.start()
    w = Watcher(base, f"{base}_ha")
    wt = asyncio.create_task(w.run())
    await w.ready.wait()
    app, task = await start_app(make_config(tmp_path, [DeviceInfo(host="127.0.0.6")], base))
    try:
        topic = f"{base}_ha/climate/gree_c8f742000006/climate/config"
        await w.wait(topic)
        before = w.counts[topic]
        await w.client.publish(f"{base}_ha/status", "online")
        for _ in range(100):
            if w.counts[topic] > before:
                break
            await asyncio.sleep(0.05)
        assert w.counts[topic] > before
    finally:
        await stop(task)
        a.stop(); wt.cancel()


async def test_unconfigured_unit_found_by_subnet_scan(tmp_path):
    base = "gree_t4"
    await clear_retained(base)
    a = FakeGree("127.0.0.12", "c8f742000012", crypto.GCM)
    await a.start()
    w = Watcher(base, f"{base}_ha")
    wt = asyncio.create_task(w.run())
    await w.ready.wait()
    cfg = make_config(tmp_path, [], base)
    cfg.scan_subnets = ["127.0.0.8/29"]
    app, task = await start_app(cfg)
    try:
        await w.wait(f"{base}/c8f742000012/availability", "online", timeout=20)
        assert (tmp_path / "devices.json").exists()
    finally:
        await stop(task)
        a.stop(); wt.cancel()
