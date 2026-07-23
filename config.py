"""Validated JSON5 settings for the camera publisher node."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Annotated, Literal, Self

import json5
import zenoh
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
PositiveInt = Annotated[int, Field(strict=True, ge=1)]
JpegQuality = Annotated[int, Field(strict=True, ge=0, le=100)]
PositiveFiniteFloat = Annotated[float, Field(gt=0, allow_inf_nan=False)]
CongestionControlName = Literal["drop", "block", "block_first"]
ReliabilityName = Literal["best_effort", "reliable"]


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _validate_concrete_key(value: str, location: str) -> str:
    if not value.strip():
        raise ValueError(f"{location} must be a non-empty string")
    try:
        zenoh.KeyExpr(value)
    except zenoh.ZError as error:
        raise ValueError(
            f"{location} must be a valid Zenoh key expression: {error}"
        ) from error
    if "*" in value:
        raise ValueError(f"{location} must be a concrete Zenoh key without wildcards")
    return value


def validate_zenoh_key_prefix(value: str) -> str:
    return _validate_concrete_key(value, "zenoh_key_prefix")


class CameraSource(ConfigModel):
    index: NonNegativeInt | None = None
    path: StrictStr | None = None

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("path must be a non-empty string")
        return value

    @model_validator(mode="after")
    def validate_selector(self) -> Self:
        if (self.index is None) == (self.path is None):
            raise ValueError("exactly one of index or path is required")
        return self

    @property
    def opencv_source(self) -> int | str:
        if self.index is not None:
            return self.index
        assert self.path is not None
        return self.path


class PublisherConfig(ConfigModel):
    publish_frequency_hz: PositiveFiniteFloat
    congestion_control: CongestionControlName
    reliability: ReliabilityName

    @field_validator("publish_frequency_hz", mode="before")
    @classmethod
    def validate_frequency_type(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("publish_frequency_hz must be a number")
        return value

    @field_validator("publish_frequency_hz")
    @classmethod
    def validate_frequency_period(cls, value: float) -> float:
        try:
            period = 1.0 / value
        except OverflowError:
            raise ValueError(
                "publish_frequency_hz must produce a finite period"
            ) from None
        if not math.isfinite(period) or period <= 0:
            raise ValueError("publish_frequency_hz must produce a finite period")
        return value


class CameraConfig(ConfigModel):
    device_key: StrictStr
    source: CameraSource
    size: tuple[PositiveInt, PositiveInt]
    jpeg_quality: JpegQuality
    publisher: PublisherConfig

    @field_validator("device_key")
    @classmethod
    def validate_device_key(cls, value: str) -> str:
        value = _validate_concrete_key(value, "device_key")
        if "/" in value:
            raise ValueError("device_key must be a single key segment")
        return value


class NodeConfig(ConfigModel):
    zenoh_key_prefix: StrictStr
    cameras: Annotated[tuple[CameraConfig, ...], Field(min_length=1)]

    @field_validator("zenoh_key_prefix")
    @classmethod
    def validate_key(cls, value: str) -> str:
        return validate_zenoh_key_prefix(value)

    def publisher_key(self, camera: CameraConfig) -> str:
        return f"{self.zenoh_key_prefix}/{camera.device_key}"

    @model_validator(mode="after")
    def validate_cameras(self) -> Self:
        device_keys: set[str] = set()
        sources: set[int | str] = set()
        for camera in self.cameras:
            if camera.device_key in device_keys:
                raise ValueError(f"duplicate device_key: {camera.device_key!r}")
            device_keys.add(camera.device_key)

            source = camera.source.opencv_source
            if source in sources:
                raise ValueError(f"duplicate camera source: {source!r}")
            sources.add(source)

            _validate_concrete_key(
                self.publisher_key(camera),
                "derived publisher key",
            )
        return self


def load_node_config(path: Path) -> NodeConfig:
    try:
        document = json5.loads(
            path.read_text(encoding="utf-8"), allow_duplicate_keys=False
        )
    except ValueError as error:
        raise ValueError(f"{path}: invalid JSON5: {error}") from error
    try:
        return NodeConfig.model_validate(document)
    except ValidationError as error:
        raise ValueError(f"{path}: invalid node configuration:\n{error}") from error
