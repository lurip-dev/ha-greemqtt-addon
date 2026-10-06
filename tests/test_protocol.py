import asyncio

import pytest

from fake_gree import FakeGree
from gree_mqtt import crypto, ha, network
from gree_mqtt.device import DeviceInfo, GreeDevice


@pytest.mark.parametrize("scheme", [crypto.ECB, crypto.GCM])
def test_crypto_roundtrip(scheme):
    data = {"t": "status", "cols": ["Pow", "Mod"], "mac": "abcdef"}
    packet = crypto.encrypt(scheme, "0123456789abcdef", data)
    assert crypto.scheme_of(packet) == scheme
    assert crypto.decrypt(scheme, "0123456789abcdef", packet) == data
    with pytest.raises(crypto.CipherError):
        crypto.decrypt(scheme, "fedcba9876543210", packet)


@pytest.mark.parametrize("scheme", [crypto.ECB, crypto.GCM])
@pytest.mark.parametrize("bind_reply", ["bindok", "bindOk"])
async def test_identify_bind_status_command(scheme, bind_reply):
    fake = FakeGree("127.0.0.2", "c8f74200aa01", scheme, bind_reply=bind_reply)
    await fake.start()
    try:
        dev = GreeDevice(DeviceInfo(host="127.0.0.2"), timeout=0.5, retries=0)
        await dev.identify()
        assert dev.info.mac == "c8f74200aa01"
        assert dev.info.cipher == scheme
        assert await dev.bind() == fake.key
        status = await dev.status()
        assert status["SetTem"] == 24
        await dev.command({"Pow": 1, "SetTem": 21})
        assert fake.state["Pow"] == 1 and fake.state["SetTem"] == 21
    finally:
        fake.stop()


async def test_bind_falls_back_to_gcm_when_cipher_unknown():
    fake = FakeGree("127.0.0.2", "c8f74200aa02", crypto.GCM)
    await fake.start()
    try:
        dev = GreeDevice(DeviceInfo(host="127.0.0.2", mac="c8f74200aa02"), timeout=0.3, retries=0)
        await dev.bind()
        assert dev.info.cipher == crypto.GCM
    finally:
        fake.stop()


async def test_closed_port_is_reported():
    dev = GreeDevice(DeviceInfo(host="127.0.0.9", mac="x"), timeout=1, retries=0)
    with pytest.raises(network.PortClosed):
        await dev.identify()


def test_temperature_offset_auto_and_sticky():
    info = DeviceInfo(host="h")
    assert ha.current_temperature(info, {"TemSen": 64}) == 24
    assert ha.current_temperature(info, {"TemSen": 43}) == 3  # ambiguous, keeps +40
    info2 = DeviceInfo(host="h")
    assert ha.current_temperature(info2, {"TemSen": 23}) == 23
    assert ha.current_temperature(info2, {"TemSen": 0}) is None
    assert ha.current_temperature(DeviceInfo(host="h", temp_offset=0), {"TemSen": 64}) == 64


def test_command_mapping():
    assert ha.command_props("hvac_mode", "off") == {"Pow": 0}
    assert ha.command_props("hvac_mode", "heat") == {"Pow": 1, "Mod": 4}
    assert ha.command_props("target_temperature", "22.4") == {"SetTem": 22, "TemUn": 0, "TemRec": 0}
    assert ha.command_props("target_temperature", "40")["SetTem"] == 30
    assert ha.command_props("fan_mode", "medium_high") == {"WdSpd": 4}
    assert ha.command_props("sleep", "ON") == {"SwhSlp": 1, "SlpMod": 1}
    with pytest.raises(ValueError):
        ha.command_props("fan_mode", "warp")


def test_state_mapping():
    state = ha.to_state(DeviceInfo(host="h"), {"Pow": 1, "Mod": 4, "SetTem": 22, "WdSpd": 5, "Lig": 1, "TemSen": 61})
    assert state["hvac_mode"] == "heat"
    assert state["fan_mode"] == "high"
    assert state["light"] == "ON"
    assert state["current_temperature"] == 21
    assert ha.to_state(DeviceInfo(host="h"), {"Pow": 0, "Mod": 4})["hvac_mode"] == "off"


def test_command_topic_parsing():
    t = ha.Topics("home/gree", "homeassistant")
    assert t.parse_command("home/gree/abc/set/fan_mode") == ("abc", "fan_mode")
    assert t.parse_command("home/gree/abc/state") is None
    assert t.parse_command("other/abc/set/x") is None


def test_scan_hosts():
    from gree_mqtt.app import scan_hosts
    assert len(scan_hosts(["192.168.5.0/24"])) == 254
    assert scan_hosts(["10.0.0.0/16"]) == []  # too large
    assert scan_hosts(["nonsense"]) == []
