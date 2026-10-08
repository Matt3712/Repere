#!/usr/bin/env python3
"""Repère's private JSON-lines bridge. Requires pymobiledevice3==11.20.2.

No HTTP server, shell commands, credentials, or app-specific private APIs.
The ready/cleared events mean a developer-service request was sent, not that
Find My, Snapchat, or the physical GPS has been independently verified.
"""
from __future__ import annotations

import argparse
import asyncio
from bisect import bisect_right
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager, suppress
import fcntl
from functools import wraps
import hashlib
import inspect
import json
import logging
import math
import os
from pathlib import Path
import random
import signal
import sys
import threading
import time


def emit(kind: str, **fields) -> None:
    try:
        print(json.dumps({"kind": kind, **fields}, ensure_ascii=False), flush=True)
    except BrokenPipeError:
        # Parent may have crashed: restoration must still run.
        pass


def coordinate(value: str, limit: float) -> float:
    try:
        result = float(value.strip().replace(",", "."))
    except (ValueError, AttributeError):
        raise ValueError("Les coordonnées doivent être des nombres décimaux.") from None
    if not math.isfinite(result) or not -limit <= result <= limit:
        raise ValueError(f"Coordonnée hors limites : valeur attendue entre {-limit:g} et {limit:g}.")
    return result


def route_speed(value: str) -> float:
    try:
        speed = float(value.strip().replace(",", "."))
    except (ValueError, AttributeError):
        raise ValueError("Choisis une vitesse entre 1 et 150 km/h.") from None
    if not math.isfinite(speed) or not 1 <= speed <= 150:
        raise ValueError("Choisis une vitesse entre 1 et 150 km/h.")
    return speed


def route_speed_for_duration(total_m: float, value: str) -> float:
    try:
        duration = float(value.strip().replace(",", "."))
    except (ValueError, AttributeError):
        raise ValueError("Choisis une durée entre 1 seconde et 24 heures.") from None
    if not math.isfinite(duration) or not 1 <= duration <= 86_400:
        raise ValueError("Choisis une durée entre 1 seconde et 24 heures.")
    speed = total_m / duration * 3.6
    if not math.isfinite(speed) or speed <= 0:
        raise ValueError("Choisis deux points distincts pour utiliser une durée de trajet.")
    if speed > 150:
        minimum_seconds = math.ceil(total_m / (150 / 3.6))
        raise ValueError(f"Durée trop courte : il faudrait dépasser 150 km/h. Prévois au moins {minimum_seconds} secondes.")
    return speed


def random_radius(value: str) -> float:
    try:
        radius = float(value.strip().replace(",", "."))
    except (ValueError, AttributeError):
        raise ValueError("Choisis un rayon entre 10 et 10 000 mètres.") from None
    if not math.isfinite(radius) or not 10 <= radius <= 10_000:
        raise ValueError("Choisis un rayon entre 10 et 10 000 mètres.")
    return radius


class RoutePath:
    """A bounded, validated polyline with distances on a spherical Earth."""
    earth_radius = 6_371_008.8
    max_file_bytes = 2_000_000

    def __init__(self, points):
        if not isinstance(points, list) or not 2 <= len(points) <= 10_000:
            raise ValueError("Le trajet doit contenir entre 2 et 10 000 points.")
        self.points = []
        for point in points:
            if not isinstance(point, dict):
                raise ValueError("Chaque point doit contenir une latitude et une longitude.")
            pair = []
            for key, limit in (("latitude", 90), ("longitude", 180)):
                value = point.get(key)
                if (isinstance(value, bool) or not isinstance(value, (int, float))
                        or not -limit <= value <= limit or not math.isfinite(value)):
                    raise ValueError("Le trajet contient une coordonnée invalide.")
                pair.append(float(value))
            self.points.append(tuple(pair))
        self.distances = [0.0]
        for start, end in zip(self.points, self.points[1:]):
            self.distances.append(self.distances[-1] + self.distance(start, end))
        self.total_m = self.distances[-1]

    @classmethod
    def from_file(cls, filename):
        if not filename:
            raise ValueError("Choisis un trajet avant de le démarrer.")
        try:
            with Path(filename).open("rb") as stream:
                data = stream.read(cls.max_file_bytes + 1)
            if len(data) > cls.max_file_bytes:
                raise ValueError("Le fichier de trajet est trop volumineux.")
            points = json.loads(data)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("Le fichier de trajet est illisible ou invalide.") from exc
        return cls(points)

    @classmethod
    def distance(cls, start, end):
        phi1, phi2 = math.radians(start[0]), math.radians(end[0])
        dphi = phi2 - phi1
        dlambda = math.radians(end[1] - start[1])
        haversine = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        return 2 * cls.earth_radius * math.asin(math.sqrt(min(1.0, max(0.0, haversine))))

    def position(self, distance_m):
        if distance_m <= 0:
            return self.points[0]
        if distance_m >= self.total_m:
            return self.points[-1]
        index = bisect_right(self.distances, distance_m) - 1
        start, end = self.points[index:index + 2]
        if distance_m == self.distances[index]:
            return start
        # Walk the shortest great-circle arc, including across the date line.
        phi1, phi2 = math.radians(start[0]), math.radians(end[0])
        lambda1 = math.radians(start[1])
        delta_lambda = math.radians(end[1] - start[1])
        bearing = math.atan2(math.sin(delta_lambda) * math.cos(phi2),
                             math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(delta_lambda))
        arc = (distance_m - self.distances[index]) / self.earth_radius
        latitude = math.asin(max(-1.0, min(1.0, math.sin(phi1) * math.cos(arc) +
                                                       math.cos(phi1) * math.sin(arc) * math.cos(bearing))))
        longitude = lambda1 + math.atan2(math.sin(bearing) * math.sin(arc) * math.cos(phi1),
                                        math.cos(arc) - math.sin(phi1) * math.sin(latitude))
        return math.degrees(latitude), (math.degrees(longitude) + 180) % 360 - 180


class RandomWalk:
    """An endless chain of great-circle segments inside a spherical disk.

    Targets have uniform surface-area density in the disk. A cap of at most
    10 km is geodesically convex, so its shortest connecting arcs stay inside.
    Cartesian vectors avoid longitude discontinuities and pole singularities.
    Only the current segment is retained, regardless of session duration.
    """
    total_m = math.inf

    def __init__(self, latitude, longitude, radius_m, *, rng=None):
        self.center = (coordinate(str(latitude), 90), coordinate(str(longitude), 180))
        self.radius_m = random_radius(str(radius_m))
        self.points = [self.center]
        self.rng = rng if rng is not None else random.Random()
        phi, lam = map(math.radians, self.center)
        self.center_vector = self.vector(self.center)
        self.north = (-math.sin(phi) * math.cos(lam), -math.sin(phi) * math.sin(lam), math.cos(phi))
        self.east = (-math.sin(lam), math.cos(lam), 0.0)
        self.segment_start = self.center
        self.segment_start_m = 0.0
        self.last_distance_m = 0.0
        self._next_target()

    @staticmethod
    def vector(point):
        phi, lam = map(math.radians, point)
        return math.cos(phi) * math.cos(lam), math.cos(phi) * math.sin(lam), math.sin(phi)

    @staticmethod
    def coordinates(vector):
        x, y, z = vector
        return math.degrees(math.atan2(z, math.hypot(x, y))), math.degrees(math.atan2(y, x))

    def sample_target(self):
        # CDF = (1 - cos(theta)) / (1 - cos(radius/R)); the half-angle
        # form retains precision for the minimum 10 m radius.
        arc = 2 * math.asin(math.sqrt(self.rng.random()) *
                            math.sin(self.radius_m / (2 * RoutePath.earth_radius)))
        bearing = 2 * math.pi * self.rng.random()
        vector = tuple(math.cos(arc) * center + math.sin(arc) *
                       (math.cos(bearing) * north + math.sin(bearing) * east)
                       for center, north, east in zip(self.center_vector, self.north, self.east))
        return self.coordinates(vector)

    def _next_target(self):
        for _ in range(32):
            target = self.sample_target()
            length = RoutePath.distance(self.segment_start, target)
            if length > 1e-5:
                self.segment_end = target
                self.segment_length_m = length
                self.segment_end_m = self.segment_start_m + length
                return
        raise RuntimeError("Impossible de créer le prochain déplacement aléatoire. Relance la simulation.")

    def position(self, distance_m):
        if not math.isfinite(distance_m) or distance_m < self.last_distance_m:
            raise ValueError("La distance parcourue doit être finie et croissante.")
        # Avoid blocking restoration after an unusually long system suspension.
        # Normal one-second updates traverse only a handful of segments.
        transitions = 0
        while distance_m > self.segment_end_m:
            transitions += 1
            if transitions > 10_000:
                raise RuntimeError("Le Mac a été suspendu trop longtemps. Relance le déplacement aléatoire.")
            self.segment_start = self.segment_end
            self.segment_start_m = self.segment_end_m
            self._next_target()
        self.last_distance_m = distance_m
        fraction = (distance_m - self.segment_start_m) / self.segment_length_m
        if fraction <= 0:
            return self.segment_start
        if fraction >= 1:
            return self.segment_end
        arc = self.segment_length_m / RoutePath.earth_radius
        left = math.sin((1 - fraction) * arc) / math.sin(arc)
        right = math.sin(fraction * arc) / math.sin(arc)
        vector = tuple(left * start + right * end for start, end in
                       zip(self.vector(self.segment_start), self.vector(self.segment_end)))
        return self.coordinates(vector)


def data_directory() -> Path:
    return Path(os.environ.get("REPERE_DATA_DIR", str(Path.home() / "Library/Application Support/Repere")))


def configure_library() -> None:
    import importlib.metadata
    version = importlib.metadata.version("pymobiledevice3")
    if version != "11.20.2":
        raise RuntimeError(f"Version de pymobiledevice3 inattendue ({version}). Reconstruis l’app avec les dépendances fournies.")
    # Pinned library has no public macOS cache-location option. Keep its state
    # with the app instead of scattering pairing records in the home directory.
    import pymobiledevice3.common as common
    common._HOMEFOLDER = data_directory() / "devices"
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING, format="%(levelname)s: %(message)s")


@contextmanager
def bounded_http_requests():
    """Bound synchronous DDI requests made by the pinned library during prepare.

    The download runs inside its async mount function, so an asyncio timeout
    alone cannot interrupt it. Explicit finite request timeouts are preserved.
    """
    from requests import Session
    original = Session.request
    signature = inspect.signature(original)

    @wraps(original)
    def request_with_timeout(*args, **kwargs):
        arguments = signature.bind(*args, **kwargs)
        if arguments.arguments.get("timeout") is None:
            arguments.arguments["timeout"] = (10, 60)
        return original(*arguments.args, **arguments.kwargs)

    Session.request = request_with_timeout
    try:
        yield
    finally:
        Session.request = original


def emit_watchdog_error(message: str, error_type: str) -> None:
    """Best-effort error without waiting on stdout locks or a full pipe.

    The watchdog exits the process immediately afterward. A nonblocking raw
    write keeps that deadline effective even if the parent stopped reading.
    """
    payload = (json.dumps({"kind": "error", "message": message,
                           "error_type": error_type}, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        descriptor = sys.stdout.fileno()
        os.set_blocking(descriptor, False)
        os.write(descriptor, payload)
    except (OSError, ValueError, AttributeError):
        pass


class PrepareWatchdog:
    """Terminate only a prepare helper whose parent died or deadline passed.

    A separate thread remains runnable while synchronous network code blocks
    the asyncio loop. Simulation uses its own graceful restoration lifecycle.
    """
    def __init__(self, timeout=600, *, interval=0.5, clock=None,
                 parent_pid=None, exit_process=None):
        self.clock = clock or time.monotonic
        self.parent_pid = parent_pid or os.getppid
        self.exit_process = exit_process or os._exit
        self.parent = self.parent_pid()
        self.deadline = self.clock() + timeout
        self.interval = interval
        self.stopped = threading.Event()
        self.state_lock = threading.Lock()
        self.thread = threading.Thread(target=self._watch, name="repere-prepare-watchdog", daemon=True)

    def start(self):
        self.thread.start()

    def _watch(self):
        while not self.stopped.is_set():
            if self.parent_pid() != self.parent:
                message = "Repère a été fermé. La préparation de l’iPhone a été interrompue."
                error_type = "ParentExitedError"
            elif self.clock() >= self.deadline:
                message = "La préparation a dépassé 10 minutes et a été interrompue. Vérifie Internet et la connexion USB, puis réessaie."
                error_type = "TimeoutError"
            else:
                self.stopped.wait(min(self.interval, max(0, self.deadline - self.clock())))
                continue
            with self.state_lock:
                if self.stopped.is_set():
                    return
                emit_watchdog_error(message, error_type)
                self.exit_process(1)
            return

    def stop(self):
        with self.state_lock:
            self.stopped.set()
        if self.thread.ident is not None and self.thread is not threading.current_thread():
            self.thread.join()


@asynccontextmanager
async def lockdown_for(udid: str, *, autopair: bool = False, connection: str = "USB"):
    from pymobiledevice3.lockdown import create_using_usbmux
    lockdown = await asyncio.wait_for(create_using_usbmux(
        serial=udid, autopair=autopair and connection == "USB", connection_type=connection, pair_timeout=30,
        pairing_records_cache_folder=data_directory() / "devices",
        label="Repere",
    ), timeout=40)
    try:
        yield lockdown
    finally:
        await lockdown.close()


async def list_devices(connection: str = "USB") -> list[dict]:
    from pymobiledevice3.usbmux import list_devices as usb_devices
    result = []
    seen = set()
    for device in await asyncio.wait_for(usb_devices(), timeout=10):
        if device.connection_type != connection or device.serial in seen:
            continue
        seen.add(device.serial)
        label = "Wi-Fi" if connection == "Network" else "USB"
        record = {"id": device.serial, "name": f"iPhone {label} — à déverrouiller", "version": "inconnue", "connection": connection}
        try:
            async with lockdown_for(device.serial, connection=connection) as lockdown:
                info = lockdown.short_info
                record.update(name=info.get("DeviceName", f"iPhone {label}"), version=lockdown.product_version)
        except Exception as exc:
            hint = "Déverrouille-le et vérifie qu’il a déjà fait confiance à ce Mac en USB." if connection == "Network" else "Déverrouille-le et accepte « Faire confiance »."
            emit("log", message=f"Appareil détecté en {label} : {hint} {type(exc).__name__}")
        result.append(record)
    return result


@asynccontextmanager
async def developer_provider(udid: str, version: str, lockdown, connection: str = "USB"):
    from packaging.version import Version
    if connection == "Network":
        from wireless import ensure_network_only
        await ensure_network_only(udid)
        if Version("17.0") <= Version(version) < Version("17.4"):
            raise RuntimeError("Le mode Wi-Fi de Repère nécessite iOS 17.4 ou ultérieur. Utilise la connexion USB avec cette version d’iOS.")
    if Version(version).major < 17:
        yield lockdown
        return
    from pymobiledevice3.exceptions import UserspaceTunnelUnavailableError
    from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel
    tunnel = UserspaceRsdTunnel(serial=udid, autopair=False, remotepairing_fallback=False)
    try:
        rsd = await asyncio.wait_for(tunnel.aopen(), timeout=50)
    except UserspaceTunnelUnavailableError:
        await tunnel.aclose()
        if connection == "Network":
            raise RuntimeError("Le tunnel développeur Wi-Fi est indisponible. Vérifie le Mode développeur, le même réseau Wi-Fi et la préparation de l’iPhone, ou utilise USB.") from None
        from pymobiledevice3.remote.native_tunnel import NativeRemotedTunnel
        emit("log", message="Connexion par le tunnel développeur de macOS…")
        tunnel = NativeRemotedTunnel(serial=udid)
        try:
            rsd = await asyncio.wait_for(tunnel.aopen(), timeout=50)
        except BaseException:
            await tunnel.aclose()
            raise
    except BaseException:
        await tunnel.aclose()
        raise
    try:
        if rsd.udid != udid:
            raise RuntimeError("L’identité de l’iPhone ne correspond pas à l’appareil sélectionné.")
        if connection == "Network":
            # The public tunnel API prefers USB. Recheck after opening so a
            # cable connected during setup cannot be reported as Wi-Fi ready.
            await ensure_network_only(udid)
        yield rsd
    finally:
        await tunnel.aclose()


async def prepare_device(udid: str, connection: str = "USB") -> None:
    from packaging.version import Version
    from pymobiledevice3.exceptions import AlreadyMountedError
    from pymobiledevice3.services.mobile_image_mounter import auto_mount, CRYPTEX_IMAGE_MIN_VERSION
    if connection == "Network":
        from wireless import ensure_network_only
        await ensure_network_only(udid)
        emit("log", message="Préparation en Wi-Fi. Garde le Mac et l’iPhone sur le même réseau et Repère ouvert.")
    else:
        emit("log", message="Déverrouille l’iPhone et accepte « Faire confiance » si la demande apparaît.")
    async with lockdown_for(udid, autopair=True, connection=connection) as lockdown:
        version = lockdown.product_version
        if connection == "Network" and Version("17.0") <= Version(version) < Version("17.4"):
            raise RuntimeError("Le mode Wi-Fi de Repère nécessite iOS 17.4 ou ultérieur. Utilise la connexion USB avec cette version d’iOS.")
        if Version(version).major >= 16 and not await lockdown.get_developer_mode_status():
            # Reveal the toggle only. Activation, reboot, and final confirmation
            # remain explicit actions on the user's iPhone.
            from pymobiledevice3.services.amfi import AmfiService
            try:
                await asyncio.wait_for(AmfiService(lockdown).reveal_developer_mode_option_in_ui(), timeout=10)
                emit("log", message="Le réglage Mode développeur a été rendu visible sur l’iPhone.")
            except Exception:
                emit("log", message="Si le Mode développeur reste masqué, associe l’iPhone à Xcode sur le Mac.")
            raise RuntimeError("Active le Mode développeur dans Réglages → Confidentialité et sécurité sur l’iPhone, puis redémarre et confirme l’activation.")
        emit("log", message="Préparation des services développeur. Un téléchargement peut être nécessaire…")
        try:
            if Version(version) >= CRYPTEX_IMAGE_MIN_VERSION:
                async with developer_provider(udid, version, lockdown, connection) as provider:
                    await auto_mount(provider)
            else:
                await auto_mount(lockdown)
        except AlreadyMountedError:
            pass
        if connection == "Network":
            # Discovery alone does not prove developer services are reachable.
            # Establish and close their tunnel before claiming Wi-Fi is ready.
            async with developer_provider(udid, version, lockdown, connection):
                pass
    label = "Wi-Fi" if connection == "Network" else "USB"
    emit("prepared", message=f"Services développeur prêts en {label}. Choisis un point ou un trajet puis lance la simulation.")


class StopSignal:
    """Stop on command, stdin EOF, SIGTERM/SIGINT, or parent death.

    stdin is read on a daemon thread: unlike asyncio.to_thread, it never
    prevents process shutdown after a device disconnect.
    """
    def __init__(self):
        self.event = asyncio.Event()
        self.loop = asyncio.get_running_loop()
        self.parent = os.getppid()
        self.watch = None

    def request(self):
        if not self.loop.is_closed():
            with suppress(RuntimeError):
                self.loop.call_soon_threadsafe(self.event.set)

    def command(self, command):
        if command == "stop":
            self.event.set()

    def start(self):
        def read_commands():
            try:
                for line in sys.stdin:
                    command = line.strip()
                    self.loop.call_soon_threadsafe(self.command, command)
                    if command == "stop":
                        break
            finally:
                self.request()
        threading.Thread(target=read_commands, daemon=True).start()
        for sig in (signal.SIGTERM, signal.SIGINT):
            self.loop.add_signal_handler(sig, self.event.set)
        async def parent_watch():
            while not self.event.is_set():
                if os.getppid() != self.parent:
                    self.event.set()
                    return
                await asyncio.sleep(1)
        self.watch = asyncio.create_task(parent_watch())

    async def close(self):
        if self.watch:
            self.watch.cancel()
            with suppress(asyncio.CancelledError):
                await self.watch
        for sig in (signal.SIGTERM, signal.SIGINT):
            self.loop.remove_signal_handler(sig)


class RouteControl(StopSignal):
    """Pause the active clock exactly when a command reaches the event loop."""
    def __init__(self, *, clock=None):
        super().__init__()
        self.clock = clock or time.monotonic
        self.changed = asyncio.Event()
        self.paused = False
        self.moving_since = None
        self.active_seconds = 0.0
        self.started = False

    def start_route(self):
        self.started = True
        self.active_seconds = 0.0
        self.moving_since = None if self.paused else self.clock()

    def elapsed(self):
        return self.active_seconds + (max(0, self.clock() - self.moving_since)
                                      if self.moving_since is not None else 0)

    def command(self, command):
        if command == "pause" and not self.paused:
            self.active_seconds = self.elapsed()
            self.moving_since = None
            self.paused = True
            self.changed.set()
        elif command == "resume" and self.paused:
            self.moving_since = self.clock() if self.started else None
            self.paused = False
            self.changed.set()
        else:
            super().command(command)


async def hold_simulation(location, lat: float, lon: float, stop: asyncio.Event, disconnected=None):
    """Keep the same transport alive; clear before releasing it, even on error."""
    tasks = []
    # set may reach the device before a timeout/connection error: attempt clear
    # in all cases once set was attempted, not only after a successful reply.
    try:
        await asyncio.wait_for(location.set(lat, lon), timeout=20)
        emit("ready", message="Simulation demandée. Résultat à vérifier dans les apps.", latitude=lat, longitude=lon)
        tasks = [asyncio.create_task(stop.wait())]
        if disconnected is not None:
            tasks.append(asyncio.create_task(disconnected()))
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if len(tasks) == 2 and tasks[1] in done:
            raise ConnectionError("iPhone déconnecté. Le retour à la position réelle reste à vérifier ; reconnecte-le et utilise Restaurer.")
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await asyncio.wait_for(location.clear(), timeout=15)
        except Exception as exc:
            raise RuntimeError("La demande de restauration a échoué. Reconnecte l’iPhone et réessaie ; si nécessaire, redémarre-le. " + str(exc)) from exc
        else:
            emit("cleared", message="Demande d’arrêt envoyée. Vérifie le retour à ta position réelle sur l’iPhone.")


async def hold_route(location, route: RoutePath | RandomWalk, speed_kmh: float, control: RouteControl,
                     disconnected=None, *, tick_seconds=1.0, continuous=False):
    """Advance one shared location service, hold B, then clear on every exit.

    Stop/disconnect interrupt a pending set as well as a pause or final hold.
    A cancelled or failed set may already have reached the phone, so clear is
    required after the first attempt, including when no ready event was sent.
    """
    if control.event.is_set():
        emit("unchanged", message="Trajet annulé avant l’envoi d’une position.")
        return
    watchers = [asyncio.create_task(control.event.wait())]
    disconnected_task = asyncio.create_task(disconnected()) if disconnected else None
    if disconnected_task:
        watchers.append(disconnected_task)
    attempted = False
    distance_m = 0.0
    latitude, longitude = route.points[0]

    def check_end():
        if disconnected_task and disconnected_task.done():
            # Retrieve a possible transport exception to avoid an unobserved task.
            disconnected_task.result()
            raise ConnectionError("iPhone déconnecté. Le retour à la position réelle reste à vérifier ; reconnecte-le et utilise Restaurer.")
        return control.event.is_set()

    def progress():
        fields = dict(latitude=latitude, longitude=longitude,
                      distance_m=distance_m, paused=control.paused)
        if not continuous:
            fields["total_m"] = route.total_m
        emit("random_progress" if continuous else "route_progress", **fields)

    async def send_position():
        nonlocal attempted
        if check_end():
            return False
        attempted = True
        setting = asyncio.create_task(asyncio.wait_for(location.set(latitude, longitude), timeout=20))
        try:
            await asyncio.wait([setting, *watchers], return_when=asyncio.FIRST_COMPLETED)
            if check_end():
                return False
            await setting
            return True
        finally:
            if not setting.done():
                setting.cancel()
            await asyncio.gather(setting, return_exceptions=True)

    try:
        if not await send_position():
            return
        control.start_route()
        emit("ready", message=("Déplacement aléatoire démarré. Résultat à vérifier dans les apps." if continuous
                               else "Trajet démarré. Résultat à vérifier dans les apps."),
             latitude=latitude, longitude=longitude)
        if continuous:
            emit("random_started", latitude=latitude, longitude=longitude,
                 radius_m=route.radius_m, speed_kmh=speed_kmh,
                 message="Déplacement aléatoire dans le cercle jusqu’à l’arrêt.")
        progress()
        finished = route.total_m == 0
        if finished:
            emit("route_finished", message="Arrivée au point B. La position est maintenue jusqu’à l’arrêt.")
        while True:
            control.changed.clear()
            changed = asyncio.create_task(control.changed.wait())
            try:
                remaining = max(0, route.total_m / (speed_kmh / 3.6) - control.elapsed())
                await asyncio.wait([*watchers, changed],
                                   timeout=None if control.paused or finished else min(tick_seconds, remaining),
                                   return_when=asyncio.FIRST_COMPLETED)
            finally:
                changed.cancel()
                await asyncio.gather(changed, return_exceptions=True)
            if check_end():
                return
            target_m = min(route.total_m, control.elapsed() * speed_kmh / 3.6)
            if not finished and target_m > distance_m:
                while target_m > distance_m:
                    distance_m = target_m
                    latitude, longitude = route.position(distance_m)
                    if not await send_position():
                        return
                    # A pause may arrive between ticks or while set is pending.
                    # Send its final fractional position before acknowledging it,
                    # otherwise resume would instantly catch up that unsent time.
                    # The active clock stays intact, preserving route duration.
                    if not control.paused:
                        break
                    target_m = min(route.total_m, control.elapsed() * speed_kmh / 3.6)
                progress()
                if distance_m >= route.total_m:
                    finished = True
                    emit("route_finished", message="Arrivée au point B. La position est maintenue jusqu’à l’arrêt.")
            else:
                # Pause/resume acknowledgement carries the last sent position.
                progress()
    finally:
        for watcher in watchers:
            watcher.cancel()
        await asyncio.gather(*watchers, return_exceptions=True)
        if attempted:
            try:
                await asyncio.wait_for(location.clear(), timeout=15)
            except Exception as exc:
                raise RuntimeError("La demande de restauration a échoué. Reconnecte l’iPhone et réessaie ; si nécessaire, redémarre-le. " + str(exc)) from exc
            else:
                emit("cleared", message="Demande d’arrêt envoyée. Vérifie le retour à ta position réelle sur l’iPhone.")
        else:
            emit("unchanged", message="Trajet interrompu avant l’envoi d’une position.")


async def hold_random(location, walk: RandomWalk, speed_kmh: float, control: RouteControl,
                      disconnected=None, *, tick_seconds=1.0):
    # Share the tested set/clear, stop, disconnect, and pause lifecycle with
    # finite routes; RandomWalk supplies an endless, bounded spatial path.
    await hold_route(location, walk, speed_kmh, control, disconnected,
                     tick_seconds=tick_seconds, continuous=True)


async def location_command(args, stop: StopSignal | None) -> None:
    from packaging.version import Version
    connection = getattr(args, "connection", "USB")
    async with AsyncExitStack() as stack:
        async def connect():
            lockdown = await stack.enter_async_context(lockdown_for(args.udid, connection=connection))
            version = lockdown.product_version
            provider = await stack.enter_async_context(developer_provider(args.udid, version, lockdown, connection))
            if Version(version).major >= 17:
                from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
                from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation
                dvt = await stack.enter_async_context(DvtProvider(provider))
                location = await stack.enter_async_context(LocationSimulation(dvt))
                disconnected = dvt.dtx.wait_disconnected
            else:
                from pymobiledevice3.services.simulate_location import DtSimulateLocation
                location = DtSimulateLocation(provider)
                stack.push_async_callback(location.close)
                async def disconnected():
                    from pymobiledevice3.usbmux import list_devices as usb_devices
                    while True:
                        await asyncio.sleep(2)
                        devices = await asyncio.wait_for(usb_devices(), timeout=10)
                        if not any(d.connection_type == connection and d.serial == args.udid for d in devices):
                            return
            return location, disconnected

        setup = asyncio.create_task(asyncio.wait_for(connect(), timeout=100))
        cancelled = asyncio.create_task(stop.event.wait()) if stop else None
        try:
            if cancelled:
                done, _ = await asyncio.wait([setup, cancelled], return_when=asyncio.FIRST_COMPLETED)
                if cancelled in done:
                    setup.cancel()
                    await asyncio.gather(setup, return_exceptions=True)
                    emit("unchanged", message="Simulation annulée avant l’envoi d’une position.")
                    return
            location, disconnected = await setup
        except Exception:
            if args.action in ("simulate", "route", "random"):
                # This branch only covers transport/channel setup. Once set
                # is attempted, only clear can resolve the uncertain state.
                emit("unchanged", message="La connexion a échoué avant l’envoi d’une position. Aucune simulation n’a été demandée par cette tentative.")
            raise
        finally:
            if cancelled:
                cancelled.cancel()
                await asyncio.gather(cancelled, return_exceptions=True)
            if not setup.done():
                setup.cancel()
                await asyncio.gather(setup, return_exceptions=True)
        if args.action == "clear":
            await asyncio.wait_for(location.clear(), timeout=15)
            emit("cleared", message="Demande d’arrêt envoyée. Vérifie la position réelle dans les apps.")
        elif args.action == "route":
            await hold_route(location, args.route, args.speed_kmh, stop, disconnected)
        elif args.action == "random":
            await hold_random(location, args.random_walk, args.speed, stop, disconnected)
        else:
            await hold_simulation(location, args.lat, args.lon, stop.event, disconnected)


@asynccontextmanager
async def device_lock(udid):
    folder = data_directory() / "locks"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / (hashlib.sha256(udid.encode()).hexdigest() + ".lock")
    with path.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Une autre session Repère utilise cet iPhone. Arrête-la avant de continuer.") from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


async def run(args):
    configure_library()
    connection = getattr(args, "connection", "USB")
    if args.action == "devices":
        emit("devices", devices=await list_devices(connection))
        return
    async with device_lock(args.udid):
        if args.action == "prepare":
            watchdog = PrepareWatchdog()
            watchdog.start()
            try:
                with bounded_http_requests():
                    await asyncio.wait_for(prepare_device(args.udid, connection), timeout=600)
            finally:
                watchdog.stop()
        elif args.action == "clear":
            await asyncio.wait_for(location_command(args, None), timeout=120)
        else:
            stop = RouteControl() if args.action in ("route", "random") else StopSignal()
            stop.start()
            try:
                # Deliberately no total session timeout. Connect/set/clear have
                # their own bounds, while the active simulation can stay open.
                await location_command(args, stop)
            finally:
                await stop.close()


def error_message(exc):
    name = type(exc).__name__
    hints = {
        "NoDeviceConnectedError": "Aucun iPhone disponible. Vérifie la connexion sélectionnée (USB ou Wi-Fi), puis actualise la liste.",
        "DeviceNotFoundError": "Cet iPhone n’est plus accessible. Rétablis la connexion sélectionnée (USB ou Wi-Fi), puis actualise la liste.",
        "NotPairedError": "Ouvre l’iPhone, accepte « Faire confiance à cet ordinateur », puis clique sur Préparer.",
        "PairingDialogResponsePendingError": "Accepte « Faire confiance » sur l’iPhone puis réessaie.",
        "UserDeniedPairingError": "L’iPhone a refusé la connexion. Accepte « Faire confiance » pour continuer.",
        "PasswordRequiredError": "Déverrouille l’iPhone puis réessaie.",
        "DeveloperModeIsNotEnabledError": "Active le Mode développeur sur l’iPhone, puis redémarre-le et confirme.",
        "InvalidServiceError": "Service développeur indisponible. Active le Mode développeur et clique sur Préparer.",
        "TimeoutError": "L’iPhone ou le téléchargement ne répond pas. Vérifie la connexion USB ou Wi-Fi, le déverrouillage et Internet.",
        "PermissionError": "Accès à l’iPhone refusé par macOS. Ouvre l’app directement sur le Mac et vérifie la connexion USB ou Wi-Fi.",
    }
    return hints.get(name, str(exc) or name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["devices", "prepare", "simulate", "route", "random", "clear"])
    parser.add_argument("--udid")
    parser.add_argument("--connection", choices=["USB", "Network"], default="USB")
    parser.add_argument("--lat")
    parser.add_argument("--lon")
    parser.add_argument("--latitude")
    parser.add_argument("--longitude")
    parser.add_argument("--radius")
    parser.add_argument("--speed")
    parser.add_argument("--route-file")
    timing = parser.add_mutually_exclusive_group()
    timing.add_argument("--speed-kmh")
    timing.add_argument("--duration-seconds")
    args = parser.parse_args()
    try:
        if args.action != "devices" and (not args.udid or not args.udid.strip()):
            raise ValueError("Choisis un iPhone avant de continuer.")
        if args.action == "simulate":
            args.lat = coordinate(args.lat, 90)
            args.lon = coordinate(args.lon, 180)
        elif args.action == "route":
            args.route = RoutePath.from_file(args.route_file)
            args.speed_kmh = (route_speed_for_duration(args.route.total_m, args.duration_seconds)
                              if args.duration_seconds is not None else route_speed(args.speed_kmh))
        elif args.action == "random":
            args.latitude = coordinate(args.latitude, 90)
            args.longitude = coordinate(args.longitude, 180)
            args.radius = random_radius(args.radius)
            args.speed = route_speed(args.speed)
            args.random_walk = RandomWalk(args.latitude, args.longitude, args.radius)
        asyncio.run(run(args))
        return 0
    except Exception as exc:
        emit("error", message=error_message(exc), error_type=type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
