"""Test loading saved devices before Bluetooth discovery sees them."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
import pytest

from custom_components.tuya_ble import async_setup_entry
from custom_components.tuya_ble.const import DOMAIN
from custom_components.tuya_ble.tuya_ble import TuyaBLEDevice, TuyaBLEDeviceCredentials


ADDRESS = "DC:23:4D:31:8D:10"


@pytest.mark.parametrize("discovery", ("off", "lookup_error", "ha", "local"))
async def test_saved_device_loads_and_connects_when_seen(
    hass: HomeAssistant, discovery: str
) -> None:
    """Power-off is an entity availability state, not a failed config entry."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_ADDRESS: ADDRESS}, title="BTH08")
    entry.add_to_hass(hass)
    manager = Mock()
    manager.get_device_credentials = AsyncMock(
        return_value=TuyaBLEDeviceCredentials(
            uuid="fixture-uuid",
            local_key="0123456789abcdef",
            device_id="fixture-device",
            category="swtz",
            product_id="o3nnpkat",
            device_name="BTH08",
            product_model="BTH08",
            product_name="BTH08",
            functions=[],
            status_range=[],
        )
    )
    real_device = BLEDevice(ADDRESS, "BTH08", {"source": "proxy"}, rssi=-45)

    async def update(device: TuyaBLEDevice) -> None:
        assert device._ble_device is real_device
        device._client = Mock(is_connected=True)
        device._is_paired = True
        device._fire_connected_callbacks()

    with (
        patch(
            "custom_components.tuya_ble.bluetooth.async_ble_device_from_address",
            return_value=real_device if discovery == "ha" else None,
        ),
        patch(
            "custom_components.tuya_ble.get_device",
            AsyncMock(
                return_value=real_device if discovery == "local" else None,
                side_effect=(
                    BleakError("not available") if discovery == "lookup_error" else None
                ),
            ),
        ) as lookup,
        patch(
            "custom_components.tuya_ble.HASSTuyaBLEDeviceManager", return_value=manager
        ),
        patch.object(
            TuyaBLEDevice, "update", autospec=True, side_effect=update
        ) as initial,
        patch(
            "custom_components.tuya_ble.bluetooth.async_register_callback",
            return_value=Mock(return_value=None),
        ) as register,
        patch.object(
            hass.config_entries, "async_forward_entry_setups", AsyncMock()
        ) as forward,
    ):
        try:
            assert await async_setup_entry(hass, entry)
            await hass.async_block_till_done()
            data = hass.data[DOMAIN][entry.entry_id]
            assert data.device.category == "swtz"
            assert data.device.product_id == "o3nnpkat"
            assert data.product.name == "BTH08 Smart Meat Thermometer"
            forward.assert_awaited_once()

            if discovery in ("off", "lookup_error"):
                assert not data.coordinator.connected
                initial.assert_not_called()
                # Real proxy discovery replaces the placeholder and wakes the
                # initial update immediately, without reloading the entry.
                register.call_args.args[1](
                    SimpleNamespace(device=real_device, advertisement=None), None
                )
                await hass.async_block_till_done()
                assert data.device._ble_device is real_device

            assert data.coordinator.connected
            initial.assert_awaited_once()
            if discovery == "ha":
                lookup.assert_not_awaited()
        finally:
            await entry._async_process_on_unload(hass)
            if DOMAIN in hass.data and entry.entry_id in hass.data[DOMAIN]:
                await hass.data[DOMAIN][entry.entry_id].coordinator.async_shutdown()


async def test_offline_setup_wait_is_cancelled_on_unload(hass: HomeAssistant) -> None:
    """An entry can unload while the powered-off device is still unseen."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_ADDRESS: ADDRESS}, title="BTH08")
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.tuya_ble.bluetooth.async_ble_device_from_address",
            return_value=None,
        ),
        patch("custom_components.tuya_ble.get_device", AsyncMock(return_value=None)),
        patch.object(TuyaBLEDevice, "initialize", AsyncMock()),
        patch(
            "custom_components.tuya_ble.get_device_product_info", return_value=Mock()
        ),
        patch.object(TuyaBLEDevice, "update", AsyncMock()) as update,
        patch(
            "custom_components.tuya_ble.bluetooth.async_register_callback",
            return_value=Mock(return_value=None),
        ),
        patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()),
    ):
        assert await async_setup_entry(hass, entry)
        await hass.async_block_till_done()
        pending = tuple(entry._background_tasks)
        assert len(pending) == 1
        await entry._async_process_on_unload(hass)
        assert pending[0].cancelled()
        update.assert_not_awaited()
        await hass.data[DOMAIN][entry.entry_id].coordinator.async_shutdown()
