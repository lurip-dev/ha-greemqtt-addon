#!/bin/bash
set -e

echo "🏠 Starting Gree MQTT Bridge Add-on..."

# DEBUG: Check if our custom file is in place - THIS WILL SHOW IN HA LOGS
echo "🔍 DEBUG: Checking for custom __main__.py..."
if [ -f "/app/GreeMQTT/__main__.py" ]; then
    echo "✅ Found __main__.py at /app/GreeMQTT/__main__.py"
    
    # Check for our custom markers
    if grep -q "patch 1" /app/GreeMQTT/__main__.py; then
        echo "✅ SUCCESS: Found 'patch 1' marker in __main__.py"
    else
        echo "❌ ERROR: 'patch 1' marker NOT found in __main__.py"
    fi
    
    if grep -q "CUSTOM ERROR HANDLING" /app/GreeMQTT/__main__.py; then
        echo "✅ SUCCESS: Found 'CUSTOM ERROR HANDLING' marker in __main__.py"
    else
        echo "❌ ERROR: 'CUSTOM ERROR HANDLING' marker NOT found in __main__.py"
    fi
    
    if grep -q "ERROR-RESISTANT VERSION" /app/GreeMQTT/__main__.py; then
        echo "✅ SUCCESS: Found 'ERROR-RESISTANT VERSION' marker in __main__.py"
    else
        echo "❌ ERROR: 'ERROR-RESISTANT VERSION' marker NOT found in __main__.py"
    fi
    
    # Show first 20 lines of the file
    echo "🔍 DEBUG: First 20 lines of __main__.py:"
    head -20 /app/GreeMQTT/__main__.py
    
    # Show file size and modification time
    echo "🔍 DEBUG: File info:"
    ls -la /app/GreeMQTT/__main__.py
    
else
    echo "❌ ERROR: __main__.py NOT found at /app/GreeMQTT/__main__.py"
fi

# Read configuration from options.json
CONFIG_PATH=/data/options.json

get_config() {
    local key="$1"
    local default="$2"
    
    if [ -f "$CONFIG_PATH" ]; then
        jq -r ".$key // \"$default\"" "$CONFIG_PATH" 2>/dev/null || echo "$default"
    else
        echo "$default"
    fi
}

has_config_value() {
    local key="$1"
    
    if [ -f "$CONFIG_PATH" ]; then
        local value=$(jq -r ".$key // null" "$CONFIG_PATH" 2>/dev/null)
        [ "$value" != "null" ] && [ "$value" != "" ]
    else
        false
    fi
}

setup_environment() {
    export MQTT_BROKER=$(get_config 'mqtt_host' 'core-mosquitto')
    export MQTT_PORT=$(get_config 'mqtt_port' '1883')
    export MQTT_USER=$(get_config 'mqtt_username' '')
    export MQTT_PASSWORD=$(get_config 'mqtt_password' '')
    export MQTT_TOPIC=$(get_config 'mqtt_topic' 'gree')
    export UPDATE_INTERVAL=$(get_config 'update_interval' '3')
    export LOG_LEVEL=$(get_config 'log_level' 'INFO')
    export ADAPTIVE_POLLING_TIMEOUT=$(get_config 'adaptive_polling_timeout' '45')
    export ADAPTIVE_FAST_INTERVAL=$(get_config 'adaptive_fast_interval' '1')
    export MQTT_MESSAGE_WORKERS=$(get_config 'mqtt_message_workers' '3')
    export IMMEDIATE_RESPONSE_TIMEOUT=$(get_config 'immediate_response_timeout' '5')

    # Handle network array
    if has_config_value 'network'; then
        export NETWORK=$(jq -r '.network[]?.ip // empty' "$CONFIG_PATH" 2>/dev/null | paste -sd, - || echo "")
    fi

    # Handle subnet
    if has_config_value 'subnet'; then
        export SUBNET=$(get_config 'subnet' '')
    fi

    # Handle UDP port  
    if has_config_value 'udp_port'; then
        export UDP_PORT=$(get_config 'udp_port' '')
    fi

    echo "Configuration loaded:"
    echo "- MQTT Broker: ${MQTT_BROKER}"
    echo "- MQTT Port: ${MQTT_PORT}"
    echo "- MQTT User: ${MQTT_USER}"
    echo "- MQTT Topic: ${MQTT_TOPIC}"
    echo "- Update Interval: ${UPDATE_INTERVAL}s"
    echo "- Log Level: ${LOG_LEVEL}"

    if [ -n "${NETWORK:-}" ]; then
        echo "- Device Network: ${NETWORK}"
    else
        echo "- Device Discovery: Auto-scan enabled"
    fi

    if [ -n "${SUBNET:-}" ]; then
        echo "- Subnet: ${SUBNET}"
    fi
}

run_application() {
    echo "🚀 Launching GreeMQTT..."
    echo "🔍 FINAL CHECK: Looking for custom version markers just before launch..."
    
    # Final verification that our patches are in place
    if grep -q "🔧 CUSTOM:" /app/GreeMQTT/__main__.py; then
        echo "✅ CONFIRMED: Custom patches detected - launching patched version"
    else
        echo "❌ WARNING: Custom patches NOT detected - launching original version"
    fi
    
    exec uv run GreeMQTT
}

main() {
    setup_environment
    run_application
}

trap 'echo "🛑 Shutting down Gree MQTT Bridge..."; exit 0' SIGTERM SIGINT

main "$@"