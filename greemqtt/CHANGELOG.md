# Changelog

## 2.0.1

- Discovery also scans every address of the local network(s) one by one (`scan_subnet`,
  on by default; `scan_subnets` to pick networks), like 1.x did. This finds units that
  broadcast does not reach, so `devices` can stay empty.

## 2.0.0

Complete rewrite. The add-on no longer wraps the `monteship/greemqtt` image; it ships
its own implementation of the Gree LAN protocol built for reliable reconnects:

- Bound units (MAC, IP, key, cipher) are remembered in `/data/devices.json` and contacted
  directly on start, so a restart no longer depends on broadcast discovery or a fresh bind.
- Every unit has its own supervisor that retries forever with backoff, re-binds when the
  key stops working or the unit goes silent, and follows IP changes found by discovery.
- Supports both ECB and GCM (firmware 1.21+) encryption, `bindOk` replies and both
  `val`/`p` command replies.
- Home Assistant entities via MQTT discovery: climate (modes, fan, vertical and horizontal
  swing) plus switches for light, X-Fan, health, fresh air, quiet, turbo, sleep,
  energy saving and 8 °C heating. Everything is republished when Home Assistant restarts.
- MQTT broker credentials are taken from the Mosquitto add-on when none are configured.

The options changed: the list of units is now `devices` (with `host`), see the docs.
