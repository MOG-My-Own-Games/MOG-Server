from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from models.device import CLIENT_UID_MAX_LENGTH, HOSTNAME_MAX_LENGTH, NAME_MAX_LENGTH, PLATFORM_MAX_LENGTH


def _as_utc(value: datetime) -> datetime:
    # SQLite hands timestamps back without their zone; every stored one is UTC.
    return value if value.tzinfo else value.replace(tzinfo=UTC)


UtcDatetime = Annotated[datetime, AfterValidator(_as_utc)]


class DeviceSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    hostname: str | None
    platform: str | None
    last_seen: UtcDatetime | None
    created_at: UtcDatetime


class RegisterDeviceForm(BaseModel):
    client_uid: str = Field(min_length=8, max_length=CLIENT_UID_MAX_LENGTH)
    hostname: str = Field(min_length=1, max_length=HOSTNAME_MAX_LENGTH)
    platform: str | None = Field(default=None, max_length=PLATFORM_MAX_LENGTH)
    os_id: str | None = Field(default=None, max_length=PLATFORM_MAX_LENGTH)
    # Take over this existing device instead of creating a new one (the client says "this is that machine").
    adopt_device_id: int | None = Field(default=None, ge=1)
    # Create the device under this name instead of its hostname.
    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX_LENGTH)


class DeviceUpdateForm(BaseModel):
    name: str = Field(min_length=1, max_length=NAME_MAX_LENGTH)


class SaveFileSchema(BaseModel):
    path: str
    size: int


class SaveVersionSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    game_id: int
    device_id: int
    trigger: str
    size_bytes: int
    content_hash: str
    file_count: int
    created_at: UtcDatetime


class SaveVersionDetailSchema(SaveVersionSchema):
    # Capped (see models.save_version.MANIFEST_MAX_ENTRIES); file_count is the real total.
    manifest: list[SaveFileSchema]


class DeviceSavesSchema(BaseModel):
    device: DeviceSchema
    versions: list[SaveVersionSchema]


class GameSavesSchema(BaseModel):
    devices: list[DeviceSavesSchema]
    keep_versions: int


class SaveUploadSchema(BaseModel):
    version: SaveVersionSchema
    # False when the archive matched the device's newest version and nothing was stored.
    created: bool
