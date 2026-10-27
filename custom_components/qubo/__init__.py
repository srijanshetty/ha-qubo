"""The Qubo integration."""

import logging
import time

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    APP_ID,
    BASE_URL,
    CONF_PASSWORD,
    CONF_USERNAME,
    DEVICE_ATTRIBUTE,
    DOMAIN,
    LOGIN_DEVICE_NAME,
    SERVICE_METERING,
    SERVICE_METERING_REFRESH,
    SERVICE_SWITCH,
)
from .hub import QuboDevice, QuboHub

_LOGGER = logging.getLogger(__name__)

# Add sensor to the platforms list
PLATFORMS = ["sensor", "switch"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Qubo from a config entry."""
    session = async_get_clientsession(hass)
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    client_id = entry.data["client_id"]  # Retrieve the client_id we saved during config flow

    # --- YOUR EXISTING LOGIN & SYNC HTTP API CALLS GO HERE ---
    # After successfully parsing the sync response, you should have:
    # access_token, refresh_token, user_uuid, device_uuid, unit_uuid, expires_at, initial_state, device_name
    session = async_get_clientsession(hass)
    _LOGGER.info("Attempting to authenticate with Qubo API")

    # --- YOUR LOGIN API CALL GOES HERE ---
    login_url = f"{BASE_URL}sms/api/v4/sp/d10e4bfb0153496e8e8bb955f7ebe413/user/login"  # Replace with actual Qubo URL
    payload = {
        "accessToken": "",
        "deviceAttribute": DEVICE_ATTRIBUTE,
        "username": username,
        "password": password,
    }

    params = {"system": "CS"}

    headers = {
        "Host": "srvcapp.platform.quboworld.com",
        "User-Agent": "libcurl-agent restclient-cpp/2:1:1",
        "Accept": "*/*",
        "App-Id": APP_ID,
        "Login-Device-Name": LOGIN_DEVICE_NAME,  # Use the constant for the device name
        "Source": "ANDROID",
        "Source-Device-Id": client_id,
        "Token-Type": "USER",
    }

    try:
        async with session.post(
            login_url, json=payload, headers=headers, params=params
        ) as response:
            # Check for errors BEFORE calling raise_for_status()
            if response.status >= 400:
                error_text = await response.text()
                _LOGGER.error(
                    "Qubo API Login Failed! Status: %s, Server said: %s",
                    response.status,
                    error_text,
                )
                return False  # Stop setting up the integration

            response.raise_for_status()
            data = await response.json()

            # Extract your tokens (Adjust keys based on Qubo's actual JSON response)
            access_token = data.get("accessToken")
            refresh_token = data.get("refreshToken")
            user_uuid = data.get("uuid")

            # Calculate expiration time (current time + 3600 seconds)
            # We subtract 60 seconds just to give a safe buffer
            expires_in = data.get("expires_in", 3600)
            expires_at = time.time() + expires_in - 60

            _LOGGER.info("Successfully authenticated with Qubo API")

            # --- YOUR SYNC API CALL GOES HERE ---
            sync_url = f"{BASE_URL}unit-entity-management/api/v6/sp/d10e4bfb0153496e8e8bb955f7ebe413/units/sync"  # Replace with ACTUAL Sync API URL

            sync_headers = {
                "Host": "srvcapp.platform.quboworld.com",
                "User-Agent": "libcurl-agent restclient-cpp/2:1:1",
                "Accept": "*/*",
                "Login-Device-Name": LOGIN_DEVICE_NAME,  # Use the constant for the device name
                "Source-Device-Id": client_id,
                "Subscriber-Key": access_token,
                "Token-Type": "USER",
                "User-UUID": user_uuid,
            }

            sync_payload = {"syncType": 1}

            async with session.post(
                sync_url, headers=sync_headers, json=sync_payload
            ) as sync_response:
                sync_response.raise_for_status()
                sync_data = await sync_response.json()

                devices = sync_data.get("devices", [])
                if not devices:
                    _LOGGER.error("No devices found in Qubo account!")
                    return False

                shadows = {
                    shadow.get("deviceUUID"): shadow
                    for shadow in sync_data.get("deviceshadow", [])
                }

                qubo_devices = []
                for device in devices:
                    qubo_device = _build_device(device, shadows)
                    if qubo_device is not None:
                        qubo_devices.append(qubo_device)

                if not qubo_devices:
                    _LOGGER.error(
                        "No supported Qubo switches or plugs found in your account! Found: %s",
                        [device.get("deviceName") for device in devices],
                    )
                    return False

    except aiohttp.ClientError as err:
        _LOGGER.error("Failed to connect to Qubo API: %s", err)
        return False

    # Create the shared Hub and start it
    hub = QuboHub(
        hass,
        session,
        access_token,
        refresh_token,
        user_uuid,
        expires_at,
        client_id,  # Pass the client_id to the Hub
        qubo_devices,
    )
    await hub.start()

    # Save the Hub to hass.data so switch.py and sensor.py can access it
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {"hub": hub}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _build_device(device, shadows):
    """Build a QuboDevice from sync data, or None if it has no switch service."""
    device_uuid = device.get("deviceUUID")
    device_name = device.get("deviceName")
    shadow = shadows.get(device_uuid)
    if shadow is None:
        _LOGGER.debug("Skipping Qubo device %s: no device shadow", device_name)
        return None

    services = shadow.get("services", [])
    service_names = {service.get("service") for service in services}
    _LOGGER.debug("Qubo device %s exposes services: %s", device_name, service_names)

    if SERVICE_SWITCH not in service_names:
        _LOGGER.debug("Skipping Qubo device %s: no %s service", device_name, SERVICE_SWITCH)
        return None

    # Multi-gang switches report one lcSwitchControl entry per channel
    initial_states = {}
    for service in services:
        if service.get("service") == SERVICE_SWITCH:
            instance_id = int(service.get("instanceId", 0))
            power_attr = service.get("attributes", {}).get("power", {})
            initial_states[instance_id] = power_attr.get("value") == "on"

    # Older plug firmware may not list metering in the shadow, so fall back to the name
    has_metering = bool(
        service_names & {SERVICE_METERING, SERVICE_METERING_REFRESH}
    ) or "plug" in (device_name or "").lower()

    op_state_value = shadow.get("operationState", {}).get("value")

    _LOGGER.info(
        "Found Qubo device %s with %d switch channel(s)%s",
        device_name,
        len(initial_states),
        " and energy metering" if has_metering else "",
    )

    return QuboDevice(
        device_uuid,
        device.get("unitUUID"),
        device_name,
        device.get("handleName"),
        sorted(initial_states),
        initial_states,
        has_metering,
        op_state_value == "online",
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    hub = hass.data[DOMAIN][entry.entry_id]["hub"]
    await hub.stop()  # Disconnect MQTT safely

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
