"""Test availability when a held BLE link disappears or reconnects."""

from datetime import timedelta
from unittest.mock import Mock

from bleak.backends.device import BLEDevice
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed
import pytest

from custom_components.tuya_ble.const import SET_DISCONNECTED_DELAY
from custom_components.tuya_ble.devices import TuyaBLECoordinator, TuyaBLEProductInfo
from custom_components.tuya_ble.sensor import TuyaBLESensor, mapping
from custom_components.tuya_ble.tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice


def _make_device(keep_connection: bool = True) -> TuyaBLEDevice:
    device = TuyaBLEDevice(
        Mock(),
        BLEDevice("11:22:33:44:55:66", "BTH08 test", {}, rssi=-50),
        keep_connection=keep_connection,
    )
    device._client = Mock(is_connected=True)
    device._is_paired = True
    return device


async def _advance(hass: HomeAssistant, freezer, seconds: int) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()


async def test_missing_disconnect_callback_expires_cached_temperature(
    hass: HomeAssistant, freezer
) -> None:
    """The scheduled check publishes unavailability without erasing history."""
    device = _make_device()
    coordinator = TuyaBLECoordinator(hass, device)
    probe_mapping = next(
        item for item in mapping["swtz"].products["o3nnpkat"] if item.dp_id == 28
    )
    sensor = TuyaBLESensor(
        hass, coordinator, device, TuyaBLEProductInfo("BTH08"), probe_mapping
    )
    sensor.async_write_ha_state = Mock()
    unsubscribe = coordinator.async_add_listener(sensor._handle_coordinator_update)
    try:
        device.datapoints._update_from_device(
            16, 0, 0, TuyaBLEDataPointType.DT_RAW, b"\x10"
        )
        device.datapoints._update_from_device(
            28, 0, 0, TuyaBLEDataPointType.DT_VALUE, 211
        )
        coordinator._async_handle_update([device.datapoints[28]])
        assert sensor.available
        assert sensor.native_value == 21.1

        # Transport state changes, but no coordinator disconnect callback fires.
        device._client = None
        device._is_paired = False
        await _advance(hass, freezer, 31)
        assert sensor.available  # Preserve the existing disconnect grace period.
        await _advance(hass, freezer, SET_DISCONNECTED_DELAY + 1)

        assert not sensor.available
        assert sensor.native_value == 21.1
        assert device.datapoints[28].value == 211
        sensor.async_write_ha_state.assert_called()

        device._client = Mock(is_connected=True)
        device._is_paired = True
        await _advance(hass, freezer, 31)
        assert sensor.available
    finally:
        unsubscribe()
        await coordinator.async_shutdown()


@pytest.mark.parametrize("lost_state", ("disconnected_client", "unpaired"))
async def test_link_check_detects_other_lost_link_states(
    hass: HomeAssistant, freezer, lost_state: str
) -> None:
    """A retained client object or failed pairing must not imply availability."""
    device = _make_device()
    coordinator = TuyaBLECoordinator(hass, device)
    coordinator._async_handle_connect()
    if lost_state == "disconnected_client":
        device._client.is_connected = False
    else:
        device._is_paired = False
    await coordinator.async_refresh()
    await _advance(hass, freezer, SET_DISCONNECTED_DELAY + 1)
    assert not coordinator.connected
    await coordinator.async_shutdown()


async def test_reconnect_cancels_timeout_and_next_disconnect_rearms_it(
    hass: HomeAssistant, freezer
) -> None:
    """Cancelling one timeout must not disable all later disconnect timeouts."""
    device = _make_device()
    coordinator = TuyaBLECoordinator(hass, device)
    coordinator._async_handle_connect()
    coordinator._async_handle_disconnect()
    await _advance(hass, freezer, 300)
    coordinator._async_handle_connect()
    coordinator._async_handle_disconnect()
    await _advance(hass, freezer, 301)
    assert coordinator.connected  # The first timeout was cancelled.
    await _advance(hass, freezer, 300)
    assert not coordinator.connected  # The second timeout still fires.
    await coordinator.async_shutdown()


async def test_quiet_connected_device_does_not_expire(
    hass: HomeAssistant, freezer
) -> None:
    """An unchanged temperature is not evidence of a lost BLE connection."""
    device = _make_device()
    coordinator = TuyaBLECoordinator(hass, device)
    unsubscribe = coordinator.async_add_listener(Mock())
    try:
        coordinator._async_handle_connect()
        await _advance(hass, freezer, SET_DISCONNECTED_DELAY * 2)
        assert coordinator.connected
    finally:
        unsubscribe()
        await coordinator.async_shutdown()


async def test_on_demand_mode_does_not_poll_intentional_disconnects(
    hass: HomeAssistant,
) -> None:
    """The held-link check does not change on-demand availability policy."""
    device = _make_device(keep_connection=False)
    device._last_connected_at = 1.0
    coordinator = TuyaBLECoordinator(hass, device)
    device._client = None
    device._is_paired = False
    assert coordinator.update_interval is None
    await coordinator.async_refresh()
    assert coordinator.connected
    await coordinator.async_shutdown()


async def test_shutdown_cancels_pending_disconnect_timeout(
    hass: HomeAssistant, freezer
) -> None:
    """An unloaded coordinator must not publish a later timeout update."""
    device = _make_device()
    coordinator = TuyaBLECoordinator(hass, device)
    listener = Mock()
    unsubscribe = coordinator.async_add_listener(listener)
    coordinator._async_handle_connect()
    coordinator._async_handle_disconnect()
    await coordinator.async_shutdown()
    listener.reset_mock()
    await _advance(hass, freezer, SET_DISCONNECTED_DELAY + 31)
    listener.assert_not_called()
    unsubscribe()
