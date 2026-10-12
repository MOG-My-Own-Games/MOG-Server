from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from endpoints.responses.save import DeviceSchema, UtcDatetime
from models.game_launch_settings import (
    PROTON_MAX_LENGTH,
    WINECFG_REG_MAX_LENGTH,
    WINETRICKS_MAX_VERBS,
    WINETRICKS_VERB,
)


class LaunchSettingsSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    proton: str | None
    winecfg_reg: str | None
    winetricks: list[str] | None
    updated_at: UtcDatetime


class DeviceLaunchSettingsSchema(LaunchSettingsSchema):
    device: DeviceSchema


class GameLaunchSettingsSchema(BaseModel):
    # What is set for every device of the game, and what for the asking device (None when nothing is).
    shared: LaunchSettingsSchema | None
    device: LaunchSettingsSchema | None
    # What is set for each of the user's devices that has something, for the web UI's Settings tab.
    devices: list[DeviceLaunchSettingsSchema] = []


class LaunchSettingsForm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: Literal["device", "all"]
    device_id: int | None = Field(default=None, ge=1)
    # Only the fields sent are changed; null clears one.
    proton: str | None = Field(default=None, max_length=PROTON_MAX_LENGTH)
    winecfg_reg: str | None = Field(default=None, max_length=WINECFG_REG_MAX_LENGTH)
    winetricks: list[str] | None = Field(default=None, max_length=WINETRICKS_MAX_VERBS)

    @field_validator("winetricks")
    @classmethod
    def _verbs_are_verbs(cls, verbs: list[str] | None) -> list[str] | None:
        if verbs is not None and not all(WINETRICKS_VERB.fullmatch(v) for v in verbs):
            raise ValueError("a winetricks verb is lowercase letters, digits, '_', '.' and '-'")
        return None if verbs is None else list(dict.fromkeys(verbs))  # once each, in order

    @model_validator(mode="after")
    def _device_scope_names_a_device(self) -> LaunchSettingsForm:
        if self.scope == "device" and self.device_id is None:
            raise ValueError("device_id is required for the device scope")
        return self
