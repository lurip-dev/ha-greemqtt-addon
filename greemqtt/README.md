# Gree MQTT Bridge Add-on

Home Assistant add-on for connecting Gree air conditioners to MQTT.

## Installation

1. Add this add-on repository to Home Assistant
2. Install the "Gree MQTT Bridge" add-on
3. Configure the options
4. Start the add-on

## Configuration

### MQTT Options

- **mqtt_host**: MQTT server address (default: core-mosquitto)
- **mqtt_port**: MQTT port (default: 1883)
- **mqtt_username**: MQTT username (optional)
- **mqtt_password**: MQTT password (optional)
- **mqtt_topic**: Main MQTT topic (default: gree)

### Gree Options

- **discovery_timeout**: Device discovery timeout in seconds
- **update_interval**: Update interval in seconds
- **devices**: List of devices to discover (optional)

### Web Interface Options

- **web_interface**: Enable web interface
- **web_port**: Web interface port

### Other Options

- **log_level**: Logging level (DEBUG, INFO, WARNING, ERROR)

## Example Configuration

```yaml
mqtt_host: "192.168.1.100"
mqtt_port: 1883
mqtt_username: "homeassistant"
mqtt_password: "mypassword"
mqtt_topic: "gree"
discovery_timeout: 10
update_interval: 30
web_interface: true
web_port: 8080
log_level: "INFO"
devices:
  - ip: "192.168.1.150"
    name: "Living Room AC"
  - ip: "192.168.1.151" 
    name: "Bedroom AC"
```

## Home Assistant Auto Discovery

The add-on will automatically discover air conditioning devices and add them to Home Assistant via MQTT Discovery.

## Supported Devices

This add-on supports Gree air conditioners and compatible devices that use the Gree protocol.

## Troubleshooting

If you encounter issues:

1. Check the add-on logs in Home Assistant
2. Ensure your MQTT broker is running and accessible
3. Verify that your Gree devices are on the same network
4. Check that your devices support the Gree WiFi protocol

## Support

For issues and feature requests, please visit the [GitHub repository](https://github.com/monteship/GreeMQTT).