"""Test BTH08 controls before their device values arrive."""

from unittest.mock import AsyncMock, Mock

from bleak.backends.device import BLEDevice
import pytest

from custom_components.tuya_ble.devices import TuyaBLECoordinator, TuyaBLEProductInfo
from custom_components.tuya_ble.number import TuyaBLENumber, mapping
from custom_components.tuya_ble.tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice


@pytest.mark.parametrize("dp_id", (10, 11, 12, 13, 36, 37, 24, 25, 26, 27, 40, 41))
@pytest.mark.parametrize("source", ("report", "write"))
async def test_missing_value_is_unknown_until_reported_or_set(hass, dp_id, source):
    """Missing DPs must not imply a minimum target or an expired countdown."""
    device = TuyaBLEDevice(
        Mock(), BLEDevice("11:22:33:44:55:66", "BTH08", {}, rssi=-50)
    )
    device._send_datapoints = AsyncMock()
    coordinator = TuyaBLECoordinator(hass, device)
    number_mapping = next(
        item for item in mapping["swtz"].products["o3nnpkat"] if item.dp_id == dp_id
    )
    entity = TuyaBLENumber(
        hass, coordinator, device, TuyaBLEProductInfo("BTH08"), number_mapping
    )
    try:
        assert entity.native_value is None
        coordinator._async_handle_connect()
        # An unrelated report cannot establish this control's setting.
        device.datapoints._update_from_device(
            3, 0, 0, TuyaBLEDataPointType.DT_VALUE, 208
        )
        assert entity.native_value is None
        value = 75 if dp_id in (10, 11, 12, 13, 36, 37) else 5
        raw = int(value * number_mapping.coefficient)
        if source == "report":
            device.datapoints._update_from_device(
                dp_id, 0, 0, TuyaBLEDataPointType.DT_VALUE, raw
            )
            device._send_datapoints.assert_not_called()
        else:
            entity.set_native_value(value)
            await hass.async_block_till_done()
            device._send_datapoints.assert_awaited_once_with([dp_id])
        assert entity.native_value == value
        assert device.datapoints[dp_id].value == raw
    finally:
        await coordinator.async_shutdown()
