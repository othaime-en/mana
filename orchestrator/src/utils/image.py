"""Helpers for container image references."""

from typing import NamedTuple, Optional


class ImageReference(NamedTuple):
    """An image reference split into repository, tag and digest"""
    repository: str
    tag: Optional[str]
    digest: Optional[str]


def split_image_reference(image: str) -> ImageReference:
    """Split an image reference without mistaking a registry port for a tag"""
    name, _, digest = image.partition("@")
    last_segment = name.rsplit("/", 1)[-1]
    tag = None
    if ":" in last_segment:
        name, tag = name.rsplit(":", 1)
    return ImageReference(name, tag, digest or None)


def with_tag(image: str, tag: str) -> str:
    """Return the image reference pointing at tag, dropping any digest"""
    return f"{split_image_reference(image).repository}:{tag}"
