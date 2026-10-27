"""Qubo hub integration helper for Home Assistant."""

from datetime import timedelta
import json
import logging
import ssl
import time

import aiohttp
import paho.mqtt.client as mqtt

from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    BASE_URL,
    DOMAIN,
    LOGIN_DEVICE_NAME,
    MQTT_HOST,
    MQTT_PORT,
    SERVICE_HEARTBEAT,
    SERVICE_METERING,
    SERVICE_METERING_REFRESH,
    SERVICE_SWITCH,
)

_LOGGER = logging.getLogger(__name__)


class QuboDevice:
    """State for a single Qubo device sharing the account-level MQTT connection."""

    def __init__(
        self,
        device_uuid,
        unit_uuid,
        device_name,
        handle_name,
        switch_instances,
        initial_states,
        has_metering,
        initial_availability,
    ) -> None:
        """Initialize the Qubo device."""
        self.device_uuid = device_uuid
        self.unit_uuid = unit_uuid
        self.device_name = device_name
        self.handle_name = handle_name

        # One entry per gang/relay; single-channel plugs only have instance 0
        self.switch_instances = switch_instances
        self.states = initial_states
        self.has_metering = has_metering
        self.available = initial_availability
        self.metrics = {
            "power": None,
            "current": None,
            "voltage": None,
            "consumption": None,
            "duration": None,
        }
        self._callbacks = set()

    @property
    def device_info(self) -> DeviceInfo:
        """Return device registry information shared by all entities of this device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.device_uuid)},
            name=self.device_name,
            manufacturer="Qubo",
            model=self.device_name,
        )

    def topic(self, direction, service):
        """Build the MQTT topic for a service on this device."""
        return f"/{direction}/{self.unit_uuid}/{self.device_uuid}/{service}"

    def register_callback(self, callback):
        """Register a callback to be called when the state updates."""
        self._callbacks.add(callback)

    def unregister_callback(self, callback):
        """Unregister a callback."""
        self._callbacks.discard(callback)

    def publish_update(self):
        """Notify all registered entities of a state change."""
        for callback in self._callbacks:
            callback()


class QuboHub:
    """Qubo hub integration helper for Home Assistant."""

    def __init__(
        self,
        hass: HomeAssistant,
        session,
        access_token,
        refresh_token,
        user_uuid,
        expires_at,
        client_id,  # Accept the client_id in the constructor
        devices,
    ) -> None:
        """Initialize the Qubo hub."""
        self.hass = hass
        self.session = session
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._expires_at = expires_at

        self._user_uuid = user_uuid
        self._client_id = client_id

        # Keyed by device UUID so incoming MQTT topics can be routed directly
        self.devices = {device.device_uuid: device for device in devices}
        self._unsub_refresh = None

        self._mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
        self._mqtt_client.username_pw_set(
            username=self._user_uuid, password=self._access_token
        )
        self._mqtt_client.on_connect = self._on_connect
        self._mqtt_client.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            _LOGGER.debug("Successfully connected to Qubo MQTT broker")
            topics = []
            for device in self.devices.values():
                topics.append((device.topic("monitor", SERVICE_SWITCH), 0))
                topics.append((device.topic("monitor", SERVICE_HEARTBEAT), 0))
                if device.has_metering:
                    topics.append((device.topic("monitor", SERVICE_METERING), 0))
            client.subscribe(topics)
        else:
            _LOGGER.error("Failed to connect to Qubo MQTT broker, return code %s", rc)

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            topic = msg.topic
            _LOGGER.debug("Received MQTT message on topic %s: %s", topic, payload)

            # Topics look like /monitor/<unit_uuid>/<device_uuid>/<service>
            parts = topic.split("/")
            if len(parts) != 5:
                _LOGGER.debug("Ignoring MQTT message on unexpected topic %s", topic)
                return
            device = self.devices.get(parts[3])
            if device is None:
                _LOGGER.debug("Ignoring MQTT message for unknown device on %s", topic)
                return
            service = parts[4]

            if service == SERVICE_SWITCH:
                service_data = (
                    payload.get("devices", {})
                    .get("services", {})
                    .get(SERVICE_SWITCH, {})
                )
                state_data = service_data.get("events", {}).get("stateChanged", {})
                if "power" in state_data:
                    instance_id = int(service_data.get("instanceId", 0))
                    _LOGGER.debug(
                        "Updating %s switch %s state to: %s",
                        device.device_name,
                        instance_id,
                        state_data["power"],
                    )
                    device.available = True  # If we got a message, it's online
                    device.states[instance_id] = state_data["power"] == "on"
                    self.hass.loop.call_soon_threadsafe(device.publish_update)
                else:
                    _LOGGER.debug("No 'power' attribute in switch state_data: %s", state_data)

            elif service == SERVICE_METERING:
                metrics_data = (
                    payload.get("devices", {})
                    .get("services", {})
                    .get(SERVICE_METERING, {})
                    .get("events", {})
                    .get("stateChanged", {})
                )
                if metrics_data:
                    _LOGGER.debug("Updating metrics with: %s", metrics_data)
                    device.available = True  # If we got metrics, it's online
                    device.metrics["power"] = float(metrics_data.get("power", 0))
                    device.metrics["current"] = float(metrics_data.get("current", 0))
                    device.metrics["consumption"] = float(
                        metrics_data.get("consumption", 0)
                    )
                    device.metrics["duration"] = int(metrics_data.get("duration", 0))

                    # Prevent zero-voltage drops from hitting the dashboard
                    new_voltage = float(metrics_data.get("voltage", 0))

                    # If the voltage is above 50V, it's a real reading.
                    # If it's below 50V (like 0.11V or 0V), it's a sensor glitch,
                    # so we ignore it and keep the last known good value.
                    if new_voltage > 50:
                        device.metrics["voltage"] = new_voltage

                    self.hass.loop.call_soon_threadsafe(device.publish_update)
                else:
                    _LOGGER.debug("No stateChanged data found in plugMetering payload")

            elif service == SERVICE_HEARTBEAT:
                operation_state = payload.get("devices", {}).get("operationState")
                if operation_state:
                    # Update status and notify HA if it changed
                    is_available = (operation_state == "online")
                    if device.available != is_available:
                        device.available = is_available
                        _LOGGER.debug(
                            "Qubo device %s went %s",
                            device.device_name,
                            "online" if is_available else "offline",
                        )
                        self.hass.loop.call_soon_threadsafe(device.publish_update)
                else:
                    _LOGGER.debug("No operationState found in heartbeat payload")

        except (json.JSONDecodeError, ValueError, KeyError, TypeError) as err:
            _LOGGER.error("Error processing MQTT message: %s. Payload: %s", err, msg.payload)
        except Exception as err:
            _LOGGER.error("Unexpected error in _on_message: %s", err)

    async def start(self):
        """Start the MQTT connection and begin periodic metric refreshes."""

        def connect_mqtt():

            # Create a secure, default context that enforces certificate and hostname verification
            context = ssl.create_default_context()
            self._mqtt_client.tls_set_context(context)

            self._mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
            self._mqtt_client.loop_start()

        await self.hass.async_add_executor_job(connect_mqtt)

        # Only plugs report metering, so skip the refresh timer for plain switches
        if any(device.has_metering for device in self.devices.values()):
            # Request metrics immediately, then every 60 seconds
            await self._send_meter_refresh()
            self._unsub_refresh = async_track_time_interval(
                self.hass, self._send_meter_refresh, timedelta(seconds=60)
            )

    async def stop(self):
        """Stop the MQTT connection and cancel periodic metric refreshes."""
        if self._unsub_refresh:
            self._unsub_refresh()
        self._mqtt_client.loop_stop()
        self._mqtt_client.disconnect()

    async def _async_refresh_token_if_needed(self):
        if time.time() < self._expires_at:
            return

        _LOGGER.info("Qubo access token expired. Refreshing")

        refresh_url = f"{BASE_URL}sms/api/v1/sp/d10e4bfb0153496e8e8bb955f7ebe413/users/{self._user_uuid}/auth/refresh"
        payload = {
            "accessToken": self._access_token,
            "refreshToken": self._refresh_token,
        }

        # set the headers as following
        headers = {
            "Host": "srvcapp.platform.quboworld.com",
            "User-Agent": "libcurl-agent restclient-cpp/2:1:1",
            "Accept": "*/*",
            "Login-Device-Name": LOGIN_DEVICE_NAME,
            "Source-Device-Id": self._client_id,  # Use the client_id we saved from the config flow
            "Token-Type": "USER",
        }

        try:
            async with self.session.post(
                refresh_url, json=payload, headers=headers
            ) as response:
                response.raise_for_status()
                data = await response.json()

                # Update our variables with the new data
                self._access_token = data.get("accessToken")

                expires_in = data.get("expires_in", 3600)
                self._expires_at = time.time() + expires_in - 60

                _LOGGER.debug("Token refreshed successfully")

                # Force MQTT to reconnect with the new password (token)
                def restart_mqtt():
                    """Safely reboot the MQTT client in a background thread."""
                    _LOGGER.debug("Rebooting MQTT client with new token")
                    self._mqtt_client.loop_stop()     # Kill the dead thread
                    self._mqtt_client.disconnect()    # Ensure socket is closed

                    # Apply new credentials and reconnect
                    self._mqtt_client.username_pw_set(username=self._user_uuid, password=self._access_token)
                    self._mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
                    self._mqtt_client.loop_start()    # Start the fresh thread
                    _LOGGER.info("MQTT connection successfully restored")

                # Send this blocking sequence to the safe executor pool
                await self.hass.async_add_executor_job(restart_mqtt)

        except aiohttp.ClientError as err:
            _LOGGER.error("Failed to refresh Qubo token: %s", err)

    def _publish_command(self, device, service, attributes, instance_id=0):
        """Publish a control command for a service on a device."""
        payload = {
            "command": {
                "devices": {
                    "deviceUUID": device.device_uuid,
                    "handleName": device.handle_name,
                    "services": {
                        service: {
                            "attributes": attributes,
                            "instanceId": instance_id,
                        }
                    },
                }
            },
            "deviceUUID": device.device_uuid,
            "msgSequenceId": int(time.time() * 1000),
            "srcDeviceId": self._client_id,  # Use the client_id we saved from the config flow
            "timestamp": int(time.time() * 1000),
        }
        topic = device.topic("control", service)
        _LOGGER.debug("Publishing payload to topic %s: %s", topic, payload)
        self._mqtt_client.publish(topic, json.dumps(payload))

    async def turn_on(self, device, instance_id=0):
        """Turn on a switch channel of a device."""
        await self._async_refresh_token_if_needed()
        self._publish_command(device, SERVICE_SWITCH, {"power": "on"}, instance_id)

    async def turn_off(self, device, instance_id=0):
        """Turn off a switch channel of a device."""
        await self._async_refresh_token_if_needed()
        self._publish_command(device, SERVICE_SWITCH, {"power": "off"}, instance_id)

    async def _send_meter_refresh(self, now=None):
        await self._async_refresh_token_if_needed()
        for device in self.devices.values():
            if not device.has_metering:
                continue
            _LOGGER.debug("Requesting meter refresh for Qubo device %s", device.device_uuid)
            self._publish_command(
                device, SERVICE_METERING_REFRESH, {"duration": "60"}
            )
