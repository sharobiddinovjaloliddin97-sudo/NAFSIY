"""Shared additive response fields; old chat source JSON remains readable."""
from typing import Literal
from pydantic import BaseModel, Field, model_serializer


class SourceInfo(BaseModel):
    type: Literal["quran", "hadith"]
    reference: str = Field(min_length=1, max_length=200)
    provider: str = Field(min_length=1, max_length=120)
    verified: Literal[False] = False
    source_id: str | None = Field(default=None, max_length=300)
    content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    arabic_edition: str | None = Field(default=None, max_length=100)
    translation_edition: str | None = Field(default=None, max_length=100)
    translation_language: str | None = Field(default=None, max_length=10)
    numbering_system: str | None = Field(default=None, max_length=120)
    verification_status: str | None = Field(default=None, max_length=60)
    grading_status: str | None = Field(default=None, max_length=60)
    quotation_status: str | None = Field(default=None, max_length=60)
    license_state: str | None = Field(default=None, max_length=30)
    attribution: str | None = Field(default=None, max_length=500)
    arabic_attribution: str | None = Field(default=None, max_length=500)
    translation_attribution: str | None = Field(default=None, max_length=500)
    license_url: str | None = Field(default=None, max_length=300)

    @model_serializer(mode="wrap")
    def serialize_metadata(self, handler):
        return {key: value for key, value in handler(self).items() if value is not None}
