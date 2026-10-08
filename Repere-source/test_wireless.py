"""Transport-selection tests without a physical iPhone or network access."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import bridge
import wireless


def device(serial, connection):
    return SimpleNamespace(serial=serial, connection_type=connection)


class NetworkSelectionTests(unittest.TestCase):
    def test_network_selected_phone_with_other_usb_phone_is_allowed(self):
        wireless.validate_network_only([
            device("mine", "Network"), device("other", "USB")
        ], "mine")

    def test_same_phone_usb_and_network_requires_unplugging(self):
        with self.assertRaisesRegex(RuntimeError, "débranche d’abord le câble"):
            wireless.validate_network_only([
                device("mine", "Network"), device("mine", "USB")
            ], "mine")

    def test_usb_only_requires_unplugging(self):
        with self.assertRaisesRegex(RuntimeError, "débranche d’abord le câble"):
            wireless.validate_network_only([device("mine", "USB")], "mine")

    def test_other_phone_network_cannot_satisfy_selection(self):
        with self.assertRaisesRegex(RuntimeError, "n’est pas visible en Wi-Fi"):
            wireless.validate_network_only([device("other", "Network")], "mine")

    def test_absent_phone_gets_finder_instructions(self):
        with self.assertRaisesRegex(RuntimeError, "Finder"):
            wireless.validate_network_only([], "mine")


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_checks_selected_device(self):
        with patch("pymobiledevice3.usbmux.list_devices", new_callable=AsyncMock,
                   return_value=[device("mine", "Network")]) as discover:
            await wireless.ensure_network_only("mine")
            discover.assert_awaited_once_with()

    async def test_discovery_timeout_propagates(self):
        async def blocked():
            await asyncio.Event().wait()
        with patch("pymobiledevice3.usbmux.list_devices", side_effect=blocked):
            with self.assertRaises(TimeoutError):
                await wireless.ensure_network_only("mine", timeout=0.01)

    async def test_discovery_error_is_not_claimed_as_network_available(self):
        with patch("pymobiledevice3.usbmux.list_devices", new_callable=AsyncMock,
                   side_effect=OSError("usbmux unavailable")):
            with self.assertRaisesRegex(OSError, "usbmux unavailable"):
                await wireless.ensure_network_only("mine")


class BridgeTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_network_lockdown_is_explicit_and_never_autopairs(self):
        lockdown = SimpleNamespace(close=AsyncMock())
        with patch("pymobiledevice3.lockdown.create_using_usbmux", new_callable=AsyncMock,
                   return_value=lockdown) as create, \
             patch.object(bridge, "data_directory", return_value=Path("/tmp/repere-test")):
            async with bridge.lockdown_for("mine", autopair=True, connection="Network") as selected:
                self.assertIs(selected, lockdown)
        self.assertEqual(create.call_args.kwargs["connection_type"], "Network")
        self.assertFalse(create.call_args.kwargs["autopair"])
        lockdown.close.assert_awaited_once()

    async def test_network_discovery_excludes_usb_and_duplicates(self):
        @asynccontextmanager
        async def connection(udid, **kwargs):
            self.assertEqual(kwargs, {"connection": "Network"})
            yield SimpleNamespace(short_info={"DeviceName": "Mon iPhone"}, product_version="18.5")
        with patch("pymobiledevice3.usbmux.list_devices", new_callable=AsyncMock, return_value=[
            device("mine", "USB"), device("mine", "Network"),
            device("mine", "Network"), device("other", "USB")
        ]), patch.object(bridge, "lockdown_for", side_effect=connection):
            records = await bridge.list_devices("Network")
        self.assertEqual(records, [{"id": "mine", "name": "Mon iPhone", "version": "18.5", "connection": "Network"}])

    async def test_network_tunnel_checks_before_and_after_and_closes(self):
        tunnel = SimpleNamespace(aopen=AsyncMock(return_value=SimpleNamespace(udid="mine")), aclose=AsyncMock())
        with patch.object(wireless, "ensure_network_only", new_callable=AsyncMock) as check, \
             patch("pymobiledevice3.remote.userspace_tunnel.UserspaceRsdTunnel", return_value=tunnel):
            async with bridge.developer_provider("mine", "18.5", object(), "Network") as provider:
                self.assertEqual(provider.udid, "mine")
        self.assertEqual(check.await_count, 2)
        tunnel.aclose.assert_awaited_once()

    async def test_network_tunnel_does_not_fall_back_to_unspecified_native_transport(self):
        from pymobiledevice3.exceptions import UserspaceTunnelUnavailableError
        tunnel = SimpleNamespace(aopen=AsyncMock(side_effect=UserspaceTunnelUnavailableError("unavailable")), aclose=AsyncMock())
        with patch.object(wireless, "ensure_network_only", new_callable=AsyncMock), \
             patch("pymobiledevice3.remote.userspace_tunnel.UserspaceRsdTunnel", return_value=tunnel), \
             patch("pymobiledevice3.remote.native_tunnel.NativeRemotedTunnel") as native:
            with self.assertRaisesRegex(RuntimeError, "Wi-Fi est indisponible"):
                async with bridge.developer_provider("mine", "18.5", object(), "Network"):
                    self.fail("Unexpected tunnel")
        native.assert_not_called()
        tunnel.aclose.assert_awaited_once()

    async def test_cable_plugged_during_setup_closes_tunnel_and_never_yields(self):
        tunnel = SimpleNamespace(aopen=AsyncMock(return_value=SimpleNamespace(udid="mine")), aclose=AsyncMock())
        with patch.object(wireless, "ensure_network_only", new_callable=AsyncMock,
                          side_effect=[None, RuntimeError("cable reconnected")]), \
             patch("pymobiledevice3.remote.userspace_tunnel.UserspaceRsdTunnel", return_value=tunnel):
            with self.assertRaisesRegex(RuntimeError, "cable reconnected"):
                async with bridge.developer_provider("mine", "18.5", object(), "Network"):
                    self.fail("Unexpected tunnel")
        tunnel.aclose.assert_awaited_once()

    async def test_pre_17_4_network_tunnel_is_rejected_before_attempt(self):
        with patch.object(wireless, "ensure_network_only", new_callable=AsyncMock), \
             patch("pymobiledevice3.remote.userspace_tunnel.UserspaceRsdTunnel") as tunnel:
            with self.assertRaisesRegex(RuntimeError, "17.4"):
                async with bridge.developer_provider("mine", "17.3.1", object(), "Network"):
                    self.fail("Unexpected tunnel")
        tunnel.assert_not_called()

    async def test_network_prepare_proves_tunnel_before_ready(self):
        @asynccontextmanager
        async def connection(*args, **kwargs):
            self.assertEqual(kwargs, {"autopair": True, "connection": "Network"})
            yield SimpleNamespace(product_version="18.5", get_developer_mode_status=AsyncMock(return_value=True))
        @asynccontextmanager
        async def provider(*args):
            self.assertEqual(args[3], "Network")
            yield object()
        with patch.object(wireless, "ensure_network_only", new_callable=AsyncMock), \
             patch.object(bridge, "lockdown_for", side_effect=connection), \
             patch.object(bridge, "developer_provider", side_effect=provider) as tunnel, \
             patch("pymobiledevice3.services.mobile_image_mounter.auto_mount", new_callable=AsyncMock), \
             patch.object(bridge, "emit") as emit:
            await bridge.prepare_device("mine", "Network")
        tunnel.assert_called_once()
        self.assertEqual(emit.call_args.args[0], "prepared")
        self.assertIn("Wi-Fi", emit.call_args.kwargs["message"])


if __name__ == "__main__":
    unittest.main()
