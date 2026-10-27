"""Constants for the Qubo integration."""

DOMAIN = "qubo"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
BASE_URL = "https://srvcapp.platform.quboworld.com/"
LOGIN_DEVICE_NAME = "HA-Qubo-Integration"
DEVICE_ATTRIBUTE = "HomeAssistant|Server|Integration"
APP_ID = "934488E68332E88B1E0F9AF552840184955629777525A195949C0BE97DEF6455"

MQTT_HOST = "mqtt.platform.quboworld.com"
MQTT_PORT = 8883

# MQTT service names used in topics and device shadows
SERVICE_SWITCH = "lcSwitchControl"
SERVICE_METERING = "plugMetering"
SERVICE_METERING_REFRESH = "meteringRefresh"
SERVICE_HEARTBEAT = "heartbeat"
