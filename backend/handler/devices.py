"""Registering the machines a user runs the client on, and keeping their names apart."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from handler.database import db_device_handler
from models.base import utc_now
from models.device import NAME_MAX_LENGTH, Device


class DeviceNotFound(Exception):
    pass


class NameTaken(Exception):
    pass


class HostnameTaken(Exception):
    """A new client uid arrived with a hostname (or name) another of the user's devices already has.
    The client has to say whether it is that machine again or a new one."""

    def __init__(self, devices: list[Device], suggested_names: list[str]):
        super().__init__("hostname taken")
        self.devices = devices
        self.suggested_names = suggested_names


def clean_name(raw: str) -> str:
    return " ".join(raw.split())[:NAME_MAX_LENGTH]


def suggest_names(base: str, os_id: str | None, taken: set[str]) -> list[str]:
    """`<host>-<os>` when the OS is known, then the first free `<host>-<n>`."""
    taken = {t.casefold() for t in taken}
    suggestions = []
    if os_id:
        named = clean_name(f"{base}-{os_id}")
        if named.casefold() not in taken:
            suggestions.append(named)
    n = 2
    while (numbered := clean_name(f"{base}-{n}")).casefold() in taken:
        n += 1
    suggestions.append(numbered)
    return suggestions


def _add(device: Device) -> Device:
    try:
        return db_device_handler.add_device(device)
    except IntegrityError as e:
        raise NameTaken(device.name) from e


def register_device(
    user_id: int,
    client_uid: str,
    hostname: str,
    platform: str | None = None,
    os_id: str | None = None,
    adopt_device_id: int | None = None,
    name: str | None = None,
) -> Device:
    """Find or create the device for this client.

    A known uid is returned as is. A new uid either takes over an existing device
    (`adopt_device_id`, e.g. after a clean reinstall), is created under `name`, or is created
    under its hostname; when that hostname is already in use the caller gets HostnameTaken
    instead of a silent duplicate."""
    hostname = clean_name(hostname)
    seen = {"hostname": hostname, "platform": platform, "last_seen": utc_now()}

    known = db_device_handler.get_by_client_uid(user_id, client_uid)
    if known is not None:
        return db_device_handler.update_device(known.id, seen)

    if adopt_device_id is not None:
        target = db_device_handler.get_device(adopt_device_id)
        if target is None or target.user_id != user_id:
            raise DeviceNotFound(adopt_device_id)
        return db_device_handler.update_device(target.id, {"client_uid": client_uid, **seen})

    devices = db_device_handler.get_for_user(user_id)
    if name:
        name = clean_name(name)
        if any(d.name.casefold() == name.casefold() for d in devices):
            raise NameTaken(name)
    else:
        name = hostname
        same = [
            d
            for d in devices
            if hostname.casefold() in ((d.hostname or "").casefold(), d.name.casefold())
        ]
        if same:
            raise HostnameTaken(same, suggest_names(hostname, os_id, {d.name for d in devices}))
    return _add(Device(user_id=user_id, client_uid=client_uid, name=name, **seen))


def rename_device(user_id: int, device_id: int, name: str) -> Device:
    device = db_device_handler.get_device(device_id)
    if device is None or device.user_id != user_id:
        raise DeviceNotFound(device_id)
    name = clean_name(name)
    clash = db_device_handler.get_by_name(user_id, name)
    if clash is not None and clash.id != device_id:
        raise NameTaken(name)
    try:
        return db_device_handler.update_device(device_id, {"name": name})
    except IntegrityError as e:
        raise NameTaken(name) from e
