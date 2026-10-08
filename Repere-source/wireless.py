"""Read-only transport checks for Repère's explicit Wi-Fi mode.

pymobiledevice3 11.20.2's UserspaceRsdTunnel selects its own usbmux
connection and prefers USB when both USB and Network are available. Its
public API does not accept a transport selector. Consequently Wi-Fi mode
requires the selected phone's cable to be unplugged before opening that
tunnel. We never toggle wireless pairing or device settings here.
"""
from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any


def validate_network_only(devices: Iterable[Any], udid: str) -> None:
    """Require a Network advertisement and no USB route to the selected phone.

    Other phones are ignored. Passing this check is discovery evidence only;
    the bridge still has to open and authenticate its Network lockdown and
    developer-service connections before reporting successful preparation.
    """
    transports = {
        device.connection_type
        for device in devices
        if device.serial == udid
    }
    if "USB" in transports:
        raise RuntimeError(
            "Pour utiliser le Wi-Fi, débranche d’abord le câble de cet iPhone, "
            "puis clique sur Actualiser et Préparer la connexion. "
            "Le Mac et l’iPhone doivent rester sur le même réseau Wi-Fi."
        )
    if "Network" not in transports:
        raise RuntimeError(
            "Cet iPhone n’est pas visible en Wi-Fi. Branche-le au Mac, puis dans "
            "Finder → iPhone → Général, coche « Afficher cet iPhone lorsqu’il "
            "est en Wi-Fi » et clique sur Appliquer. Débranche ensuite le câble, "
            "garde le Mac et l’iPhone sur le même réseau Wi-Fi, puis actualise Repère."
        )


async def ensure_network_only(udid: str, *, timeout: float = 10) -> None:
    """Discover transports without pairing or changing anything on the iPhone."""
    from pymobiledevice3.usbmux import list_devices

    devices = await asyncio.wait_for(list_devices(), timeout=timeout)
    validate_network_only(devices, udid)
