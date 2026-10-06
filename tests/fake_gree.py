"""A minimal emulated Gree unit speaking the LAN protocol over UDP."""

from __future__ import annotations

import asyncio
import json
import secrets
import string

from gree_mqtt import crypto


class FakeGree(asyncio.DatagramProtocol):
    def __init__(self, host: str, mac: str, scheme: str = crypto.ECB, port: int = 7000,
                 temsen: int = 64, bind_reply: str = "bindok") -> None:
        self.host, self.port, self.mac, self.scheme = host, port, mac, scheme
        self.key = self.new_key()
        self.bind_reply = bind_reply
        self.state = {"Pow": 0, "Mod": 1, "SetTem": 24, "TemUn": 0, "TemRec": 0, "WdSpd": 0,
                      "Lig": 1, "SwUpDn": 0, "SwingLfRig": 0, "TemSen": temsen}
        self.transport = None
        self.binds = 0
        self.statuses = 0
        self.commands: list[dict] = []

    @staticmethod
    def new_key() -> str:
        return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self.transport, _ = await loop.create_datagram_endpoint(
            lambda: self, local_addr=(self.host, self.port))

    def stop(self) -> None:
        if self.transport:
            self.transport.close()
            self.transport = None

    def _send(self, inner: dict, key: str, addr, extra: dict | None = None) -> None:
        packet = {"t": "pack", "i": 0, "uid": 0, "cid": self.mac, "tcid": ""}
        packet.update(extra or {})
        packet.update(crypto.encrypt(self.scheme, key, inner))
        self.transport.sendto(json.dumps(packet).encode(), addr)

    def datagram_received(self, data: bytes, addr) -> None:
        msg = json.loads(data)
        if msg.get("t") == "scan":
            self._send({"t": "dev", "cid": self.mac, "mac": self.mac, "name": f"fake{self.mac[-2:]}",
                        "ver": "V1.2.1", "model": "fake"}, crypto.generic_key(self.scheme), addr)
            return
        if msg.get("t") != "pack":
            return
        if msg.get("i") == 1:
            try:
                inner = crypto.decrypt(self.scheme, crypto.generic_key(self.scheme), msg)
            except crypto.CipherError:
                return  # wrong scheme: real units stay silent
            if inner.get("t") == "bind" and inner.get("mac") == self.mac:
                self.binds += 1
                self._send({"t": self.bind_reply, "mac": self.mac, "key": self.key, "r": 200},
                           crypto.generic_key(self.scheme), addr)
            return
        try:
            inner = crypto.decrypt(self.scheme, self.key, msg)
        except crypto.CipherError:
            return  # stale key: silent
        if inner.get("t") == "status":
            self.statuses += 1
            cols = [c for c in inner["cols"] if c in self.state]
            self._send({"t": "dat", "mac": self.mac, "r": 200, "cols": cols,
                        "dat": [self.state[c] for c in cols]}, self.key, addr)
        elif inner.get("t") == "cmd":
            props = dict(zip(inner["opt"], inner["p"]))
            self.commands.append(props)
            self.state.update(props)
            self._send({"t": "res", "mac": self.mac, "r": 200, "opt": inner["opt"], "p": inner["p"],
                        "val": inner["p"]}, self.key, addr)
