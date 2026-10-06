"""Add-on options and the persistent device registry."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .device import DeviceInfo
from .network import DEFAULT_PORT

_LOGGER = logging.getLogger(__name__)


@dataclass
class MqttConfig:
    host: str = "core-mosquitto"
    port: int = 1883
    username: str | None = None
    password: str | None = None
    base_topic: str = "gree"
    discovery_prefix: str = "homeassistant"


@dataclass
class Config:
    mqtt: MqttConfig = field(default_factory=MqttConfig)
    poll_interval: float = 10.0
    discovery_interval: float = 300.0
    auto_add_discovered: bool = True
    broadcast_addresses: list[str] = field(default_factory=list)
    devices: list[DeviceInfo] = field(default_factory=list)
    request_timeout: float = 3.0
    log_level: str = "INFO"
    data_dir: Path = Path("/data")


def _supervisor_mqtt() -> dict[str, Any] | None:
    """MQTT credentials from the Supervisor (Mosquitto add-on), if available."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return None
    req = urllib.request.Request(
        "http://supervisor/services/mqtt", headers={"Authorization": f"Bearer {token}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.load(resp).get("data") or None
    except Exception as err:  # noqa: BLE001 - optional convenience only
        _LOGGER.info("No MQTT service from Supervisor (%s)", err)
        return None


def _device_from_option(opt: dict[str, Any]) -> DeviceInfo:
    enc = (opt.get("encryption") or "auto").lower()
    offset = opt.get("temp_offset")
    return DeviceInfo(
        host=str(opt["host"]).strip(),
        port=int(opt.get("port") or DEFAULT_PORT),
        mac=(str(opt["mac"]).replace(":", "").replace("-", "").lower() if opt.get("mac") else None),
        name=opt.get("name") or None,
        key=opt.get("key") or None,
        cipher=enc if enc in ("ecb", "gcm") else None,
        uid=int(opt.get("uid") or 0),
        temp_offset=None if offset in (None, "", "auto") else int(offset),
    )


def load_config(path: Path | None = None) -> Config:
    path = path or Path(os.environ.get("GREE_OPTIONS", "/data/options.json"))
    opts: dict[str, Any] = {}
    if path.exists():
        opts = json.loads(path.read_text())
    else:
        _LOGGER.warning("Options file %s not found, using defaults", path)

    mqtt = MqttConfig(
        host=opts.get("mqtt_host") or "",
        port=int(opts.get("mqtt_port") or 1883),
        username=opts.get("mqtt_username") or None,
        password=opts.get("mqtt_password") or None,
        base_topic=opts.get("mqtt_base_topic") or "gree",
        discovery_prefix=opts.get("discovery_prefix") or "homeassistant",
    )
    if not mqtt.host:
        service = _supervisor_mqtt()
        if service:
            mqtt.host = service.get("host") or "core-mosquitto"
            mqtt.port = int(service.get("port") or 1883)
            mqtt.username = service.get("username") or None
            mqtt.password = service.get("password") or None
            _LOGGER.info("Using MQTT broker from Supervisor: %s:%s", mqtt.host, mqtt.port)
        else:
            mqtt.host = "core-mosquitto"

    return Config(
        mqtt=mqtt,
        poll_interval=float(opts.get("poll_interval") or 10),
        discovery_interval=float(opts.get("discovery_interval") or 300),
        auto_add_discovered=bool(opts.get("auto_add_discovered", True)),
        broadcast_addresses=[a for a in opts.get("broadcast_addresses") or [] if a],
        devices=[_device_from_option(d) for d in opts.get("devices") or [] if d.get("host")],
        request_timeout=float(opts.get("request_timeout") or 3),
        log_level=str(opts.get("log_level") or "INFO").upper(),
        data_dir=Path(os.environ.get("GREE_DATA_DIR", "/data")),
    )


class Registry:
    """Units we have bound before, keyed by MAC, stored in /data.

    This is what makes restarts reliable: on start every known unit is contacted
    directly by IP with its saved key, without depending on broadcast discovery.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.devices: dict[str, DeviceInfo] = {}

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text())
            self.devices = {mac: DeviceInfo.from_store(d) for mac, d in raw.items()}
            _LOGGER.info("Loaded %d known device(s) from %s", len(self.devices), self.path)
        except (OSError, ValueError, TypeError) as err:
            _LOGGER.error("Cannot read %s, starting with an empty registry: %s", self.path, err)

    def save(self) -> None:
        data = {mac: info.to_store() for mac, info in self.devices.items()}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".devices-")
            with os.fdopen(fd, "w") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        except OSError as err:
            _LOGGER.error("Cannot write %s: %s", self.path, err)

    def update(self, info: DeviceInfo) -> None:
        assert info.mac
        self.devices[info.mac] = info
        self.save()
