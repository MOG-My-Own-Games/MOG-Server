import pytest

from handler import devices
from handler.devices import (
    DeviceNotFound,
    HostnameTaken,
    NameTaken,
    register_device,
    rename_device,
    suggest_names,
)

UID_A, UID_B, UID_C = "uid-aaaaaaaa", "uid-bbbbbbbb", "uid-cccccccc"


def test_a_new_machine_is_named_after_its_hostname(db):
    device = register_device(1, UID_A, "karasu", platform="linux")
    assert (device.name, device.hostname, device.platform) == ("karasu", "karasu", "linux")
    assert device.last_seen is not None


def test_the_same_client_is_the_same_device_and_its_details_follow(db):
    first = register_device(1, UID_A, "karasu", platform="linux")
    again = register_device(1, UID_A, "karasu-pc", platform="win32")
    assert again.id == first.id and again.name == "karasu"
    assert (again.hostname, again.platform) == ("karasu-pc", "win32")


def test_the_same_hostname_on_a_new_client_asks_instead_of_duplicating(db):
    existing = register_device(1, UID_A, "karasu", platform="linux")

    with pytest.raises(HostnameTaken) as asked:
        register_device(1, UID_B, "Karasu", os_id="fedora")

    assert [d.id for d in asked.value.devices] == [existing.id]
    assert asked.value.suggested_names == ["Karasu-fedora", "Karasu-2"]
    assert len(devices.db_device_handler.get_for_user(1)) == 1


def test_it_can_be_the_same_machine_after_a_reinstall(db):
    old = register_device(1, UID_A, "karasu")
    adopted = register_device(1, UID_B, "karasu", adopt_device_id=old.id)
    assert adopted.id == old.id
    assert register_device(1, UID_B, "karasu").id == old.id  # the new uid is now the device's
    assert devices.db_device_handler.get_by_client_uid(1, UID_A) is None


def test_it_can_be_a_new_machine_under_a_name_of_its_own(db):
    register_device(1, UID_A, "karasu")
    other = register_device(1, UID_B, "karasu", name="karasu-fedora")
    assert other.name == "karasu-fedora"
    with pytest.raises(NameTaken):
        register_device(1, UID_C, "karasu", name="KARASU-fedora")


def test_another_users_device_cannot_be_adopted_and_names_do_not_clash_across_users(db):
    theirs = register_device(2, UID_A, "karasu")
    with pytest.raises(DeviceNotFound):
        register_device(1, UID_B, "karasu", adopt_device_id=theirs.id)
    assert register_device(1, UID_B, "karasu").name == "karasu"


def test_a_renamed_device_keeps_its_hostname_apart_from_its_name(db):
    old = register_device(1, UID_A, "karasu")
    rename_device(1, old.id, "  salotto   pc ")
    assert devices.db_device_handler.get_device(old.id).name == "salotto pc"
    # its hostname still identifies it as a candidate for the same machine
    with pytest.raises(HostnameTaken):
        register_device(1, UID_B, "karasu")


def test_rename_refuses_a_name_in_use_and_a_foreign_device(db):
    a = register_device(1, UID_A, "karasu")
    register_device(1, UID_B, "karasu", name="other")
    with pytest.raises(NameTaken):
        rename_device(1, a.id, "other")
    assert rename_device(1, a.id, "karasu").name == "karasu"  # its own name is not a clash
    with pytest.raises(DeviceNotFound):
        rename_device(2, a.id, "x")


def test_suggestions_skip_names_in_use():
    assert suggest_names("pc", "fedora", {"pc", "pc-fedora", "pc-2"}) == ["pc-3"]
    assert suggest_names("pc", None, {"pc"}) == ["pc-2"]
