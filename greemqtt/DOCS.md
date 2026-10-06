# Gree MQTT Bridge

Controls Gree (and rebranded: Cooper&Hunter, Sinclair, Tosot, Ekokai, ...) Wi-Fi air
conditioners over the local network and exposes them to Home Assistant through MQTT
discovery.

It was written because the built-in `gree` integration often leaves some units
unavailable after Home Assistant restarts. The causes found in that integration and the
`greeclimate` library, and how this add-on avoids them:

| Problem in core `gree` / `greeclimate` | This add-on |
| --- | --- |
| A bind that times out at startup is never retried; the unit is polled with the generic key until the next restart. | A unit only counts as bound after a real `bindok`. Bind is retried forever with backoff. |
| Units are found only by broadcast; later scans never re-announce a unit that is already known. | Known units are stored in `/data/devices.json` and contacted directly by IP with the saved key on start. Discovery also sends unicast scans to every known IP. |
| An IP change is not applied to the already-open socket. | Every request uses a fresh socket; discovery updates the IP by MAC. |
| A unit that was reset/re-paired keeps an old key forever. | After 3 failed polls, or when a reply cannot be decrypted, the key is dropped and the unit is bound again. |
| Failed polls are hard to detect, so stale state is shown. | Each request waits for an answer; after 3 failures the unit is marked unavailable and keeps being retried. |
| Late replies from one unit can be mixed up with another. | One request at a time per unit, on a connected socket. |

## Requirements

- An MQTT broker. With the **Mosquitto broker** add-on nothing needs to be configured.
- The **MQTT** integration in Home Assistant.
- Units must be connected to your Wi-Fi with the Gree+ / EWPE Smart app first.
- Give every unit a fixed IP (DHCP reservation in the router). Not strictly required,
  but it makes recovery faster.
- Some newer Wi-Fi modules (firmware 2.07, 2.10+) no longer accept local control at all;
  the log then says `refused the connection (UDP port closed)`. Nothing local can fix that.

If you used the built-in **Gree** integration, remove it once the add-on works, so the
units are not polled twice.

## Configuration

```yaml
devices:
  - host: 192.168.1.50
    name: Salon
  - host: 192.168.1.51
    name: Sypialnia
    encryption: gcm      # optional: auto (default), ecb, gcm
    temp_offset: auto    # optional: auto (default), 0, 40
poll_interval: 10
discovery_interval: 300
auto_add_discovered: true
broadcast_addresses: []
mqtt_host: ""            # empty = use the Mosquitto add-on
mqtt_base_topic: gree
discovery_prefix: homeassistant
log_level: INFO
```

- `devices` can stay empty: units found by discovery are added automatically
  (`auto_add_discovered`) and remembered. Listing them by IP is still recommended, because
  it does not depend on broadcast reaching the units (VLANs, mesh Wi-Fi, AP isolation).
- `temp_offset`: most units report room temperature +40; some do not, and some switch
  after a power loss. `auto` handles both. Set `0` or `40` if the reading is still wrong.
- `key` lets you supply a known device key; normally it is obtained automatically.

## Entities

Per unit: a climate entity (off/auto/cool/dry/fan_only/heat, target temperature 16-30 °C,
room temperature, 6 fan speeds, vertical and horizontal swing) and switches for display
light, X-Fan, health, fresh air, quiet, turbo, sleep, energy saving and 8 °C heating, plus
a diagnostic IP sensor (disabled by default).

## MQTT topics

- `gree/<mac>/state` - JSON state (retained)
- `gree/<mac>/availability` - `online`/`offline` (retained)
- `gree/<mac>/set/<field>` - commands: `hvac_mode`, `target_temperature`, `fan_mode`,
  `swing_mode`, `swing_horizontal_mode`, `power`, `light`, `xfan`, `health`, `fresh_air`,
  `quiet`, `turbo`, `sleep`, `energy_saving`, `anti_freeze`
- `gree/bridge/availability` - add-on status (last will)

When Home Assistant publishes `online` on `homeassistant/status` (after it restarts), the
add-on republishes discovery, state and availability for every unit.

## Troubleshooting

- Set `log_level: DEBUG` to see every request.
- `no answer from ...`: check the IP and that the unit is powered (Wi-Fi modules are
  often unpowered when the unit is off at the breaker). The add-on keeps retrying.
- To force a fresh bind of everything, stop the add-on and delete `devices.json` from the
  add-on's data folder.
