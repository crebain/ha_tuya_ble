"""Test BTH08 probe presence independently of cached temperatures."""

from unittest.mock import Mock

from bleak.backends.device import BLEDevice
import pytest

from custom_components.tuya_ble.devices import TuyaBLECoordinator, TuyaBLEProductInfo
from custom_components.tuya_ble.sensor import TuyaBLESensor, mapping
from custom_components.tuya_ble.tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice


@pytest.mark.parametrize("mask", (None, 0, 1, 2, 4, 8, 16, 32, 63))
async def test_presence_controls_each_probe(hass, mask):
    """Only present probes are available, even with all temperatures cached."""
    device = TuyaBLEDevice(
        Mock(), BLEDevice("11:22:33:44:55:66", "BTH08", {}, rssi=-50)
    )
    coordinator = TuyaBLECoordinator(hass, device)
    coordinator._async_handle_connect()
    try:
        if mask is not None:
            device.datapoints._update_from_device(
                16, 0, 0, TuyaBLEDataPointType.DT_RAW, bytes([mask])
            )
        for bit, dp_id in enumerate((2, 3, 4, 5, 28, 29)):
            probe_mapping = next(
                item
                for item in mapping["swtz"].products["o3nnpkat"]
                if item.dp_id == dp_id
            )
            sensor = TuyaBLESensor(
                hass, coordinator, device, TuyaBLEProductInfo("BTH08"), probe_mapping
            )
            sensor.async_write_ha_state = Mock()
            device.datapoints._update_from_device(
                dp_id, 0, 0, TuyaBLEDataPointType.DT_VALUE, 208
            )
            sensor._handle_coordinator_update()
            assert sensor.available == bool((mask or 0) & (1 << bit))
            assert sensor.native_value == 20.8

            # Unplugging clears availability immediately, retaining history.
            device.datapoints._update_from_device(
                16, 0, 0, TuyaBLEDataPointType.DT_RAW, b"\x00"
            )
            sensor._handle_coordinator_update()
            assert not sensor.available
            assert sensor.native_value == 20.8
            if mask is not None:
                device.datapoints._update_from_device(
                    16, 0, 0, TuyaBLEDataPointType.DT_RAW, bytes([mask])
                )
    finally:
        await coordinator.async_shutdown()


@pytest.mark.parametrize(
    ("dp_type", "value"),
    (
        (TuyaBLEDataPointType.DT_VALUE, 16),
        (TuyaBLEDataPointType.DT_RAW, b""),
        (TuyaBLEDataPointType.DT_RAW, b"\x10\x00"),
    ),
)
async def test_invalid_presence_is_unavailable(hass, dp_type, value):
    """Malformed or incorrectly typed presence must not enable cached readings."""
    device = TuyaBLEDevice(
        Mock(), BLEDevice("11:22:33:44:55:66", "BTH08", {}, rssi=-50)
    )
    coordinator = TuyaBLECoordinator(hass, device)
    coordinator._async_handle_connect()
    try:
        probe_mapping = next(
            item for item in mapping["swtz"].products["o3nnpkat"] if item.dp_id == 28
        )
        sensor = TuyaBLESensor(
            hass, coordinator, device, TuyaBLEProductInfo("BTH08"), probe_mapping
        )
        device.datapoints._update_from_device(16, 0, 0, dp_type, value)
        assert not sensor.available
    finally:
        await coordinator.async_shutdown()
