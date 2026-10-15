"""The copy step: "use the files as they are" as one step of an install chain, so it can come first, ahead of patches."""

COPY_STEP = "mog:copy"


def is_copy_step(path: str | None) -> bool:
    return path == COPY_STEP
