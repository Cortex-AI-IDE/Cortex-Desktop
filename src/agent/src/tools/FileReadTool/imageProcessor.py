"""Minimal image processor compatibility layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class ResizeOptions:
    width: Optional[int] = None
    height: Optional[int] = None
    fit: str = "inside"


@dataclass
class JpegOptions:
    quality: int = 85


@dataclass
class PngOptions:
    compression_level: int = 6


@dataclass
class WebpOptions:
    quality: int = 80


@dataclass
class SharpCreatorOptions:
    width: int = 0
    height: int = 0
    channels: int = 4
    background: str = "transparent"


@dataclass
class ImageMetadata:
    width: Optional[int] = None
    height: Optional[int] = None
    format: Optional[str] = None
    size_bytes: int = 0


class _NoopImageProcessor:
    async def process(self, image_bytes: bytes, *_args, **_kwargs) -> bytes:
        return image_bytes


class _NoopImageCreator:
    async def create(self, *_args, **_kwargs) -> bytes:
        return b""


def get_image_processor() -> _NoopImageProcessor:
    return _NoopImageProcessor()


def get_image_processor_sync() -> _NoopImageProcessor:
    return _NoopImageProcessor()


def get_image_creator() -> _NoopImageCreator:
    return _NoopImageCreator()


def get_image_creator_sync() -> _NoopImageCreator:
    return _NoopImageCreator()


async def process_image(
    image_bytes: bytes,
    _resize: Optional[ResizeOptions] = None,
    _format: Optional[str] = None,
    _options: Optional[Dict[str, Any]] = None,
) -> bytes:
    return image_bytes


async def create_image(
    _creator_options: Optional[SharpCreatorOptions] = None,
    _format: str = "png",
    _options: Optional[Dict[str, Any]] = None,
) -> bytes:
    return b""


def get_image_metadata(image_bytes: bytes) -> ImageMetadata:
    return ImageMetadata(size_bytes=len(image_bytes))


__all__ = [
    "ImageMetadata",
    "ResizeOptions",
    "JpegOptions",
    "PngOptions",
    "WebpOptions",
    "SharpCreatorOptions",
    "get_image_processor",
    "get_image_processor_sync",
    "get_image_creator",
    "get_image_creator_sync",
    "process_image",
    "create_image",
    "get_image_metadata",
]

