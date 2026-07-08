"""Wrapper around the Anthropic API that forces structured, schema-validated JSON output.

Every LLM call that feeds the report goes through `structured_call`: the pydantic model's
JSON schema is passed as a forced tool, so Claude can only respond via that tool's
arguments. Freeform text calls are not used anywhere output feeds the report (see the
"Explicitly out of scope" / edge-case notes in directives/due_diligence_agent.md).
"""
from __future__ import annotations

import logging
import os
from typing import Type, TypeVar

from anthropic import Anthropic
from pydantic import BaseModel, ValidationError

from dd_agent.config import ModelConfig

logger = logging.getLogger("dd_agent.llm")

T = TypeVar("T", bound=BaseModel)

_TOOL_NAME = "emit_structured_output"


class StructuredCallError(RuntimeError):
    """Raised when Claude fails to produce schema-valid output after retrying."""


def _client() -> Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to .env at the repo root."
        )
    return Anthropic(api_key=api_key)


def structured_call(
    system_prompt: str,
    user_prompt: str,
    output_model: Type[T],
    model_config: ModelConfig,
    max_retries: int = 1,
) -> T:
    """Call Claude and force output matching `output_model`'s JSON schema.

    Retries once (by default) with the validation error appended to the prompt if the
    model's output fails schema validation, per the directive's edge-case handling.
    Raises StructuredCallError if it still fails after retries.
    """
    client = _client()
    schema = output_model.model_json_schema()
    tool = {
        "name": _TOOL_NAME,
        "description": f"Emit output matching the {output_model.__name__} schema.",
        "input_schema": schema,
    }

    attempt_prompt = user_prompt
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        response = client.messages.create(
            model=model_config.name,
            max_tokens=model_config.max_tokens,
            temperature=model_config.temperature,
            system=system_prompt,
            tools=[tool],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=[{"role": "user", "content": attempt_prompt}],
        )

        tool_use_block = next(
            (b for b in response.content if b.type == "tool_use" and b.name == _TOOL_NAME),
            None,
        )
        if tool_use_block is None:
            last_error = StructuredCallError("Claude did not return a tool_use block")
            logger.warning("Structured call attempt %d: no tool_use block", attempt)
            continue

        try:
            return output_model.model_validate(tool_use_block.input)
        except ValidationError as exc:
            last_error = exc
            logger.warning(
                "Structured call attempt %d failed schema validation: %s", attempt, exc
            )
            attempt_prompt = (
                f"{user_prompt}\n\n"
                f"Your previous response failed schema validation with this error:\n{exc}\n"
                "Correct it and respond again via the tool."
            )

    raise StructuredCallError(
        f"Failed to get schema-valid output from Claude after {max_retries + 1} attempt(s): {last_error}"
    )
