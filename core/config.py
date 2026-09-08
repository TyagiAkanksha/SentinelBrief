"""The single config surface (CONVENTIONS.md §7); `Settings()` constructs with zero env vars.

Every setting has a `.env.example` default. Secrets are `SecretStr` so a `repr()` in a log line
can never leak them. `MODEL_PRICES_JSON` (PRD §6.4 / §7.3) is USD per million tokens, per model
id; it decodes to `Decimal` (never `float`) to avoid precision drift in cost math.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class ModelPrice(BaseModel):
    """USD price per million tokens for one model id (PRD §6.4)."""

    input_per_mtok: Decimal
    output_per_mtok: Decimal


class Settings(BaseSettings):
    """Pydantic-settings config surface; zero-env constructible (CONVENTIONS.md §7)."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str = "https://api.openai.com/v1"
    llm_json_mode: Literal["json_object", "json_schema"] = "json_object"
    cheap_model: str = ""
    strong_model: str = ""
    # NoDecode: pydantic-settings' own complex-field env decoding raises SettingsError on
    # malformed JSON before any field validator runs. Skipping it lets the `mode="before"`
    # validator below do the json.loads itself, so malformed input surfaces as a pydantic
    # ValidationError instead (CONVENTIONS.md §7).
    model_prices_json: Annotated[dict[str, ModelPrice], NoDecode] = Field(default_factory=dict)
    triage_prompt_version: str = "triage-v1"
    environment: str = "development"
    database_url: SecretStr = SecretStr("")
    ingest_hmac_secret: SecretStr = SecretStr("")
    cors_origins: str = "http://localhost:3000"
    alerts_list_cache_ttl_s: int = 15
    stats_cache_ttl_s: int = 60

    @field_validator("model_prices_json", mode="before")
    @classmethod
    def _decode_model_prices_json(cls, value: object) -> object:
        """Decode `MODEL_PRICES_JSON` from its raw env string; ValueError -> ValidationError.

        Args:
            value: The raw env value (a JSON string) or an already-decoded mapping.

        Returns:
            A mapping suitable for `dict[str, ModelPrice]` validation.
        """
        if isinstance(value, str):
            return json.loads(value)
        return value

    @property
    def is_dev(self) -> bool:
        """True unless `environment` is exactly "production" (case/whitespace-insensitive)."""
        return self.environment.strip().lower() != "production"

    @property
    def cors_origin_list(self) -> list[str]:
        """`cors_origins` split on commas, blanks stripped (CONVENTIONS.md §5)."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]
