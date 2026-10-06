"""UDP transport for the Gree LAN protocol.

Every request opens a fresh socket. That costs nothing at these rates and means
a socket that went bad (interface flapped, host network came up late after a
reboot, ...) can never wedge a device until the next restart.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from typing import Any

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 7000


class RequestTimeout(Exception):
    """The device did not answer in time."""


class PortClosed(OSError):
    """The host answered with ICMP port unreachable: nothing listens on UDP 7000.

    Some recent Wi-Fi module firmware (e.g. 2.07, 2.10+) removed local control."""


class _Collector(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.queue: asyncio.Queue[tuple[dict[str, Any], tuple[str, int]]] = asyncio.Queue()
        self.refused = False

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            packet = json.loads(data.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            _LOGGER.debug("Ignoring non-JSON datagram from %s", addr)
            return
        if isinstance(packet, dict):
            self.queue.put_nowait((packet, addr))

    def error_received(self, exc: Exception) -> None:
        # ICMP port unreachable and friends; the request times out or reports PortClosed.
        if isinstance(exc, ConnectionRefusedError):
            self.refused = True
        _LOGGER.debug("UDP error: %s", exc)


async def _open(broadcast: bool = False, remote: tuple[str, int] | None = None):
    loop = asyncio.get_running_loop()
    if remote:
        # A connected socket: the kernel drops datagrams from anyone else and reports
        # ICMP port-unreachable back to us.
        transport, proto = await loop.create_datagram_endpoint(
            _Collector, family=socket.AF_INET, remote_addr=remote
        )
    else:
        transport, proto = await loop.create_datagram_endpoint(
            _Collector,
            family=socket.AF_INET,
            local_addr=("0.0.0.0", 0),
            allow_broadcast=broadcast,
        )
    return transport, proto


async def request(host: str, port: int, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    """Send one packet to a device and return the first packet it answers with."""
    transport, proto = await _open(remote=(host, port))
    try:
        transport.sendto(json.dumps(payload).encode())
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0 or proto.refused:
                break
            try:
                packet, addr = await asyncio.wait_for(proto.queue.get(), min(remaining, 0.25))
            except TimeoutError:
                continue
            return packet
        if proto.refused:
            raise PortClosed(f"{host}:{port} refused the connection (UDP port closed)")
        raise RequestTimeout(f"no answer from {host}:{port}")
    finally:
        transport.close()


async def scan(
    targets: list[tuple[str, int]], timeout: float
) -> list[tuple[dict[str, Any], tuple[str, int]]]:
    """Send a scan packet to every target (broadcast or unicast) and collect replies."""
    transport, proto = await _open(broadcast=True)
    replies: list[tuple[dict[str, Any], tuple[str, int]]] = []
    try:
        data = json.dumps({"t": "scan"}).encode()
        for target in targets:
            try:
                transport.sendto(data, target)
            except OSError as err:
                _LOGGER.debug("Scan to %s failed: %s", target, err)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while (remaining := deadline - loop.time()) > 0:
            try:
                replies.append(await asyncio.wait_for(proto.queue.get(), remaining))
            except TimeoutError:
                break
    finally:
        transport.close()
    return replies
