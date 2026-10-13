"""Test BTH08 countdown-alarm reporting and acknowledgement."""

from unittest.mock import Mock

from bleak.backends.device import BLEDevice
import pytest

from custom_components.tuya_ble.binary_sensor import TuyaBLEBinarySensor, mapping
from custom_components.tuya_ble.devices import TuyaBLECoordinator, TuyaBLEProductInfo
from custom_components.tuya_ble.tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice


@pytest.mark.parametrize("probe", range(1, 7))
async def test_alarm_bitmap_lifecycle(hass, probe):
    """Missing data is unknown; expiry, combined alarms and acknowledgement decode."""
    device = TuyaBLEDevice(
        Mock(), BLEDevice("11:22:33:44:55:66", "BTH08", {}, rssi=-50)
    )
    device._device_info = Mock(product_id="o3nnpkat")
    coordinator = TuyaBLECoordinator(hass, device)
    entity = TuyaBLEBinarySensor(
        hass,
        coordinator,
        device,
        TuyaBLEProductInfo("BTH08"),
        mapping["swtz"].products["o3nnpkat"][probe - 1],
    )
    entity.async_write_ha_state = Mock()
    unsubscribe = coordinator.async_add_listener(entity._handle_coordinator_update)
    try:
        assert not entity.available
        coordinator._async_handle_connect()
        assert entity.available
        assert entity.is_on is None
        # Replay observed slots 4 and 6 and their combined bitmap, plus other bits.
        for mask in (0, 8, 32, 40, 1, 2, 4, 16, 63, 0):
            device.datapoints._update_from_device(
                22, 0, 0, TuyaBLEDataPointType.DT_BITMAP, bytes([mask])
            )
            coordinator._async_handle_update([device.datapoints[22]])
            assert entity.is_on == bool(mask & (1 << (probe - 1)))
        device.datapoints._update_from_device(
            22, 0, 0, TuyaBLEDataPointType.DT_BITMAP, bytes([1 << (probe - 1)])
        )
        coordinator._async_handle_update([device.datapoints[22]])
        coordinator._async_handle_disconnect()
        assert not entity.available
        assert (
            entity.is_on is True
        )  # Preserve the reading without claiming availability.
        coordinator._async_handle_connect()
        assert entity.available
        device.datapoints._update_from_device(
            22, 0, 0, TuyaBLEDataPointType.DT_BITMAP, b"\x00"
        )
        coordinator._async_handle_update([device.datapoints[22]])
        assert entity.is_on is False
    finally:
        unsubscribe()
        await coordinator.async_shutdown()
