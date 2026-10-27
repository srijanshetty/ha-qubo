"""Qubo Smart Switch and Plug Switch Entities."""

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: callable):
    """Set up the Qubo switch platform from a config entry."""
    hub = hass.data[DOMAIN][entry.entry_id]["hub"]

    # Create one switch per channel, so multi-gang switches get one entity per gang
    entities = [
        QuboSwitch(hub, device, instance_id, index)
        for device in hub.devices.values()
        for index, instance_id in enumerate(device.switch_instances)
    ]
    async_add_entities(entities)

class QuboSwitch(SwitchEntity):
    """Representation of a single channel of a Qubo switch or plug."""

    def __init__(self, hub, device, instance_id, index) -> None:
        """Initialize the Qubo switch entity."""
        self._hub = hub
        self._device = device
        self._instance_id = instance_id

        if len(device.switch_instances) == 1:
            self._attr_name = device.device_name
        else:
            self._attr_name = f"{device.device_name} {index + 1}"

        # Keep the bare device UUID for instance 0 so existing plug entities keep their ID
        if instance_id == 0:
            self._attr_unique_id = device.device_uuid
        else:
            self._attr_unique_id = f"{device.device_uuid}_{instance_id}"

        self._attr_icon = "mdi:power-plug" if device.has_metering else "mdi:light-switch"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for the switch."""
        return self._device.device_info

    @property
    def is_on(self) -> bool:
        """Return True if the switch is on."""
        return self._device.states.get(self._instance_id, False)

    @property
    def should_poll(self) -> bool:
        """Return whether polling is required for this entity."""
        return False

    @property
    def available(self) -> bool:
        """Return True if the device is online."""
        return self._device.available


    async def async_turn_on(self, **kwargs):
        """Turn the switch on."""
        await self._hub.turn_on(self._device, self._instance_id)

    async def async_turn_off(self, **kwargs):
        """Turn the switch off."""
        await self._hub.turn_off(self._device, self._instance_id)

    async def async_added_to_hass(self):
        """Register the device callback when the entity is added."""
        self._device.register_callback(self.async_write_ha_state)

    async def async_will_remove_from_hass(self):
        """Unregister the device callback when the entity is removed."""
        self._device.unregister_callback(self.async_write_ha_state)
