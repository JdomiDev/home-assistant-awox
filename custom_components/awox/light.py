import logging
from typing import Any, Optional

from .awox_mesh import AwoxMesh

import homeassistant.util.color as color_util
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

# ⚡ Only import classes/enums, define constants locally
from homeassistant.components.light import LightEntity, ColorMode

from homeassistant.const import (
    CONF_NAME,
    CONF_DEVICES,
    CONF_MAC,
    STATE_ON,
    STATE_OFF,
    STATE_UNAVAILABLE,
)
from .const import DOMAIN, CONF_MESH_ID, CONF_MANUFACTURER, CONF_MODEL, CONF_FIRMWARE

# Define ATTR_* constants locally for HA 2026+
ATTR_BRIGHTNESS = "brightness"
ATTR_COLOR_TEMP = "color_temp"
ATTR_RGB_COLOR = "rgb_color"

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    _LOGGER.debug('entry %s', entry.data[CONF_DEVICES])

    mesh = hass.data[DOMAIN][entry.entry_id]
    lights = []
    for device in entry.data[CONF_DEVICES]:
        if 'light' not in device['type']:
            continue
        device.setdefault(CONF_MANUFACTURER, None)
        device.setdefault(CONF_MODEL, None)
        device.setdefault(CONF_FIRMWARE, None)

        type_string = device.get('type', '')
        supported_color_modes = set()

        if 'color' in type_string:
            supported_color_modes.add(ColorMode.RGB)
        if 'temperature' in type_string:
            supported_color_modes.add(ColorMode.COLOR_TEMP)
        if not supported_color_modes and 'dimming' in type_string:
            supported_color_modes.add(ColorMode.BRIGHTNESS)
        if not supported_color_modes:
            supported_color_modes.add(ColorMode.ONOFF)

        light = AwoxLight(
            mesh,
            device[CONF_MAC],
            device[CONF_MESH_ID],
            device[CONF_NAME],
            supported_color_modes,
            device[CONF_MANUFACTURER],
            device[CONF_MODEL],
            device[CONF_FIRMWARE]
        )
        _LOGGER.info('Setup light [%d] %s', device[CONF_MESH_ID], device[CONF_NAME])
        lights.append(light)

    async_add_entities(lights)


def convert_value_to_available_range(value, min_from, max_from, min_to, max_to) -> int:
    normalized = (value - min_from) / (max_from - min_from)
    new_value = min(round((normalized * (max_to - min_to)) + min_to), max_to)
    return max(new_value, min_to)


class AwoxLight(CoordinatorEntity, LightEntity):
    """Representation of an AwoX Light."""

    def __init__(
        self,
        coordinator: AwoxMesh,
        mac: str,
        mesh_id: int,
        name: str,
        supported_color_modes: set[str] | None,
        manufacturer: str,
        model: str,
        firmware: str
    ):
        super().__init__(coordinator)
        self._mesh = coordinator
        self._mac = mac
        self._mesh_id = mesh_id

        self._attr_name = name
        self._attr_unique_id = f"awoxmesh-{self._mesh_id}"
        self._attr_supported_color_modes = supported_color_modes

        self._manufacturer = manufacturer
        self._model = model
        self._firmware = firmware

        self._mesh.register_device(mesh_id, mac, name, self.status_callback)

        self._state = None
        self._attr_color_mode = ColorMode.ONOFF
        self._red = None
        self._green = None
        self._blue = None
        self._white_temperature = None
        self._white_brightness = None
        self._color_brightness = None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.unique_id)},
            name=self.name,
            manufacturer=self._manufacturer,
            model=self._model.replace('_', ' '),
            sw_version=self._firmware,
            via_device=(DOMAIN, self._mesh.identifier),
        )

    @property
    def icon(self) -> Optional[str]:
        if 'Spot' in self._model:
            return 'mdi:wall-sconce-flat'
        return None

    @property
    def available(self) -> bool:
        return self._state is not None

    @property
    def state(self) -> StateType:
        if self._state is None:
            return STATE_UNAVAILABLE
        return STATE_ON if self.is_on else STATE_OFF

    @property
    def rgb_color(self):
        return self._red, self._green, self._blue

    @property
    def color_temp(self):
        if self._white_temperature is None:
            return None
        return convert_value_to_available_range(
            self._white_temperature, 0, 0x7f, self.min_mireds, self.max_mireds
        )

    @property
    def brightness(self):
        if self.color_mode != ColorMode.RGB:
            if self._white_brightness is None:
                return None
            return convert_value_to_available_range(self._white_brightness, 1, 0x7f, 0, 255)
        if self._color_brightness is None:
            return None
        return convert_value_to_available_range(self._color_brightness, 0x0A, 0x64, 0, 255)

    @property
    def min_mireds(self):
        return 153  # 6500K

    @property
    def max_mireds(self):
        return 370  # 2700K

    @property
    def is_on(self):
        return bool(self._state)

    async def async_turn_on(self, **kwargs: Any) -> None:
        status = {}
        _LOGGER.debug("[%s] Turn on %s", self.unique_id, kwargs)

        if ATTR_RGB_COLOR in kwargs:
            rgb = kwargs[ATTR_RGB_COLOR]
            await self._mesh.async_set_color(self._mesh_id, *rgb)
            status.update({'red': rgb[0], 'green': rgb[1], 'blue': rgb[2], 'state': True})

        if ATTR_BRIGHTNESS in kwargs:
            status['state'] = True
            if self.color_mode != ColorMode.RGB:
                device_brightness = convert_value_to_available_range(kwargs[ATTR_BRIGHTNESS], 0, 255, 1, 0x7f)
                await self._mesh.async_set_white_brightness(self._mesh_id, device_brightness)
                status['white_brightness'] = device_brightness
            else:
                device_brightness = convert_value_to_available_range(kwargs[ATTR_BRIGHTNESS], 0, 255, 0x0A, 0x64)
                await self._mesh.async_set_color_brightness(self._mesh_id, device_brightness)
                status['color_brightness'] = device_brightness

        if ATTR_COLOR_TEMP in kwargs:
            device_white_temp = convert_value_to_available_range(
                kwargs[ATTR_COLOR_TEMP], self.min_mireds, self.max_mireds, 0, 0x7f
            )
            await self._mesh.async_set_white_temperature(self._mesh_id, device_white_temp)
            status.update({'state': True, 'white_temperature': device_white_temp})

        if 'state' not in status:
            await self._mesh.async_on(self._mesh_id)
            status['state'] = True

        self.status_callback(status)

    async def async_turn_off(self, **kwargs):
        _LOGGER.debug("[%s] Turn off", self.unique_id)
        await self._mesh.async_off(self._mesh_id)
        self.status_callback({'state': False})

    @callback
    def status_callback(self, status) -> None:
        self._state = status.get('state', self._state)
        self._white_brightness = status.get('white_brightness', self._white_brightness)
        self._white_temperature = status.get('white_temperature', self._white_temperature)
        self._color_brightness = status.get('color_brightness', self._color_brightness)
        self._red = status.get('red', self._red)
        self._green = status.get('green', self._green)
        self._blue = status.get('blue', self._blue)

        supported_color_modes = self.supported_color_modes
        color_mode = ColorMode.ONOFF
        if status.get('color_mode'):
            color_mode = ColorMode.RGB
        elif ColorMode.COLOR_TEMP in supported_color_modes:
            color_mode = ColorMode.COLOR_TEMP
        elif ColorMode.BRIGHTNESS in supported_color_modes:
            color_mode = ColorMode.BRIGHTNESS
        self._attr_color_mode = color_mode

        _LOGGER.debug(
            "[%s][%s] mode[%s] Status callback: %s",
            self.unique_id, self.name, self._attr_color_mode, status
        )

        self.hass.loop.call_soon_threadsafe(self.async_write_ha_state)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Update handled by status_callback, nothing here."""
        pass
