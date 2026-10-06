"""Talking to one Gree unit: scan, bind, status and command."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from . import crypto, network

_LOGGER = logging.getLogger(__name__)

# Status columns we ask for. Units ignore the ones they do not support.
STATUS_COLS = [
    "Pow", "Mod", "SetTem", "TemUn", "TemRec", "WdSpd", "Air", "Blo", "Health",
    "SwhSlp", "SlpMod", "Lig", "SwingLfRig", "SwUpDn", "Quiet", "Tur", "StHt",
    "SvSt", "TemSen", "HeatCoolType", "hid",
]


class BindError(Exception):
    """Binding failed with every known cipher."""


class DeviceError(Exception):
    """The device answered with something we did not expect."""


@dataclass
class DeviceInfo:
    """What we know (and persist) about a unit."""

    host: str
    port: int = network.DEFAULT_PORT
    mac: str | None = None
    name: str | None = None
    key: str | None = None
    cipher: str | None = None
    version: str | None = None
    model: str | None = None
    uid: int = 0
    temp_offset: int | None = None  # None = auto
    extra: dict[str, Any] = field(default_factory=dict)

    def to_store(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("extra", None)
        return data

    @classmethod
    def from_store(cls, data: dict[str, Any]) -> "DeviceInfo":
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__ and k != "extra"}
        return cls(**known)


def parse_scan_reply(packet: dict[str, Any], addr: tuple[str, int]) -> DeviceInfo | None:
    """Turn a scan reply into DeviceInfo, or None if it is not one."""
    if packet.get("t") != "pack" or "pack" not in packet:
        return None
    scheme = crypto.scheme_of(packet)
    try:
        data = crypto.decrypt(scheme, crypto.generic_key(scheme), packet)
    except crypto.CipherError as err:
        _LOGGER.debug("Undecryptable scan reply from %s: %s", addr, err)
        return None
    if data.get("t") != "dev":
        return None
    mac = data.get("mac") or data.get("cid") or packet.get("cid")
    if not mac:
        return None
    return DeviceInfo(
        host=addr[0],
        mac=str(mac).lower(),
        name=data.get("name") or None,
        cipher=scheme,
        version=_version(data),
        model=data.get("model") or data.get("brand"),
    )


def _version(data: dict[str, Any]) -> str | None:
    # Firmware version is reported as e.g. "V1.2.1"; "hid" carries it too on newer units.
    ver = data.get("ver") or ""
    ver = str(ver).lstrip("Vv")
    return ver or None


class GreeDevice:
    """Protocol operations against one unit. Stateless apart from ``info``."""

    def __init__(self, info: DeviceInfo, timeout: float = 3.0, retries: int = 2) -> None:
        self.info = info
        self.timeout = timeout
        self.retries = retries

    async def _send(self, payload: dict[str, Any]) -> dict[str, Any]:
        last: Exception | None = None
        for _ in range(self.retries + 1):
            try:
                return await network.request(self.info.host, self.info.port, payload, self.timeout)
            except network.RequestTimeout as err:
                # Units doze off and need a moment to answer; just ask again.
                last = err
                await asyncio.sleep(0.5)
        assert last is not None
        raise last

    def _outer(self, i: int) -> dict[str, Any]:
        return {"cid": "app", "i": i, "t": "pack", "uid": self.info.uid, "tcid": self.info.mac or ""}

    async def identify(self) -> DeviceInfo:
        """Unicast scan: learn MAC, cipher and firmware of the unit at ``host``."""
        reply = await self._send({"t": "scan"})
        found = parse_scan_reply(reply, (self.info.host, self.info.port))
        if found is None:
            raise DeviceError(f"unexpected scan reply from {self.info.host}")
        self.info.mac = found.mac
        self.info.name = self.info.name or found.name
        self.info.version = found.version
        self.info.model = self.info.model or found.model
        if self.info.cipher is None:
            self.info.cipher = found.cipher
        return self.info

    async def bind(self) -> str:
        """Obtain the unit's private key. Tries the known cipher first, then the other one."""
        if not self.info.mac:
            await self.identify()
        order = [crypto.ECB, crypto.GCM]
        if self.info.cipher == crypto.GCM:
            order.reverse()
        errors = []
        for scheme in order:
            payload = self._outer(1)
            payload.update(
                crypto.encrypt(
                    scheme,
                    crypto.generic_key(scheme),
                    {"mac": self.info.mac, "t": "bind", "uid": self.info.uid},
                )
            )
            try:
                reply = await self._send(payload)
                data = crypto.decrypt(scheme, crypto.generic_key(scheme), reply)
            except network.PortClosed:
                raise
            except (network.RequestTimeout, crypto.CipherError) as err:
                errors.append(f"{scheme}: {err}")
                continue
            # Some firmware answers "bindOk" (greeclimate #113).
            if str(data.get("t", "")).lower() == "bindok" and data.get("key"):
                self.info.key = data["key"]
                self.info.cipher = scheme
                _LOGGER.info("Bound %s (%s) using %s", self.info.mac, self.info.host, scheme)
                return self.info.key
            errors.append(f"{scheme}: unexpected reply {data.get('t')!r}")
        raise BindError("; ".join(errors))

    async def _call(self, data: dict[str, Any]) -> dict[str, Any]:
        if not self.info.key or not self.info.cipher:
            raise BindError("device is not bound")
        payload = self._outer(0)
        payload.update(crypto.encrypt(self.info.cipher, self.info.key, data))
        reply = await self._send(payload)
        return crypto.decrypt(self.info.cipher, self.info.key, reply)

    async def status(self, cols: list[str] | None = None) -> dict[str, Any]:
        cols = cols or STATUS_COLS
        data = await self._call({"cols": cols, "mac": self.info.mac, "t": "status"})
        if data.get("t") != "dat":
            raise DeviceError(f"unexpected status reply {data.get('t')!r}")
        return dict(zip(data.get("cols", []), data.get("dat", [])))

    async def command(self, props: dict[str, int]) -> dict[str, Any]:
        data = await self._call({"opt": list(props), "p": list(props.values()), "t": "cmd"})
        if data.get("t") != "res":
            raise DeviceError(f"unexpected command reply {data.get('t')!r}")
        values = data.get("val") or data.get("p") or []
        return dict(zip(data.get("opt", []), values))
