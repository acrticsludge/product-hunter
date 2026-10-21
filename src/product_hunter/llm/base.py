from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class GenerationRequest(BaseModel):
    prompt: str = Field(min_length=1)


class GenerationResponse(BaseModel):
    text: str
    provider: str
    model: str
    extractor_version: str
    input_chars: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    provider_version: str | None = None


@runtime_checkable
class TextGenerator(Protocol):
    name: str

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        ...

    def check(self) -> dict[str, object]:
        """Verify the provider and model are usable."""
        ...


class ProviderError(Exception):
    """A provider call failed or returned an unusable response."""


class ModelCallFailed(ProviderError):
    """A provider call failed, and the error names the model that failed.

    The identity travels with the error, so a caller can attribute the
    failure to the model that made the call and not to the model the run was
    built from. A chain that fell through fails on a candidate the run never
    named, and the record has to say which one.
    """

    def __init__(
        self,
        message: str,
        *,
        provider_id: str | None = None,
        model_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.provider_id = provider_id
        self.model_id = model_id


class ProviderUnavailable(ProviderError):
    """The configured provider cannot be used in this environment."""


class InputTooLarge(ProviderError):
    """The prompt exceeds the configured input limit."""
