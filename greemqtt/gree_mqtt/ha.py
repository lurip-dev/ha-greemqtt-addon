"""Mapping between raw Gree properties and Home Assistant MQTT entities."""

from __future__ import annotations

from typing import Any

from . import __version__
from .device import DeviceInfo

MODES = {0: "auto", 1: "cool", 2: "dry", 3: "fan_only", 4: "heat"}
FAN_MODES = {0: "auto", 1: "low", 2: "medium_low", 3: "medium", 4: "medium_high", 5: "high"}
SWING_V = {
    0: "off", 1: "full", 2: "fixed_upper", 3: "fixed_upper_middle", 4: "fixed_middle",
    5: "fixed_lower_middle", 6: "fixed_lower", 7: "swing_lower", 8: "swing_lower_middle",
    9: "swing_middle", 10: "swing_upper_middle", 11: "swing_upper",
}
SWING_H = {
    0: "off", 1: "full", 2: "left", 3: "left_center", 4: "center", 5: "right_center", 6: "right",
}

# switch key -> (Gree property, friendly name, icon, entity_category)
SWITCHES = {
    "light": ("Lig", "Display light", "mdi:lightbulb", "config"),
    "xfan": ("Blo", "X-Fan", "mdi:fan-clock", "config"),
    "health": ("Health", "Health (ionizer)", "mdi:pine-tree", None),
    "fresh_air": ("Air", "Fresh air", "mdi:air-filter", None),
    "quiet": ("Quiet", "Quiet", "mdi:volume-off", None),
    "turbo": ("Tur", "Turbo", "mdi:car-turbocharger", None),
    "sleep": ("SwhSlp", "Sleep", "mdi:power-sleep", None),
    "energy_saving": ("SvSt", "Energy saving", "mdi:leaf", None),
    "anti_freeze": ("StHt", "8°C heating", "mdi:snowflake-thermometer", None),
}

MIN_TEMP = 16
MAX_TEMP = 30
TEMP_OFFSET = 40


def current_temperature(info: DeviceInfo, raw: dict[str, Any]) -> float | None:
    """Room temperature from TemSen.

    Most firmware reports TemSen + 40, some (4.x and others) report it raw, and the
    same unit can switch after a power loss (RobHofmann #456, greeclimate #133).
    0 means the unit has no sensor. Values are unambiguous outside 40..45; inside
    that band we keep the last decision. ``temp_offset`` in the options overrides it.
    """
    value = raw.get("TemSen")
    if not isinstance(value, (int, float)) or value == 0:
        return None
    offset = info.temp_offset
    if offset is None:
        if value > TEMP_OFFSET + 5:
            offset = TEMP_OFFSET
        elif value < TEMP_OFFSET:
            offset = 0
        else:
            offset = info.extra.get("auto_temp_offset", TEMP_OFFSET)
        info.extra["auto_temp_offset"] = offset
    return float(value - offset)


def onoff(value: Any) -> str:
    return "ON" if value else "OFF"


def to_state(info: DeviceInfo, raw: dict[str, Any]) -> dict[str, Any]:
    """Build the JSON published on the state topic."""
    power = bool(raw.get("Pow"))
    state: dict[str, Any] = {
        "power": onoff(power),
        "mode": MODES.get(raw.get("Mod", 0), "auto"),
        "hvac_mode": MODES.get(raw.get("Mod", 0), "auto") if power else "off",
        "target_temperature": raw.get("SetTem"),
        "current_temperature": current_temperature(info, raw),
        "fan_mode": FAN_MODES.get(raw.get("WdSpd", 0), "auto"),
        "swing_mode": SWING_V.get(raw.get("SwUpDn", 0), "off"),
        "swing_horizontal_mode": SWING_H.get(raw.get("SwingLfRig", 0), "off"),
    }
    for key, (prop, *_rest) in SWITCHES.items():
        state[key] = onoff(raw.get(prop))
    return state


def _reverse(table: dict[int, str], value: str) -> int:
    for k, v in table.items():
        if v == value:
            return k
    raise ValueError(f"unknown value {value!r}")


def command_props(field: str, payload: str) -> dict[str, int]:
    """Translate an MQTT command into the Gree properties to send."""
    payload = payload.strip()
    if field == "hvac_mode":
        if payload == "off":
            return {"Pow": 0}
        return {"Pow": 1, "Mod": _reverse(MODES, payload)}
    if field == "power":
        return {"Pow": 1 if payload.upper() in ("ON", "1", "TRUE") else 0}
    if field == "target_temperature":
        temp = int(round(float(payload)))
        temp = max(MIN_TEMP, min(MAX_TEMP, temp))
        return {"SetTem": temp, "TemUn": 0, "TemRec": 0}
    if field == "fan_mode":
        return {"WdSpd": _reverse(FAN_MODES, payload)}
    if field == "swing_mode":
        return {"SwUpDn": _reverse(SWING_V, payload)}
    if field == "swing_horizontal_mode":
        return {"SwingLfRig": _reverse(SWING_H, payload)}
    if field in SWITCHES:
        value = 1 if payload.upper() in ("ON", "1", "TRUE") else 0
        prop = SWITCHES[field][0]
        if field == "sleep":
            return {"SwhSlp": value, "SlpMod": value}
        return {prop: value}
    raise ValueError(f"unknown command {field!r}")


class Topics:
    def __init__(self, base: str, discovery_prefix: str) -> None:
        self.base = base.rstrip("/")
        self.discovery_prefix = discovery_prefix.rstrip("/")

    @property
    def bridge_availability(self) -> str:
        return f"{self.base}/bridge/availability"

    def device(self, mac: str) -> str:
        return f"{self.base}/{mac}"

    def availability(self, mac: str) -> str:
        return f"{self.device(mac)}/availability"

    def state(self, mac: str) -> str:
        return f"{self.device(mac)}/state"

    def current_temperature(self, mac: str) -> str:
        return f"{self.device(mac)}/current_temperature"

    def command(self, mac: str, field: str) -> str:
        return f"{self.device(mac)}/set/{field}"

    @property
    def command_wildcard(self) -> str:
        return f"{self.base}/+/set/+"

    def parse_command(self, topic: str) -> tuple[str, str] | None:
        parts = topic.split("/")
        base = self.base.split("/")
        if len(parts) != len(base) + 3 or parts[: len(base)] != base or parts[-2] != "set":
            return None
        return parts[-3], parts[-1]


def _mac_pretty(mac: str) -> str:
    return ":".join(mac[i : i + 2] for i in range(0, len(mac), 2)) if len(mac) == 12 else mac


def discovery_messages(topics: Topics, info: DeviceInfo) -> list[tuple[str, dict[str, Any]]]:
    """All retained MQTT discovery configs for one unit."""
    mac = info.mac
    assert mac
    node = f"gree_{mac}"
    device = {
        "identifiers": [node],
        "connections": [["mac", _mac_pretty(mac)]],
        "name": info.name or f"Gree {mac[-6:]}",
        "manufacturer": "Gree",
        "model": info.model or "Air conditioner",
        "sw_version": info.version,
        "via_device": "gree_mqtt_bridge",
    }
    origin = {"name": "Gree MQTT Bridge", "sw": __version__,
              "url": "https://github.com/lurip-dev/ha-greemqtt-addon"}
    availability = {
        "availability": [
            {"topic": topics.bridge_availability},
            {"topic": topics.availability(mac)},
        ],
        "availability_mode": "all",
    }
    state = topics.state(mac)
    climate = {
        "name": None,
        "unique_id": f"{node}_climate",
        "device": device,
        "origin": origin,
        **availability,
        "modes": ["off", *MODES.values()],
        "mode_state_topic": state,
        "mode_state_template": "{{ value_json.hvac_mode }}",
        "mode_command_topic": topics.command(mac, "hvac_mode"),
        "temperature_state_topic": state,
        "temperature_state_template": "{{ value_json.target_temperature }}",
        "temperature_command_topic": topics.command(mac, "target_temperature"),
        "current_temperature_topic": topics.current_temperature(mac),
        "fan_modes": list(FAN_MODES.values()),
        "fan_mode_state_topic": state,
        "fan_mode_state_template": "{{ value_json.fan_mode }}",
        "fan_mode_command_topic": topics.command(mac, "fan_mode"),
        "swing_modes": list(SWING_V.values()),
        "swing_mode_state_topic": state,
        "swing_mode_state_template": "{{ value_json.swing_mode }}",
        "swing_mode_command_topic": topics.command(mac, "swing_mode"),
        "swing_horizontal_modes": list(SWING_H.values()),
        "swing_horizontal_mode_state_topic": state,
        "swing_horizontal_mode_state_template": "{{ value_json.swing_horizontal_mode }}",
        "swing_horizontal_mode_command_topic": topics.command(mac, "swing_horizontal_mode"),
        "min_temp": MIN_TEMP,
        "max_temp": MAX_TEMP,
        "temp_step": 1,
        "precision": 1.0,
        "temperature_unit": "C",
        "optimistic": False,
    }
    msgs = [(f"{topics.discovery_prefix}/climate/{node}/climate/config", climate)]
    for key, (_prop, name, icon, category) in SWITCHES.items():
        cfg = {
            "name": name,
            "unique_id": f"{node}_{key}",
            "device": device,
            "origin": origin,
            **availability,
            "icon": icon,
            "state_topic": state,
            "value_template": f"{{{{ value_json.{key} }}}}",
            "command_topic": topics.command(mac, key),
            "payload_on": "ON",
            "payload_off": "OFF",
        }
        if category:
            cfg["entity_category"] = category
        msgs.append((f"{topics.discovery_prefix}/switch/{node}/{key}/config", cfg))
    msgs.append(
        (
            f"{topics.discovery_prefix}/sensor/{node}/ip/config",
            {
                "name": "IP address",
                "unique_id": f"{node}_ip",
                "device": device,
                "origin": origin,
                "availability": [{"topic": topics.bridge_availability}],
                "state_topic": f"{topics.device(mac)}/ip",
                "entity_category": "diagnostic",
                "icon": "mdi:ip-network",
                "enabled_by_default": False,
            },
        )
    )
    return msgs


def bridge_discovery(topics: Topics) -> tuple[str, dict[str, Any]]:
    """A connectivity binary sensor for the add-on itself; also the via_device parent."""
    return (
        f"{topics.discovery_prefix}/binary_sensor/gree_mqtt_bridge/connectivity/config",
        {
            "name": "Connection",
            "unique_id": "gree_mqtt_bridge_connectivity",
            "device_class": "connectivity",
            "entity_category": "diagnostic",
            "state_topic": topics.bridge_availability,
            "payload_on": "online",
            "payload_off": "offline",
            "device": {
                "identifiers": ["gree_mqtt_bridge"],
                "name": "Gree MQTT Bridge",
                "manufacturer": "lurip-dev",
                "sw_version": __version__,
            },
        },
    )
