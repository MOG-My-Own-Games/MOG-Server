from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from config import MOG_VERSION
from handler import changelog
from handler.auth import CurrentUser

router = APIRouter(prefix="/about", tags=["about"])


class AboutSchema(BaseModel):
    version: str
    changelog: str | None  # Markdown; "" when the version has nothing to list, null when this build carries no notes


@router.get("")
async def about(user: CurrentUser) -> AboutSchema:
    return AboutSchema(version=MOG_VERSION, changelog=changelog.read())
