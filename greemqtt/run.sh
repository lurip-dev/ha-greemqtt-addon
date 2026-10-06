#!/bin/sh
set -e
export PYTHONUNBUFFERED=1
exec python3 -m gree_mqtt
