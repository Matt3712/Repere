"""Device-free tests for continuous movement inside a spherical disk."""
import asyncio
from contextlib import asynccontextmanager
import io
import math
import random
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import bridge


class RandomGeometryTests(unittest.TestCase):
    def test_radius_boundaries_and_invalid_values(self):
        self.assertEqual(bridge.random_radius("10"), 10)
        self.assertEqual(bridge.random_radius("10000"), 10000)
        self.assertEqual(bridge.random_radius(" 50,5 "), 50.5)
        for value in (None, True, False, "", "NaN", "Infinity", "-Infinity", "9.999", "10000.001", "-10"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                bridge.random_radius(value)

    def test_center_validation_and_distance_must_advance(self):
        for latitude, longitude in ((91, 0), (-91, 0), (0, 181), (0, -181),
                                    (float("nan"), 0), (0, float("inf")), (True, 0), (0, False), (None, 0)):
            with self.subTest(latitude=latitude, longitude=longitude), self.assertRaises(ValueError):
                bridge.RandomWalk(latitude, longitude, 100)
        walk = bridge.RandomWalk(48, 2, 100, rng=random.Random(1))
        self.assertEqual(walk.position(0), (48, 2))
        first = walk.position(1)
        self.assertEqual(walk.position(1), first)
        for distance in (-1, .5, float("nan"), float("inf")):
            with self.subTest(distance=distance), self.assertRaises(ValueError):
                walk.position(distance)

    def test_targets_and_every_intermediate_arc_stay_inside_at_poles_and_date_line(self):
        centers = [(48.8566, 2.3522), (0, 180), (0, -180), (90, 45), (-90, -135),
                   (89.99999, 179.99999), (-89.99999, -179.99999), (0, 0)]
        for center in centers:
            for radius in (10, 10000):
                with self.subTest(center=center, radius=radius):
                    walk = bridge.RandomWalk(*center, radius, rng=random.Random(12))
                    for _ in range(100):
                        target = walk.segment_end
                        self.assertLessEqual(bridge.RoutePath.distance(center, target), radius + 1e-5)
                        start_m, length = walk.segment_start_m, walk.segment_length_m
                        for fraction in (.01, .25, .5, .75, 1):
                            point = walk.position(start_m + length * fraction)
                            self.assertTrue(all(math.isfinite(value) for value in point))
                            self.assertLessEqual(bridge.RoutePath.distance(center, point), radius + 1e-5)
                        # Pass the vertex to generate the next segment.
                        walk.position(walk.segment_end_m + .00001)

    def test_targets_have_uniform_surface_area_density(self):
        walk = bridge.RandomWalk(0, 0, 10000, rng=random.Random(128))
        fractions, north, east = [], [], []
        denominator = math.sin(walk.radius_m / (2 * bridge.RoutePath.earth_radius))
        for _ in range(5000):
            point = walk.sample_target()
            distance = bridge.RoutePath.distance(walk.center, point)
            fractions.append((math.sin(distance / (2 * bridge.RoutePath.earth_radius)) / denominator) ** 2)
            vector = walk.vector(point)
            north.append(sum(a * b for a, b in zip(vector, walk.north)))
            east.append(sum(a * b for a, b in zip(vector, walk.east)))
        self.assertAlmostEqual(sum(fractions) / len(fractions), .5, delta=.02)
        self.assertLess(min(fractions), .002)
        self.assertGreater(max(fractions), .998)
        self.assertAlmostEqual(sum(north) / len(north), 0, delta=.00004)
        self.assertAlmostEqual(sum(east) / len(east), 0, delta=.00004)

    def test_continuity_and_constant_distance_away_from_turns(self):
        walk = bridge.RandomWalk(90, 170, 10, rng=random.Random(10))
        previous, turns, straight_steps = walk.position(0), 0, 0
        for distance in range(1, 2001):
            previous_segment = walk.segment_start_m
            point = walk.position(distance)
            separation = bridge.RoutePath.distance(previous, point)
            self.assertLessEqual(separation, 1 + 1e-5)
            if previous_segment == walk.segment_start_m:
                straight_steps += 1
                self.assertAlmostEqual(separation, 1, delta=1e-5)
            else:
                turns += 1
            previous = point
        self.assertGreater(turns, 100)
        self.assertGreater(straight_steps, 1000)

    def test_degenerate_rng_is_bounded_instead_of_looping_forever(self):
        with self.assertRaisesRegex(RuntimeError, "prochain déplacement"):
            bridge.RandomWalk(0, 0, 10, rng=SimpleNamespace(random=lambda: 0))

    def test_cli_validates_before_any_transport_and_preserves_connection_choice(self):
        arguments = ["bridge.py", "random", "--udid", "test-device", "--connection", "Network",
                     "--latitude", "48.5", "--longitude", "2.5", "--radius", "100", "--speed", "5"]
        with patch.object(bridge.sys, "argv", arguments), patch.object(bridge, "run", new_callable=AsyncMock) as run:
            self.assertEqual(bridge.main(), 0)
        parsed = run.call_args.args[0]
        self.assertEqual(parsed.random_walk.center, (48.5, 2.5))
        self.assertEqual(parsed.random_walk.radius_m, 100)
        self.assertEqual(parsed.speed, 5)
        self.assertEqual(parsed.connection, "Network")
        for flag, invalid in (("--latitude", "91"), ("--longitude", "181"), ("--latitude", "NaN"),
                              ("--radius", "9"), ("--radius", "10001"), ("--speed", "0"), ("--speed", "151")):
            failed = arguments.copy()
            failed[failed.index(flag) + 1] = invalid
            with self.subTest(flag=flag, invalid=invalid), patch.object(bridge.sys, "argv", failed), \
                 patch.object(bridge, "run", new_callable=AsyncMock) as run, patch.object(bridge, "emit") as emit:
                self.assertEqual(bridge.main(), 1)
                run.assert_not_awaited()
                self.assertEqual(emit.call_args.args[0], "error")


class RandomLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 0.0
        self.control = bridge.RouteControl(clock=lambda: self.now)
        self.walk = bridge.RandomWalk(48, 2, 100, rng=random.Random(14))
        self.location = SimpleNamespace(set=AsyncMock(), clear=AsyncMock())
        self.events, self.queue = [], asyncio.Queue()
        def capture(kind, **fields):
            self.events.append((kind, fields))
            self.queue.put_nowait((kind, fields))
        emitter = patch.object(bridge, "emit", side_effect=capture)
        emitter.start()
        self.addCleanup(emitter.stop)

    async def event(self, kind, predicate=lambda fields: True):
        async def wait():
            while True:
                found, fields = await self.queue.get()
                if kind == found and predicate(fields):
                    return fields
        return await asyncio.wait_for(wait(), 1)

    def launch(self, *, disconnected=None, speed=3.6):
        task = asyncio.create_task(bridge.hold_random(self.location, self.walk, speed, self.control,
                                                    disconnected, tick_seconds=.002))
        async def cleanup():
            if not task.done():
                self.control.command("stop")
            await asyncio.gather(task, return_exceptions=True)
        self.addAsyncCleanup(cleanup)
        return task

    async def test_starts_at_center_only_after_set_succeeds_and_never_emits_finished(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def set_position(*args):
            started.set()
            await release.wait()
        self.location.set.side_effect = set_position
        task = self.launch()
        await asyncio.wait_for(started.wait(), 1)
        self.assertEqual(self.events, [])
        release.set()
        fields = await self.event("random_started")
        self.assertEqual((fields["latitude"], fields["longitude"]), self.walk.center)
        self.assertEqual(fields["radius_m"], 100)
        progress = await self.event("random_progress")
        self.assertEqual(progress["distance_m"], 0)
        self.assertNotIn("total_m", progress)
        self.assertFalse(task.done())
        self.control.command("stop")
        await asyncio.wait_for(task, 1)
        self.location.clear.assert_awaited_once()
        self.assertEqual([kind for kind, _ in self.events], ["ready", "random_started", "random_progress", "cleared"])

    async def test_pause_resume_keeps_requested_speed_without_catching_up(self):
        task = self.launch()
        await self.event("random_started")
        self.now = 1
        moved = await self.event("random_progress", lambda fields: fields["distance_m"] > 0)
        self.assertEqual(moved["distance_m"], 1)
        self.control.command("pause")
        paused = await self.event("random_progress", lambda fields: fields["paused"])
        self.assertEqual(paused["distance_m"], 1)
        sends = self.location.set.await_count
        self.now = 10001
        await asyncio.sleep(.01)
        self.assertEqual(self.location.set.await_count, sends)
        self.control.command("resume")
        await self.event("random_progress", lambda fields: not fields["paused"])
        self.now = 10002
        resumed = await self.event("random_progress", lambda fields: fields["distance_m"] > 1)
        self.assertEqual(resumed["distance_m"], 2)
        for call in self.location.set.call_args_list:
            self.assertLessEqual(bridge.RoutePath.distance(self.walk.center, call.args), 100 + 1e-5)
        self.control.command("stop")
        await asyncio.wait_for(task, 1)
        self.location.clear.assert_awaited_once()

    async def test_continues_past_multiple_targets_without_finishing(self):
        self.walk = bridge.RandomWalk(0, 180, 10, rng=random.Random(3))
        task = self.launch(speed=36)
        await self.event("random_started")
        self.now = 100
        fields = await self.event("random_progress", lambda fields: fields["distance_m"] > 0)
        self.assertEqual(fields["distance_m"], 1000)
        self.now = 200
        fields = await self.event("random_progress", lambda fields: fields["distance_m"] > 1000)
        self.assertEqual(fields["distance_m"], 2000)
        self.assertNotIn("route_finished", [kind for kind, _ in self.events])
        self.assertFalse(task.done())
        self.control.command("stop")
        await asyncio.wait_for(task, 1)

    async def test_fractional_pause_sends_frozen_point_before_ack_without_resume_jump(self):
        task = self.launch()
        await self.event("random_started")
        self.now = 1
        await self.event("random_progress", lambda fields: fields["distance_m"] == 1)
        self.now = 1.75
        self.control.command("pause")
        paused = await self.event("random_progress", lambda fields: fields["paused"])
        self.assertEqual(paused["distance_m"], 1.75)
        held_position = self.location.set.call_args.args
        self.assertEqual(held_position, (paused["latitude"], paused["longitude"]))
        sends = self.location.set.await_count
        self.now = 1000
        await asyncio.sleep(.01)
        self.assertEqual(self.location.set.await_count, sends)
        self.control.command("resume")
        resumed = await self.event("random_progress", lambda fields: not fields["paused"])
        self.assertEqual(resumed["distance_m"], 1.75)
        self.assertEqual(self.location.set.await_count, sends, "Resume must not send an immediate catch-up position")
        self.now = 1001
        moved = await self.event("random_progress", lambda fields: fields["distance_m"] > 1.75)
        self.assertEqual(moved["distance_m"], 2.75)
        self.control.command("stop")
        await asyncio.wait_for(task, 1)
        self.location.clear.assert_awaited_once()

    async def test_pause_during_pending_set_finishes_fraction_before_ack(self):
        pending, release = asyncio.Event(), asyncio.Event()
        sends = 0
        async def set_position(*args):
            nonlocal sends
            sends += 1
            if sends == 2:
                pending.set()
                await release.wait()
        self.location.set.side_effect = set_position
        task = self.launch()
        await self.event("random_started")
        self.now = 1
        await asyncio.wait_for(pending.wait(), 1)
        self.now = 1.75
        self.control.command("pause")
        release.set()
        paused = await self.event("random_progress", lambda fields: fields["paused"])
        self.assertEqual(paused["distance_m"], 1.75)
        self.assertEqual(sends, 3, "Finish the fractional pause position after the in-flight set")
        self.assertEqual(self.location.set.call_args.args, (paused["latitude"], paused["longitude"]))
        self.now = 1000
        self.control.command("resume")
        resumed = await self.event("random_progress", lambda fields: not fields["paused"])
        self.assertEqual(resumed["distance_m"], 1.75)
        self.assertEqual(sends, 3)
        self.control.command("stop")
        await asyncio.wait_for(task, 1)
        self.location.clear.assert_awaited_once()

    async def test_stop_during_pending_set_still_attempts_restoration(self):
        started = asyncio.Event()
        async def pending(*args):
            started.set()
            await asyncio.Event().wait()
        self.location.set.side_effect = pending
        task = self.launch()
        await asyncio.wait_for(started.wait(), 1)
        self.control.command("stop")
        await asyncio.wait_for(task, 1)
        self.location.clear.assert_awaited_once()
        self.assertEqual([kind for kind, _ in self.events], ["cleared"])

    async def test_error_on_initial_set_attempts_clear_without_ready(self):
        self.location.set.side_effect = TimeoutError("no reply")
        with self.assertRaises(TimeoutError):
            await self.launch()
        self.location.clear.assert_awaited_once()
        self.assertEqual([kind for kind, _ in self.events], ["cleared"])

    async def test_error_mid_movement_attempts_clear(self):
        self.location.set.side_effect = [None, ConnectionError("lost")]
        task = self.launch()
        await self.event("random_started")
        self.now = 1
        with self.assertRaises(ConnectionError):
            await asyncio.wait_for(task, 1)
        self.location.clear.assert_awaited_once()
        self.assertEqual(self.events[-1][0], "cleared")

    async def test_disconnect_while_paused_attempts_clear_and_clear_failure_is_visible(self):
        disconnected = asyncio.Event()
        self.location.clear.side_effect = ConnectionError("unreachable")
        task = self.launch(disconnected=disconnected.wait)
        await self.event("random_started")
        self.control.command("pause")
        await self.event("random_progress", lambda fields: fields["paused"])
        disconnected.set()
        with self.assertRaisesRegex(RuntimeError, "restauration a échoué"):
            await asyncio.wait_for(task, 1)
        self.location.clear.assert_awaited_once()
        self.assertNotIn("cleared", [kind for kind, _ in self.events])
        self.assertNotIn("unchanged", [kind for kind, _ in self.events])

    async def test_eof_stops_movement_and_cleans_up(self):
        task = self.launch()
        await self.event("random_started")
        with patch.object(bridge.sys, "stdin", io.StringIO("pause\nresume\n")), \
             patch.object(self.control.loop, "add_signal_handler"), \
             patch.object(self.control.loop, "remove_signal_handler"):
            self.control.start()
            try:
                await asyncio.wait_for(task, 1)
            finally:
                await self.control.close()
        self.location.clear.assert_awaited_once()
        self.assertTrue(self.control.event.is_set())

    async def test_parent_death_stops_movement_and_cleans_up(self):
        task = self.launch()
        await self.event("random_started")
        with patch.object(bridge.os, "getppid", return_value=self.control.parent + 1), \
             patch.object(bridge.threading, "Thread"), \
             patch.object(self.control.loop, "add_signal_handler"), \
             patch.object(self.control.loop, "remove_signal_handler"):
            self.control.start()
            try:
                await asyncio.wait_for(task, 1)
            finally:
                await self.control.close()
        self.location.clear.assert_awaited_once()

    async def test_cancellation_clears_but_preexisting_stop_does_not_touch_phone(self):
        task = self.launch()
        await self.event("random_started")
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.location.clear.assert_awaited_once()
        self.location.set.reset_mock()
        self.location.clear.reset_mock()
        self.control.command("stop")
        await bridge.hold_random(self.location, self.walk, 3.6, self.control)
        self.location.set.assert_not_awaited()
        self.location.clear.assert_not_awaited()
        self.assertEqual(self.events[-1][0], "unchanged")

    async def test_run_uses_pause_capable_control_and_closes_it(self):
        @asynccontextmanager
        async def lock(*args):
            yield
        control = SimpleNamespace(start=Mock(), close=AsyncMock())
        with patch.object(bridge, "configure_library"), patch.object(bridge, "device_lock", side_effect=lock), \
             patch.object(bridge, "RouteControl", return_value=control) as factory, \
             patch.object(bridge, "location_command", new_callable=AsyncMock) as command:
            args = SimpleNamespace(action="random", udid="test")
            await bridge.run(args)
        factory.assert_called_once()
        command.assert_awaited_once_with(args, control)
        control.close.assert_awaited_once()

    async def test_setup_failure_is_unchanged_and_never_starts_movement(self):
        @asynccontextmanager
        async def failed_lockdown(*args, **kwargs):
            raise ConnectionError("device absent")
            yield
        with patch.object(bridge, "lockdown_for", side_effect=failed_lockdown), \
             patch.object(bridge, "hold_random", new_callable=AsyncMock) as hold:
            with self.assertRaises(ConnectionError):
                await bridge.location_command(SimpleNamespace(action="random", udid="test"), self.control)
        hold.assert_not_awaited()
        self.location.set.assert_not_awaited()
        self.assertEqual([kind for kind, _ in self.events], ["unchanged"])


if __name__ == "__main__":
    unittest.main()
