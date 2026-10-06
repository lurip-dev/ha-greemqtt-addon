# Gree MQTT Add-ons

Home Assistant add-on repository.

[![Add repository](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Flurip-dev%2Fha-greemqtt-addon)

## Add-ons

- [Gree MQTT Bridge](greemqtt/) - controls Gree Wi-Fi air conditioners locally and
  publishes them to Home Assistant via MQTT discovery, with reliable reconnects after
  restarts. Documentation: [greemqtt/DOCS.md](greemqtt/DOCS.md).

## Development

```sh
pip install -r greemqtt/requirements.txt cryptography pytest pytest-asyncio
docker run -d -p 18830:1883 eclipse-mosquitto:2 mosquitto -c /mosquitto-no-auth.conf
pytest
```

The tests run against emulated units (`tests/fake_gree.py`) on loopback addresses; the
end-to-end tests need the MQTT broker above and are skipped without it.
