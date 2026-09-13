"""Structured-output calls to Claude, with usage, latency and cost accounting.

Agents depend on the `StructuredLlm` protocol, so tests substitute a fake and never touch the
network. The real implementation streams (long protocol sections and long outputs must not hit
HTTP timeouts) and validates the response against the agent's Pydantic output model.

Retries: the Anthropic SDK retries connection errors, 408/409/429 and 5xx with exponential
backoff; `max_retries` raises its default of 2 so rate limits during concurrent extraction are
absorbed rather than failing an agent.
"""

import base64
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

import anthropic
from pydantic import BaseModel

from backend.models.extraction import LlmUsage

log = logging.getLogger(__name__)

OutT = TypeVar("OutT", bound=BaseModel)

DEFAULT_EXTRACTION_MODEL = "claude-sonnet-5"
MAX_RETRIES = 6

# USD per million tokens (input, output). Cache writes bill at 1.25x input, cache reads at 0.1x.
PRICING: dict[str, tuple[float, float]] = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def estimate_cost(usage: LlmUsage) -> float:
    price = PRICING.get(usage.model)
    if price is None:
        return 0.0
    per_in, per_out = price[0] / 1e6, price[1] / 1e6
    return round(
        usage.input_tokens * per_in
        + usage.cache_creation_input_tokens * per_in * 1.25
        + usage.cache_read_input_tokens * per_in * 0.1
        + usage.output_tokens * per_out,
        6,
    )


class LlmOutputError(RuntimeError):
    """The model returned no usable structured output (refusal, truncation, invalid JSON)."""

    def __init__(self, message: str, usage: LlmUsage | None = None) -> None:
        super().__init__(message)
        self.usage = usage


@dataclass
class LlmRequest:
    model: str
    system: str
    user_content: str
    max_tokens: int = 32000
    effort: str | None = None  # low | medium | high | xhigh | max; None = model default
    #: PNG page images sent before the text (e.g. schedule-of-activities pages read by vision).
    images: list[bytes] = field(default_factory=list)


class StructuredLlm(Protocol):
    async def extract(
        self, request: LlmRequest, output_model: type[OutT]
    ) -> tuple[OutT, LlmUsage]: ...


def _content(request: LlmRequest) -> str | list[dict[str, Any]]:
    if not request.images:
        return request.user_content
    blocks: list[dict[str, Any]] = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.standard_b64encode(image).decode("ascii"),
            },
        }
        for image in request.images
    ]
    blocks.append({"type": "text", "text": request.user_content})
    return blocks


class AnthropicLlm:
    def __init__(self, api_key: str) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=MAX_RETRIES)

    async def extract(self, request: LlmRequest, output_model: type[OutT]) -> tuple[OutT, LlmUsage]:
        params: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": _content(request)}],
            "output_format": output_model,
        }
        if request.effort:
            params["output_config"] = {"effort": request.effort}

        started = time.perf_counter()
        async with self._client.messages.stream(**params) as stream:
            message = await stream.get_final_message()
        latency = time.perf_counter() - started

        u = message.usage
        usage = LlmUsage(
            model=request.model,
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_creation_input_tokens=u.cache_creation_input_tokens or 0,
            cache_read_input_tokens=u.cache_read_input_tokens or 0,
            latency_seconds=round(latency, 2),
            stop_reason=message.stop_reason,
            request_id=getattr(message, "_request_id", None),
        )
        usage.cost_usd = estimate_cost(usage)
        log.info("llm call", extra={"llm": usage.model_dump()})

        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            raise LlmOutputError(f"model declined the request: {details}", usage)
        if message.stop_reason == "max_tokens":
            raise LlmOutputError(
                f"output truncated at max_tokens={request.max_tokens}; raise the limit", usage
            )
        parsed = message.parsed_output
        if parsed is None:
            raise LlmOutputError("response did not contain valid structured output", usage)
        return parsed, usage
