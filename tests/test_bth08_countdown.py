"""Test BTH08 countdown minute units and state publication."""

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
    ("raw_minutes", "minutes"),
    (
        (0, 0),
        (1, 1),
        (59, 59),
        (60, 60),
        (61, 61),
        (300, 300),
        (1440, 1440),
        (46469, 46469),
    ),
)
async def test_countdown_reports_minutes(
    hass: HomeAssistant, dp_id: int, raw_minutes: int, minutes: float
) -> None:
    """Preserve raw minute reports, including unexpected values above the schema limit."""
    entity, device = _make_countdown(hass, dp_id)
    device.datapoints._update_from_device(
        dp_id, 0, 0, TuyaBLEDataPointType.DT_VALUE, raw_minutes
    )

    assert entity.native_value == pytest.approx(minutes)
    assert device.datapoints[dp_id].value == raw_minutes
    device._send_datapoints.assert_not_called()


async def test_countdown_decrements_in_minutes(hass: HomeAssistant, dp_id: int) -> None:
    """A raw decrement of one represents a whole minute, without fractional values."""
    entity, device = _make_countdown(hass, dp_id)
    device.datapoints._update_from_device(
        dp_id, 0, 0, TuyaBLEDataPointType.DT_VALUE, 46469
    )
    previous_minutes = entity.native_value
    device.datapoints._update_from_device(
        dp_id, 0, 0, TuyaBLEDataPointType.DT_VALUE, 46468
    )

    assert previous_minutes == 46469
    assert entity.native_value == 46468
    assert previous_minutes - entity.native_value == 1
    assert device.datapoints[dp_id].value == 46468


async def test_countdown_publishes_only_minute_and_availability_changes(
    hass: HomeAssistant, dp_id: int
) -> None:
    """Empty probes and unchanged minutes must not produce repeated HA writes."""
    entity, device = _make_countdown(hass, dp_id)
    entity.async_write_ha_state = Mock()
    coordinator = entity._coordinator
    unsubscribe = coordinator.async_add_listener(entity._handle_coordinator_update)
    bit = (24, 25, 26, 27, 40, 41).index(dp_id)

    def report(dp, dp_type, value):
        device.datapoints._update_from_device(dp, 0, 0, dp_type, value)
        coordinator._async_handle_update([device.datapoints[dp]])

    try:
        report(16, TuyaBLEDataPointType.DT_RAW, b"\x00")
        assert not entity.available
        entity.async_write_ha_state.reset_mock()
        for minutes in range(120, 60, -1):
            report(dp_id, TuyaBLEDataPointType.DT_VALUE, minutes)
        entity.async_write_ha_state.assert_not_called()

        report(16, TuyaBLEDataPointType.DT_RAW, bytes([1 << bit]))
        assert entity.available
        entity.async_write_ha_state.assert_called_once()
        entity.async_write_ha_state.reset_mock()
        report(dp_id, TuyaBLEDataPointType.DT_VALUE, 1)
        assert entity.native_value == 1
        entity.async_write_ha_state.assert_called_once()
        entity.async_write_ha_state.reset_mock()
        for _ in range(60):
            report(dp_id, TuyaBLEDataPointType.DT_VALUE, 1)
        # Other probes and unrelated DPs also notify all coordinator listeners.
        report(1, TuyaBLEDataPointType.DT_VALUE, 80)
        entity.async_write_ha_state.assert_not_called()
        report(dp_id, TuyaBLEDataPointType.DT_VALUE, 0)
        assert entity.native_value == 0
        entity.async_write_ha_state.assert_called_once()
        entity.async_write_ha_state.reset_mock()
        report(16, TuyaBLEDataPointType.DT_RAW, b"\x00")
        assert not entity.available
        entity.async_write_ha_state.assert_called_once()
        entity.async_write_ha_state.reset_mock()
        report(dp_id, TuyaBLEDataPointType.DT_VALUE, 500)
        entity.async_write_ha_state.assert_not_called()
    finally:
        unsubscribe()
        await coordinator.async_shutdown()


async def test_countdown_startup_requires_probe_evidence(hass, dp_id):
    """A countdown report alone does not prove an attached probe at startup."""
    entity, device = _make_countdown(hass, dp_id)
    coordinator = entity._coordinator
    coordinator._async_handle_connect()
    try:
        device.datapoints._update_from_device(
            dp_id, 0, 0, TuyaBLEDataPointType.DT_VALUE, 300
        )
        assert not entity.available
        bit = (24, 25, 26, 27, 40, 41).index(dp_id)
        temperature_dp = (2, 3, 4, 5, 28, 29)[bit]
        device.datapoints._update_from_device(
            temperature_dp, 0, 0, TuyaBLEDataPointType.DT_VALUE, 210
        )
        assert entity.available
    finally:
        await coordinator.async_shutdown()


@pytest.mark.parametrize("reported", (False, True))
@pytest.mark.parametrize(
    ("minutes", "raw_minutes"), ((0, 0), (1, 1), (5, 5), (1440, 1440))
)
async def test_countdown_writes_minutes(
    hass: HomeAssistant, dp_id: int, reported: bool, minutes: float, raw_minutes: int
) -> None:
    """Encode whole-minute writes directly, before and after a DP report."""
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
    assert datapoint.value == raw_minutes
    assert datapoint._get_value() == pack(">i", raw_minutes)
    assert entity.native_value == minutes
