"""Test BTH08 countdown conversion between BLE seconds and HA minutes."""

from struct import pack
from unittest.mock import AsyncMock, Mock

from bleak.backends.device import BLEDevice
from homeassistant.core import HomeAssistant
import pytest

from custom_components.tuya_ble.devices import TuyaBLECoordinator, TuyaBLEProductInfo
from custom_components.tuya_ble.number import TuyaBLENumber, mapping
from custom_components.tuya_ble.tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice


pytestmark = pytest.mark.parametrize("dp_id", (24, 25, 26, 27, 40, 41))


def _make_countdown(
    hass: HomeAssistant, dp_id: int
) -> tuple[TuyaBLENumber, TuyaBLEDevice]:
    """Create a real number and datapoint store with BLE sending mocked."""
    device = TuyaBLEDevice(
        Mock(),
        BLEDevice("11:22:33:44:55:66", "BTH08 test", {}, rssi=-50),
    )
    device._send_datapoints = AsyncMock()
    countdown_mapping = next(
        item for item in mapping["swtz"].products["o3nnpkat"] if item.dp_id == dp_id
    )
    entity = TuyaBLENumber(
        hass,
        TuyaBLECoordinator(hass, device),
        device,
        TuyaBLEProductInfo("BTH08 Smart Meat Thermometer"),
        countdown_mapping,
    )
    return entity, device


@pytest.mark.parametrize(
    ("raw_seconds", "minutes"),
    ((0, 0), (60, 1), (300, 5), (46469, 774.4833333333333), (86400, 1440)),
)
async def test_countdown_reports_minutes(
    hass: HomeAssistant, dp_id: int, raw_seconds: int, minutes: float
) -> None:
    """Convert device reports, including limits and fractional minutes."""
    entity, device = _make_countdown(hass, dp_id)
    device.datapoints._update_from_device(
        dp_id, 0, 0, TuyaBLEDataPointType.DT_VALUE, raw_seconds
    )

    assert entity.native_value == pytest.approx(minutes)
    assert device.datapoints[dp_id].value == raw_seconds
    device._send_datapoints.assert_not_called()


async def test_countdown_preserves_one_second_updates(
    hass: HomeAssistant, dp_id: int
) -> None:
    """A one-second BLE decrement must not become a one-minute decrement."""
    entity, device = _make_countdown(hass, dp_id)
    device.datapoints._update_from_device(
        dp_id, 0, 0, TuyaBLEDataPointType.DT_VALUE, 46469
    )
    previous_minutes = entity.native_value
    device.datapoints._update_from_device(
        dp_id, 0, 0, TuyaBLEDataPointType.DT_VALUE, 46468
    )

    assert entity.native_value == pytest.approx(774.4666666666667)
    assert previous_minutes - entity.native_value == pytest.approx(1 / 60)


@pytest.mark.parametrize("reported", (False, True))
@pytest.mark.parametrize(
    ("minutes", "raw_seconds"), ((0, 0), (1, 60), (5, 300), (1440, 86400))
)
async def test_countdown_writes_seconds(
    hass: HomeAssistant, dp_id: int, reported: bool, minutes: float, raw_seconds: int
) -> None:
    """Encode whole-minute writes as seconds, before and after a DP report."""
    entity, device = _make_countdown(hass, dp_id)
    if reported:
        device.datapoints._update_from_device(
            dp_id, 0, 0, TuyaBLEDataPointType.DT_VALUE, 46469
        )

    entity.set_native_value(minutes)
    await hass.async_block_till_done()

    device._send_datapoints.assert_awaited_once_with([dp_id])
    datapoint = device.datapoints[dp_id]
    assert datapoint.type == TuyaBLEDataPointType.DT_VALUE
    assert datapoint.value == raw_seconds
    assert datapoint._get_value() == pack(">i", raw_seconds)
    assert entity.native_value == minutes
