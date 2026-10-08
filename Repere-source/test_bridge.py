"""Device-free regression tests for Repère's validation and session lifecycle.

These tests use fake transports. Passing them does not verify GPS delivery or
Find My/Snapchat behavior on a physical iPhone.
"""
import asyncio
from contextlib import asynccontextmanager
import inspect
import io
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import bridge


class PreparationTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_stops_watchdog_on_success_and_failure(self):
        @asynccontextmanager
        async def lock(*args):
            yield
        for failure in (None, RuntimeError("mount failed")):
            with self.subTest(failure=failure), \
                 patch.object(bridge, "configure_library"), \
                 patch.object(bridge, "device_lock", side_effect=lock), \
                 patch.object(bridge, "bounded_http_requests") as http, \
                 patch.object(bridge, "PrepareWatchdog") as watchdog, \
                 patch.object(bridge, "prepare_device", new_callable=AsyncMock, side_effect=failure):
                if failure is None:
                    await bridge.run(SimpleNamespace(action="prepare", udid="one"))
                else:
                    with self.assertRaisesRegex(RuntimeError, "mount failed"):
                        await bridge.run(SimpleNamespace(action="prepare", udid="one"))
                watchdog.return_value.start.assert_called_once()
                watchdog.return_value.stop.assert_called_once()
                http.return_value.__exit__.assert_called_once()

    async def test_simulation_never_starts_prepare_watchdog(self):
        @asynccontextmanager
        async def lock(*args):
            yield
        with patch.object(bridge, "configure_library"), \
             patch.object(bridge, "device_lock", side_effect=lock), \
             patch.object(bridge, "PrepareWatchdog") as watchdog, \
             patch.object(bridge, "StopSignal") as stop, \
             patch.object(bridge, "location_command", new_callable=AsyncMock):
            stop.return_value.close = AsyncMock()
            await bridge.run(SimpleNamespace(action="simulate", udid="one"))
        watchdog.assert_not_called()

    async def test_disabled_developer_mode_reveals_toggle_without_enabling_or_mounting(self):
        lockdown = SimpleNamespace(product_version="18.4", get_developer_mode_status=AsyncMock(return_value=False))
        @asynccontextmanager
        async def connection(*args, **kwargs):
            yield lockdown
        from pymobiledevice3.services.amfi import AmfiService
        with patch.object(bridge, "lockdown_for", side_effect=connection), \
             patch.object(bridge, "emit"), \
             patch.object(AmfiService, "reveal_developer_mode_option_in_ui", new_callable=AsyncMock) as reveal, \
             patch.object(AmfiService, "enable_developer_mode", new_callable=AsyncMock) as enable, \
             patch("pymobiledevice3.services.mobile_image_mounter.auto_mount", new_callable=AsyncMock) as mount:
            with self.assertRaisesRegex(RuntimeError, "Active le Mode développeur"):
                await bridge.prepare_device("test-device")
            reveal.assert_awaited_once()
            enable.assert_not_awaited()
            mount.assert_not_awaited()

    async def test_unavailable_toggle_reveal_still_explains_manual_activation(self):
        lockdown = SimpleNamespace(product_version="17.3", get_developer_mode_status=AsyncMock(return_value=False))
        @asynccontextmanager
        async def connection(*args, **kwargs):
            yield lockdown
        from pymobiledevice3.services.amfi import AmfiService
        with patch.object(bridge, "lockdown_for", side_effect=connection), \
             patch.object(bridge, "emit") as emitter, \
             patch.object(AmfiService, "reveal_developer_mode_option_in_ui", new_callable=AsyncMock, side_effect=OSError("test")):
            with self.assertRaisesRegex(RuntimeError, "Active le Mode développeur"):
                await bridge.prepare_device("test-device")
            self.assertTrue(any("Xcode" in call.kwargs.get("message", "") for call in emitter.call_args_list))


class CoordinateTests(unittest.TestCase):
    def test_decimal_formats_and_inclusive_limits(self):
        for text, limit, expected in [
            (" 48,8566 ", 90, 48.8566), ("-90", 90, -90),
            ("90", 90, 90), ("-180", 180, -180), ("180", 180, 180),
            ("0", 90, 0), ("-0", 90, 0), ("1e-3", 90, .001),
        ]:
            with self.subTest(text=text, limit=limit):
                self.assertEqual(bridge.coordinate(text, limit), expected)

    def test_non_finite_invalid_and_boolean_values_are_rejected(self):
        for value in (True, False, None, "true", "false", "NaN", "nan",
                      "inf", "-inf", "Infinity", "-Infinity", "1e999",
                      "", " ", "48,8,1", "48 degrees", {}, []):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    bridge.coordinate(value, 90)

    def test_outside_latitude_and_longitude_limits(self):
        for value, limit in (("90.000001", 90), ("-90.000001", 90),
                             ("180.000001", 180), ("-180.000001", 180)):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    bridge.coordinate(value, limit)


class RouteValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="repere-route-test-")
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "route.json"
        self.points = [{"latitude": 0, "longitude": 0}, {"latitude": 0, "longitude": .001}]

    def write(self, points):
        self.path.write_text(json.dumps(points))
        return str(self.path)

    def test_file_round_trip_bounds_and_haversine_distance(self):
        route = bridge.RoutePath.from_file(self.write(self.points))
        self.assertEqual(route.points, [(0, 0), (0, .001)])
        self.assertAlmostEqual(route.total_m, 111.195080, places=5)
        route = bridge.RoutePath([{"latitude": -90, "longitude": -180}, {"latitude": 90, "longitude": 180}])
        self.assertGreater(route.total_m, 20_000_000)
        self.assertEqual(len(bridge.RoutePath(self.points * 5000).points), 10_000)

    def test_invalid_shape_count_coordinate_and_boolean_are_rejected(self):
        for invalid in (None, {}, [], self.points[:1], self.points * 5001,
                        [[], self.points[1]], [{"latitude": 0}, self.points[1]]):
            with self.subTest(invalid=str(invalid)[:50]), self.assertRaises(ValueError):
                bridge.RoutePath(invalid)
        for value in (None, True, False, "0", float("nan"), float("inf"), -91, 91, 10 ** 1000):
            with self.subTest(value=str(value)[:20]), self.assertRaises(ValueError):
                bridge.RoutePath([{"latitude": value, "longitude": 0}, self.points[1]])
        for longitude in (-181, 181, float("nan")):
            with self.assertRaises(ValueError):
                bridge.RoutePath([{"latitude": 0, "longitude": longitude}, self.points[1]])

    def test_file_errors_and_size_limit(self):
        for content in (b"not json", b"\xff", b" " * (bridge.RoutePath.max_file_bytes + 1)):
            self.path.write_bytes(content)
            with self.assertRaises(ValueError):
                bridge.RoutePath.from_file(str(self.path))
        for filename in (None, str(self.path) + ".missing"):
            with self.assertRaises(ValueError):
                bridge.RoutePath.from_file(filename)

    def test_speed_and_duration_bounds(self):
        self.assertEqual(bridge.route_speed("1"), 1)
        self.assertEqual(bridge.route_speed("150"), 150)
        self.assertEqual(bridge.route_speed("5,5"), 5.5)
        for value in (None, True, "0", "0.9", "150.001", "NaN", "Infinity", "invalid"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                bridge.route_speed(value)
        self.assertEqual(bridge.route_speed_for_duration(10, "1"), 36)
        self.assertAlmostEqual(bridge.route_speed_for_duration(1, "86400"), 3.6 / 86400)
        for value in (None, True, "0", "0.99", "86401", "NaN", "Infinity", "invalid"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                bridge.route_speed_for_duration(100, value)
        with self.assertRaisesRegex(ValueError, "deux points distincts"):
            bridge.route_speed_for_duration(0, "60")
        with self.assertRaisesRegex(ValueError, "Durée trop courte"):
            bridge.route_speed_for_duration(1000, "1")

    def test_interpolation_follows_segments_and_short_date_line_crossing(self):
        route = bridge.RoutePath(self.points + [{"latitude": .001, "longitude": .001}])
        lat, lon = route.position(route.distances[1] / 2)
        self.assertAlmostEqual(lat, 0, places=10)
        self.assertAlmostEqual(lon, .0005, places=10)
        self.assertEqual(route.position(route.distances[1]), (0, .001))
        self.assertEqual(route.position(route.total_m), (.001, .001))
        self.assertEqual(route.position(-10), (0, 0))
        self.assertEqual(route.position(route.total_m + 10), (.001, .001))
        crossing = bridge.RoutePath([{"latitude": 0, "longitude": 179}, {"latitude": 0, "longitude": -179}])
        self.assertAlmostEqual(abs(crossing.position(crossing.total_m / 2)[1]), 180, places=8)
        duplicates = bridge.RoutePath([self.points[0], self.points[0], self.points[1], self.points[1]])
        self.assertAlmostEqual(duplicates.position(duplicates.total_m / 2)[1], .0005, places=10)

    def test_cli_validates_route_before_opening_transport_and_uses_exact_duration(self):
        filename = self.write(self.points)
        with patch.object(bridge.sys, "argv", ["bridge.py", "route", "--udid", "test", "--route-file", filename,
                                             "--duration-seconds", "60"]), \
             patch.object(bridge, "run", new_callable=AsyncMock) as run:
            self.assertEqual(bridge.main(), 0)
        args = run.call_args.args[0]
        self.assertAlmostEqual(args.route.total_m / (args.speed_kmh / 3.6), 60)
        with patch.object(bridge.sys, "argv", ["bridge.py", "route", "--udid", "test", "--route-file", filename,
                                             "--duration-seconds", "1"]), \
             patch.object(bridge, "run", new_callable=AsyncMock) as run, patch.object(bridge, "emit") as emit:
            self.assertEqual(bridge.main(), 1)
        run.assert_not_awaited()
        self.assertIn("trop courte", emit.call_args.kwargs["message"])
        with patch.object(bridge.sys, "argv", ["bridge.py", "route", "--udid", "test", "--route-file", filename,
                                             "--duration-seconds", "60", "--speed-kmh", "5"]), \
             patch.object(bridge.sys, "stderr", new=io.StringIO()), patch.object(bridge, "run", new_callable=AsyncMock) as run:
            with self.assertRaises(SystemExit) as caught:
                bridge.main()
        self.assertEqual(caught.exception.code, 2)
        run.assert_not_awaited()


class RouteLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 0.0
        self.control = bridge.RouteControl(clock=lambda: self.now)
        self.events = []
        self.queue = asyncio.Queue()
        self.route = bridge.RoutePath([{"latitude": 0, "longitude": 0}, {"latitude": 0, "longitude": .001}])
        self.location = SimpleNamespace(set=AsyncMock(), clear=AsyncMock())
        def capture(kind, **fields):
            event = kind, fields
            self.events.append(event)
            self.queue.put_nowait(event)
        self.emitter = patch.object(bridge, "emit", side_effect=capture)
        self.emitter.start()
        self.addCleanup(self.emitter.stop)

    async def event(self, kind, predicate=lambda fields: True):
        async def wait():
            while True:
                found, fields = await self.queue.get()
                if found == kind and predicate(fields):
                    return fields
        return await asyncio.wait_for(wait(), 1)

    def launch(self, *, disconnected=None, speed=3.6, route=None):
        task = asyncio.create_task(bridge.hold_route(self.location, route or self.route, speed,
                                                   self.control, disconnected, tick_seconds=.002))
        async def cleanup():
            if not task.done():
                self.control.command("stop")
            await asyncio.gather(task, return_exceptions=True)
        self.addAsyncCleanup(cleanup)
        return task

    async def test_pause_resume_excludes_pause_time_and_stop_clears(self):
        task = self.launch()
        first = await self.event("route_progress")
        self.assertEqual(first["distance_m"], 0)
        self.now = 1
        moved = await self.event("route_progress", lambda fields: fields["distance_m"] > 0)
        self.assertAlmostEqual(moved["distance_m"], 1)
        self.control.command("pause")
        paused = await self.event("route_progress", lambda fields: fields["paused"])
        self.assertEqual(paused["distance_m"], moved["distance_m"])
        sends_before_pause = self.location.set.await_count
        self.now = 3601
        await asyncio.sleep(.01)
        self.assertEqual(self.location.set.await_count, sends_before_pause)
        self.control.command("resume")
        await self.event("route_progress", lambda fields: not fields["paused"])
        self.now = 3602
        resumed = await self.event("route_progress", lambda fields: fields["distance_m"] > 1)
        self.assertAlmostEqual(resumed["distance_m"], 2)
        self.control.command("stop")
        await asyncio.wait_for(task, 1)
        self.location.clear.assert_awaited_once()
        self.assertEqual(self.events[-1][0], "cleared")

    async def test_duration_arrives_at_b_then_holds_until_stop(self):
        duration = 60.0
        task = self.launch(speed=bridge.route_speed_for_duration(self.route.total_m, str(duration)))
        await self.event("ready")
        self.now = duration / 2
        halfway = await self.event("route_progress", lambda fields: fields["distance_m"] > 0)
        self.assertAlmostEqual(halfway["distance_m"], self.route.total_m / 2)
        self.now = duration
        await self.event("route_finished")
        self.assertEqual(self.location.set.call_args.args, self.route.points[-1])
        self.assertFalse(task.done())
        self.location.clear.assert_not_awaited()
        count = self.location.set.await_count
        self.now = duration + 10000
        await asyncio.sleep(.01)
        self.assertEqual(self.location.set.await_count, count)
        self.control.command("stop")
        await asyncio.wait_for(task, 1)
        self.location.clear.assert_awaited_once()

    async def test_fractional_pause_preserves_requested_route_duration(self):
        duration = 10.0
        task = self.launch(speed=bridge.route_speed_for_duration(self.route.total_m, str(duration)))
        await self.event("ready")
        self.now = 1
        await self.event("route_progress", lambda fields: fields["distance_m"] > 0)
        self.now = 1.75
        self.control.command("pause")
        paused = await self.event("route_progress", lambda fields: fields["paused"])
        self.assertAlmostEqual(paused["distance_m"], self.route.total_m * 1.75 / duration)
        sends = self.location.set.await_count
        self.now = 1000
        self.control.command("resume")
        resumed = await self.event("route_progress", lambda fields: not fields["paused"])
        self.assertEqual(resumed["distance_m"], paused["distance_m"])
        self.assertEqual(self.location.set.await_count, sends)
        self.now = 1000 + duration - 1.75
        await self.event("route_finished")
        self.assertEqual(self.location.set.call_args.args, self.route.points[-1])
        self.assertAlmostEqual(self.control.elapsed(), duration)
        self.control.command("stop")
        await asyncio.wait_for(task, 1)
        self.location.clear.assert_awaited_once()

    async def test_fractional_duration_shortens_the_last_tick_to_meet_deadline(self):
        original_wait = asyncio.wait
        timeouts = []
        async def advance_clock(tasks, *, timeout=None, return_when=asyncio.ALL_COMPLETED):
            if timeout is not None:
                timeouts.append(timeout)
                self.now += timeout
                timeout = 0
            return await original_wait(tasks, timeout=timeout, return_when=return_when)
        speed = bridge.route_speed_for_duration(self.route.total_m, "3.2")
        with patch.object(bridge.asyncio, "wait", side_effect=advance_clock):
            task = asyncio.create_task(bridge.hold_route(self.location, self.route, speed, self.control))
            try:
                await self.event("route_finished")
                self.assertAlmostEqual(self.now, 3.2)
                self.assertEqual(len(timeouts), 4)
                self.assertAlmostEqual(timeouts[-1], .2)
                self.assertEqual(self.location.set.call_args.args, self.route.points[-1])
            finally:
                self.control.command("stop")
                await asyncio.wait_for(task, 1)

    async def test_zero_length_route_finishes_and_holds(self):
        same_point = {"latitude": 1, "longitude": 2}
        task = self.launch(route=bridge.RoutePath([same_point, same_point]))
        await self.event("route_finished")
        self.location.set.assert_awaited_once_with(1, 2)
        self.assertFalse(task.done())
        self.control.command("stop")
        await asyncio.wait_for(task, 1)
        self.location.clear.assert_awaited_once()

    async def test_pending_first_set_can_be_stopped_and_still_clears(self):
        started = asyncio.Event()
        async def set_position(*args):
            started.set()
            await asyncio.Event().wait()
        self.location.set.side_effect = set_position
        task = self.launch()
        await asyncio.wait_for(started.wait(), 1)
        self.control.command("stop")
        await asyncio.wait_for(task, 1)
        self.location.clear.assert_awaited_once()
        self.assertEqual([kind for kind, _ in self.events], ["cleared"])

    async def test_disconnect_while_paused_attempts_clear(self):
        disconnected = asyncio.Event()
        task = self.launch(disconnected=disconnected.wait)
        await self.event("ready")
        self.control.command("pause")
        await self.event("route_progress", lambda fields: fields["paused"])
        disconnected.set()
        with self.assertRaisesRegex(ConnectionError, "déconnecté"):
            await asyncio.wait_for(task, 1)
        self.location.clear.assert_awaited_once()

    async def test_disconnect_before_first_set_is_unchanged(self):
        async def already_disconnected():
            return None
        loop = asyncio.get_running_loop()
        original_create_task = asyncio.create_task
        def eager_disconnect(coroutine):
            if coroutine.cr_code is already_disconnected.__code__:
                coroutine.close()
                finished = loop.create_future()
                finished.set_result(None)
                return finished
            return original_create_task(coroutine)
        with patch.object(bridge.asyncio, "create_task", side_effect=eager_disconnect):
            with self.assertRaisesRegex(ConnectionError, "déconnecté"):
                await bridge.hold_route(self.location, self.route, 3.6, self.control, already_disconnected)
        self.location.set.assert_not_awaited()
        self.location.clear.assert_not_awaited()
        self.assertEqual([kind for kind, _ in self.events], ["unchanged"])

    async def test_mid_route_set_timeout_attempts_clear_and_never_finishes(self):
        self.location.set.side_effect = [None, TimeoutError("set timed out")]
        task = self.launch()
        await self.event("ready")
        self.now = 1
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(task, 1)
        self.location.clear.assert_awaited_once()
        self.assertNotIn("route_finished", [kind for kind, _ in self.events])
        self.assertEqual(self.events[-1][0], "cleared")

    async def test_failed_initial_set_and_failed_clear_never_claim_success(self):
        self.location.set.side_effect = TimeoutError("uncertain")
        self.location.clear.side_effect = ConnectionError("unreachable")
        with self.assertRaisesRegex(RuntimeError, "restauration a échoué"):
            await self.launch()
        self.location.clear.assert_awaited_once()
        self.assertEqual(self.events, [])

    async def test_cancellation_restores_and_preexisting_stop_is_unchanged(self):
        task = self.launch()
        await self.event("ready")
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.location.clear.assert_awaited_once()
        self.location.set.reset_mock()
        self.location.clear.reset_mock()
        self.control.command("stop")
        await bridge.hold_route(self.location, self.route, 3.6, self.control)
        self.location.set.assert_not_awaited()
        self.location.clear.assert_not_awaited()
        self.assertEqual(self.events[-1][0], "unchanged")

    async def test_control_ignores_setup_time_and_repeated_pause_commands(self):
        self.now = 100
        self.control.command("pause")
        self.now = 200
        self.control.start_route()
        self.assertEqual(self.control.elapsed(), 0)
        self.control.command("resume")
        self.now = 201
        self.control.command("pause")
        self.now = 1000
        self.control.command("pause")
        self.assertEqual(self.control.elapsed(), 1)
        self.control.command("resume")
        self.now = 1001
        self.assertEqual(self.control.elapsed(), 2)


class HttpTimeoutTests(unittest.TestCase):
    def test_default_and_explicit_request_timeouts_and_restoration(self):
        from requests import Session
        calls = []
        def original(session, method, url, params=None, data=None, headers=None,
                     cookies=None, files=None, auth=None, timeout=None, **kwargs):
            calls.append(timeout)
        with patch.object(Session, "request", original):
            with bridge.bounded_http_requests():
                Session().request("GET", "https://example.invalid")
                Session().request("GET", "https://example.invalid", timeout=None)
                Session().request("GET", "https://example.invalid", timeout=(2, 5))
                Session().request("GET", "https://example.invalid", None, None, None, None, None, None, 3)
            self.assertIs(Session.request, original)
        self.assertEqual(calls, [(10, 60), (10, 60), (2, 5), 3])

    def test_original_request_restored_after_error(self):
        from requests import Session
        original = Session.request
        with self.assertRaises(RuntimeError):
            with bridge.bounded_http_requests():
                raise RuntimeError("download failed")
        self.assertIs(Session.request, original)


class PrepareWatchdogTests(unittest.TestCase):
    def test_expiration_reports_error_before_exit_without_real_process_exit(self):
        exited = threading.Event()
        calls = []
        def terminate(code):
            calls.append(("exit", code))
            exited.set()
        def report(message, error_type):
            calls.append(("error", error_type))
        watchdog = bridge.PrepareWatchdog(timeout=0, parent_pid=lambda: 123,
                                          exit_process=terminate)
        with patch.object(bridge, "emit_watchdog_error", side_effect=report):
            watchdog.start()
            try:
                self.assertTrue(exited.wait(1))
            finally:
                watchdog.stop()
        self.assertEqual(calls, [("error", "TimeoutError"), ("exit", 1)])
        self.assertFalse(watchdog.thread.is_alive())

    def test_parent_death_reports_error_and_exits(self):
        exited = threading.Event()
        parent = [123]
        terminate = Mock(side_effect=lambda code: exited.set())
        watchdog = bridge.PrepareWatchdog(parent_pid=lambda: parent[0],
                                          exit_process=terminate, interval=0.001)
        with patch.object(bridge, "emit_watchdog_error") as report:
            watchdog.start()
            try:
                parent[0] = 1
                self.assertTrue(exited.wait(1))
            finally:
                watchdog.stop()
        terminate.assert_called_once_with(1)
        self.assertEqual(report.call_args.args[1], "ParentExitedError")
        self.assertFalse(watchdog.thread.is_alive())

    def test_stopped_watchdog_cannot_survive_or_fire_later(self):
        now = [0]
        parent = [123]
        terminate = Mock()
        watchdog = bridge.PrepareWatchdog(timeout=600, clock=lambda: now[0],
                                          parent_pid=lambda: parent[0], exit_process=terminate)
        with patch.object(bridge, "emit_watchdog_error") as report:
            watchdog.start()
            watchdog.stop()
            now[0], parent[0] = 1000, 1
            watchdog.stop()  # repeated cleanup is harmless
        self.assertFalse(watchdog.thread.is_alive())
        terminate.assert_not_called()
        report.assert_not_called()


class HoldSimulationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.events = []
        self.ready = asyncio.Event()
        def capture(kind, **fields):
            self.events.append((kind, fields))
            if kind == "ready":
                self.ready.set()
        self.emitter = patch.object(bridge, "emit", side_effect=capture)
        self.emitter.start()
        self.addCleanup(self.emitter.stop)

    def kinds(self):
        return [kind for kind, _ in self.events]

    async def test_ready_only_after_set_completes_and_stop_clears_same_object(self):
        set_started, allow_set, stop = asyncio.Event(), asyncio.Event(), asyncio.Event()
        order = []
        async def set_position(lat, lon):
            order.append(("set", lat, lon))
            set_started.set()
            await allow_set.wait()
            order.append(("set-returned",))
        async def clear():
            order.append(("clear",))
        location = SimpleNamespace(set=AsyncMock(side_effect=set_position), clear=AsyncMock(side_effect=clear))
        task = asyncio.create_task(bridge.hold_simulation(location, 48.0, 2.0, stop))
        try:
            await asyncio.wait_for(set_started.wait(), 1)
            self.assertNotIn("ready", self.kinds())
            location.clear.assert_not_awaited()
            allow_set.set()
            await asyncio.wait_for(self.ready.wait(), 1)
            self.assertFalse(task.done())
            self.assertNotIn("cleared", self.kinds())
            stop.set()
            await asyncio.wait_for(task, 1)
        finally:
            if not task.done():
                allow_set.set()
                stop.set()
                await asyncio.wait_for(task, 1)
        location.set.assert_awaited_once_with(48.0, 2.0)
        location.clear.assert_awaited_once_with()
        self.assertEqual(order, [("set", 48.0, 2.0), ("set-returned",), ("clear",)])
        self.assertEqual(self.kinds(), ["ready", "cleared"])

    async def test_set_failure_attempts_clear_without_announcing_ready(self):
        for error in (ConnectionError("set rejected"), TimeoutError("no reply")):
            with self.subTest(error=type(error).__name__):
                self.events.clear()
                location = SimpleNamespace(set=AsyncMock(side_effect=error), clear=AsyncMock())
                with self.assertRaises(type(error)):
                    await bridge.hold_simulation(location, 1, 2, asyncio.Event())
                location.clear.assert_awaited_once_with()
                self.assertNotIn("ready", self.kinds())
                self.assertEqual(self.kinds(), ["cleared"])

    async def test_clear_failure_never_announces_cleared(self):
        stop = asyncio.Event()
        stop.set()
        location = SimpleNamespace(set=AsyncMock(), clear=AsyncMock(side_effect=ConnectionError("gone")))
        with self.assertRaisesRegex(RuntimeError, "restauration a échoué"):
            await bridge.hold_simulation(location, 1, 2, stop)
        self.assertEqual(self.kinds(), ["ready"])

    async def test_disconnect_ends_session_with_error_and_attempts_restoration(self):
        disconnected = asyncio.Event()
        location = SimpleNamespace(set=AsyncMock(), clear=AsyncMock())
        task = asyncio.create_task(bridge.hold_simulation(location, 1, 2, asyncio.Event(), disconnected.wait))
        await asyncio.wait_for(self.ready.wait(), 1)
        disconnected.set()
        with self.assertRaisesRegex(ConnectionError, "déconnecté"):
            await asyncio.wait_for(task, 1)
        location.clear.assert_awaited_once_with()

    async def test_disconnect_with_failed_restoration_never_announces_cleared(self):
        async def disconnected():
            return None
        location = SimpleNamespace(set=AsyncMock(), clear=AsyncMock(side_effect=ConnectionError("gone")))
        with self.assertRaisesRegex(RuntimeError, "restauration a échoué"):
            await bridge.hold_simulation(location, 1, 2, asyncio.Event(), disconnected)
        self.assertEqual(self.kinds(), ["ready"])

    async def test_cancellation_still_clears(self):
        location = SimpleNamespace(set=AsyncMock(), clear=AsyncMock())
        task = asyncio.create_task(bridge.hold_simulation(location, 1, 2, asyncio.Event()))
        await asyncio.wait_for(self.ready.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        location.clear.assert_awaited_once_with()
        self.assertIn("cleared", self.kinds())

    async def test_set_and_clear_both_fail_no_success_events(self):
        location = SimpleNamespace(set=AsyncMock(side_effect=TimeoutError("set")),
                                   clear=AsyncMock(side_effect=ConnectionError("clear")))
        with self.assertRaisesRegex(RuntimeError, "restauration a échoué"):
            await bridge.hold_simulation(location, 1, 2, asyncio.Event())
        self.assertEqual(self.events, [])


class DeviceLockTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="repere-lock-test-")
        self.addCleanup(self.temporary.cleanup)
        self.environment = patch.dict(os.environ, {"REPERE_DATA_DIR": self.temporary.name})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    async def test_same_device_is_exclusive_and_lock_releases(self):
        async with bridge.device_lock("device-a"):
            with self.assertRaisesRegex(RuntimeError, "autre session"):
                async with bridge.device_lock("device-a"):
                    self.fail("A second session acquired the same device")
        async with bridge.device_lock("device-a"):
            pass

    async def test_different_devices_do_not_block_each_other(self):
        async with bridge.device_lock("device-a"):
            async with bridge.device_lock("device-b"):
                pass

    async def test_exception_releases_lock(self):
        with self.assertRaises(ValueError):
            async with bridge.device_lock("device-a"):
                raise ValueError("operation failed")
        async with bridge.device_lock("device-a"):
            pass

    async def test_udid_never_becomes_a_filesystem_path(self):
        async with bridge.device_lock("../arbitrary/device"):
            files = list((Path(self.temporary.name) / "locks").iterdir())
            self.assertEqual(len(files), 1)
            self.assertRegex(files[0].name, r"^[0-9a-f]{64}\.lock$")


class LocationCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_interrupts_pending_setup_without_setting_location(self):
        started = asyncio.Event()
        setup_cancelled = asyncio.Event()
        stop = SimpleNamespace(event=asyncio.Event())

        @asynccontextmanager
        async def pending_lockdown(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
                yield None
            finally:
                setup_cancelled.set()

        with patch.object(bridge, "lockdown_for", side_effect=pending_lockdown), \
             patch.object(bridge, "hold_simulation", new_callable=AsyncMock) as hold, \
             patch.object(bridge, "emit") as emit:
            task = asyncio.create_task(bridge.location_command(
                SimpleNamespace(action="simulate", udid="one", lat=1, lon=2), stop))
            await asyncio.wait_for(started.wait(), 1)
            stop.event.set()
            await asyncio.wait_for(task, 1)
        self.assertTrue(setup_cancelled.is_set())
        hold.assert_not_awaited()
        self.assertEqual([call.args[0] for call in emit.call_args_list], ["unchanged"])
        self.assertIn("avant", emit.call_args.kwargs["message"])

    async def test_setup_failure_emits_unchanged_and_propagates_without_set(self):
        @asynccontextmanager
        async def failed_lockdown(*args, **kwargs):
            raise ConnectionError("missing developer service")
            yield  # mark this as an async context manager
        with patch.object(bridge, "lockdown_for", side_effect=failed_lockdown), \
             patch.object(bridge, "hold_simulation", new_callable=AsyncMock) as hold, \
             patch.object(bridge, "emit") as emitter:
            with self.assertRaisesRegex(ConnectionError, "missing developer service"):
                await bridge.location_command(SimpleNamespace(action="simulate", udid="one", lat=1, lon=2),
                                              SimpleNamespace(event=asyncio.Event()))
        hold.assert_not_awaited()
        self.assertEqual([call.args[0] for call in emitter.call_args_list], ["unchanged"])

    async def test_clear_setup_failure_never_claims_unchanged(self):
        @asynccontextmanager
        async def failed_lockdown(*args, **kwargs):
            raise ConnectionError("device absent")
            yield
        with patch.object(bridge, "lockdown_for", side_effect=failed_lockdown), \
             patch.object(bridge, "emit") as emitter:
            with self.assertRaises(ConnectionError):
                await bridge.location_command(SimpleNamespace(action="clear", udid="one"), None)
        emitter.assert_not_called()

    async def test_set_timeout_and_failed_clear_never_claim_unchanged(self):
        @asynccontextmanager
        async def context(value):
            yield value
        async def disconnect():
            await asyncio.Event().wait()
        lockdown = SimpleNamespace(product_version="18.4")
        dvt = SimpleNamespace(dtx=SimpleNamespace(wait_disconnected=disconnect))
        location = SimpleNamespace(set=AsyncMock(side_effect=TimeoutError("set outcome uncertain")),
                                   clear=AsyncMock(side_effect=ConnectionError("cannot clear")))
        from pymobiledevice3.services.dvt.instruments import dvt_provider, location_simulation
        with patch.object(bridge, "lockdown_for", side_effect=lambda *a, **k: context(lockdown)), \
             patch.object(bridge, "developer_provider", side_effect=lambda *a: context(object())), \
             patch.object(dvt_provider, "DvtProvider", side_effect=lambda p: context(dvt)), \
             patch.object(location_simulation, "LocationSimulation", side_effect=lambda p: context(location)), \
             patch.object(bridge, "emit") as emitter:
            with self.assertRaisesRegex(RuntimeError, "restauration a échoué"):
                await bridge.location_command(SimpleNamespace(action="simulate", udid="one", lat=1, lon=2),
                                              SimpleNamespace(event=asyncio.Event()))
        location.set.assert_awaited_once()
        location.clear.assert_awaited_once()
        self.assertNotIn("unchanged", [call.args[0] for call in emitter.call_args_list])
        self.assertNotIn("cleared", [call.args[0] for call in emitter.call_args_list])

    async def test_dvt_set_and_clear_share_transport_until_cleanup(self):
        order = []
        stop = SimpleNamespace(event=asyncio.Event())
        async def disconnect():
            await asyncio.Event().wait()
        lockdown = SimpleNamespace(product_version="18.4")
        provider = object()
        dvt = SimpleNamespace(dtx=SimpleNamespace(wait_disconnected=disconnect))
        async def set_position(lat, lon):
            order.append("set")
            stop.event.set()
        location = SimpleNamespace(set=AsyncMock(side_effect=set_position), clear=AsyncMock(side_effect=lambda: order.append("clear")))

        @asynccontextmanager
        async def context(value, name):
            order.append(name + "-open")
            try:
                yield value
            finally:
                order.append(name + "-close")

        from pymobiledevice3.services.dvt.instruments import dvt_provider, location_simulation
        with patch.object(bridge, "lockdown_for", side_effect=lambda *a, **k: context(lockdown, "lockdown")), \
             patch.object(bridge, "developer_provider", side_effect=lambda *a: context(provider, "provider")), \
             patch.object(dvt_provider, "DvtProvider", side_effect=lambda p: context(dvt, "dvt")) as dvt_factory, \
             patch.object(location_simulation, "LocationSimulation", side_effect=lambda p: context(location, "location")) as location_factory, \
             patch.object(bridge, "emit"):
            await bridge.location_command(SimpleNamespace(action="simulate", udid="one", lat=1, lon=2), stop)
        dvt_factory.assert_called_once_with(provider)
        location_factory.assert_called_once_with(dvt)
        self.assertEqual(order, ["lockdown-open", "provider-open", "dvt-open", "location-open", "set", "clear",
                                 "location-close", "dvt-close", "provider-close", "lockdown-close"])


class DeveloperProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_older_ios_yields_original_lockdown(self):
        lockdown = object()
        async with bridge.developer_provider("one", "16.7.10", lockdown) as result:
            self.assertIs(result, lockdown)

    async def test_userspace_identity_mismatch_is_rejected_and_closed(self):
        from pymobiledevice3.remote import userspace_tunnel
        tunnel = SimpleNamespace(aopen=AsyncMock(return_value=SimpleNamespace(udid="wrong")), aclose=AsyncMock())
        with patch.object(userspace_tunnel, "UserspaceRsdTunnel", return_value=tunnel):
            with self.assertRaisesRegex(RuntimeError, "identité"):
                async with bridge.developer_provider("one", "18.4", object()):
                    self.fail("Mismatched device accepted")
        tunnel.aclose.assert_awaited_once()

    async def test_unavailable_userspace_falls_back_and_closes_both_handles(self):
        from pymobiledevice3.exceptions import UserspaceTunnelUnavailableError
        from pymobiledevice3.remote import userspace_tunnel, native_tunnel
        userspace = SimpleNamespace(aopen=AsyncMock(side_effect=UserspaceTunnelUnavailableError("old iOS")), aclose=AsyncMock())
        rsd = SimpleNamespace(udid="one")
        native = SimpleNamespace(aopen=AsyncMock(return_value=rsd), aclose=AsyncMock())
        with patch.object(userspace_tunnel, "UserspaceRsdTunnel", return_value=userspace), \
             patch.object(native_tunnel, "NativeRemotedTunnel", return_value=native) as factory, \
             patch.object(bridge, "emit"):
            async with bridge.developer_provider("one", "17.2", object()) as result:
                self.assertIs(result, rsd)
        factory.assert_called_once_with(serial="one")
        userspace.aclose.assert_awaited_once()
        native.aclose.assert_awaited_once()


class PinnedLibraryContractTests(unittest.TestCase):
    def test_installed_api_signatures_without_opening_device(self):
        from importlib.metadata import version
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.dtx import DTXConnection
        from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel
        from pymobiledevice3.remote.native_tunnel import NativeRemotedTunnel
        from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation
        from pymobiledevice3.services.mobile_image_mounter import auto_mount, CRYPTEX_IMAGE_MIN_VERSION
        self.assertEqual(version("pymobiledevice3"), "11.20.2")
        for parameter in ("serial", "autopair", "connection_type", "pair_timeout", "pairing_records_cache_folder", "label"):
            self.assertIn(parameter, inspect.signature(create_using_usbmux).parameters)
        self.assertIn("remotepairing_fallback", inspect.signature(UserspaceRsdTunnel).parameters)
        self.assertIn("serial", inspect.signature(NativeRemotedTunnel).parameters)
        for method in (DTXConnection.wait_disconnected, LocationSimulation.set, LocationSimulation.clear, auto_mount):
            self.assertTrue(inspect.iscoroutinefunction(method))
        self.assertIsNotNone(CRYPTEX_IMAGE_MIN_VERSION)


if __name__ == "__main__":
    unittest.main()
