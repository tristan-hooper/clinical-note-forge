"""Non-secret TOML configuration; stdlib parsing, validated at the boundary."""

import math
import tomllib
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from .models import Model, NoteStyle


class Config(Model):
    seed: int = 42
    renderer: Literal["template", "llm"] = "template"
    styles: list[NoteStyle] = Field(default_factory=lambda: list(NoteStyle))
    style_weights: list[float] = Field(default_factory=lambda: [1.0, 1.0, 1.0])
    split_proportions: tuple[float, float, float] = (0.8, 0.1, 0.1)
    max_records: int | None = Field(default=None, ge=1)
    selection_fraction: float = Field(default=1.0, ge=0, le=1)
    max_facts_per_group: int | None = Field(default=None, ge=1)
    retries: int = Field(default=2, ge=0, le=10)
    model: str = ""
    temperature: float = Field(default=0.2, ge=0, le=2)
    timeout_seconds: float = Field(default=60, gt=0, le=300)
    max_response_tokens: int = Field(default=4096, ge=1)
    cache_dir: Path = Path(".cache/clinicalnoteforge")

    @model_validator(mode="after")
    def proportions(self) -> Self:
        if not self.styles or len(set(self.styles)) != len(self.styles):
            raise ValueError("styles must be nonempty and unique")
        weights = self.style_weights
        if len(weights) != len(self.styles) or any(not math.isfinite(x) or x < 0 for x in weights):
            raise ValueError("provide one finite, nonnegative weight per style")
        if sum(weights) <= 0:
            raise ValueError("style weights must have positive total")
        if any(not math.isfinite(x) or x < 0 for x in self.split_proportions):
            raise ValueError("split proportions must be finite and nonnegative")
        if not math.isclose(sum(self.split_proportions), 1, abs_tol=1e-9):
            raise ValueError("split proportions must sum to one")
        return self


def load_config(path: Path | None, **overrides) -> Config:
    data = tomllib.loads(path.read_text(encoding="utf-8")) if path else {}
    data.update({k: v for k, v in overrides.items() if v is not None})
    return Config.model_validate(data)
