"""Bounded output profiles shared by the renderer, API and delivery paths."""

OUTPUT_SIDES = {"standard": 1920, "high": 3840}


def normalize_image_resolution(value: object = None) -> str:
    resolution = "standard" if value is None else str(value).strip().lower()
    if resolution not in OUTPUT_SIDES:
        raise ValueError("image_resolution must be standard or high")
    return resolution
