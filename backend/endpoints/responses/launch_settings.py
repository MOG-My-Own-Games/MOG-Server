from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from endpoints.responses.save import DeviceSchema, UtcDatetime
from utils.launch_args import LAUNCH_MAX_TAGS, is_tag
from models.game_launch_settings import (
    PROTON_MAX_LENGTH,
    WINECFG_REG_MAX_LENGTH,
    WINETRICKS_MAX_VERBS,
    WINETRICKS_VERB,
    normalize_winetricks,
)


class LaunchSettingsSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    proton: str | None
    winecfg_reg: str | None
    winetricks: list[str] | None
    launch_args: list[str] | None
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
    # The launch arguments are tags. `launch_args` replaces the whole list; `launch_args_add` and `launch_args_remove` change the
    # list the server holds by now (so two editors do not undo each other), and are not sent with `launch_args`.
    launch_args: list[str] | None = Field(default=None, max_length=LAUNCH_MAX_TAGS)
    launch_args_add: list[str] | None = Field(default=None, max_length=LAUNCH_MAX_TAGS)
    launch_args_remove: list[str] | None = Field(default=None, max_length=LAUNCH_MAX_TAGS)

    @field_validator("launch_args", "launch_args_add", "launch_args_remove")
    @classmethod
    def _tags_are_tags(cls, tags: list[str] | None) -> list[str] | None:
        if tags is not None and not all(is_tag(t) for t in tags):
            raise ValueError("a launch argument tag is 1 to 200 characters, one line, with no spaces at its ends")
        return None if tags is None else list(dict.fromkeys(tags))  # once each, in order

    @model_validator(mode="after")
    def _replace_or_change(self) -> LaunchSettingsForm:
        patching = self.launch_args_add is not None or self.launch_args_remove is not None
        if patching and self.launch_args is not None:
            raise ValueError("send launch_args to replace the list, or launch_args_add and launch_args_remove to change it, not both")
        return self

    @field_validator("winetricks")
    @classmethod
    def _verbs_are_verbs(cls, verbs: list[str] | None) -> list[str] | None:
        if verbs is not None and not all(WINETRICKS_VERB.fullmatch(v) for v in verbs):
            raise ValueError(
                "a winetricks entry is a verb (lowercase letters, digits, '_', '.' and '-') or verb=value, where the value is "
                "letters, digits and '_', '.', ',', ':', '+', '-', 1 to 40 of them"
            )
        return None if verbs is None else normalize_winetricks(verbs)  # one entry for each key, the last value winning

    @model_validator(mode="after")
    def _device_scope_names_a_device(self) -> LaunchSettingsForm:
        if self.scope == "device" and self.device_id is None:
            raise ValueError("device_id is required for the device scope")
        return self
