"""The one place that calls the Claude API. Every call is logged to api_calls."""

import json
import time

import anthropic

from app.db import utc_now

FALLBACK_BETA = "server-side-fallback-2026-07-01"

_client = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    return _client


class LLMError(Exception):
    """A call that produced no usable JSON.

    kind: api_error / refusal / truncated / invalid_output.
    fatal: True when every later call would fail the same way (bad key, bad
    model name, malformed request), so a batch run should stop.
    """

    def __init__(self, kind: str, message: str, api_call_id=None, fatal: bool = False):
        super().__init__(message)
        self.kind = kind
        self.api_call_id = api_call_id
        self.fatal = fatal


def _log_call(conn, *, purpose, model, question_id, response, duration_ms, error, request_id):
    usage = getattr(response, "usage", None)
    cur = conn.execute(
        """INSERT INTO api_calls
           (created_at, purpose, model, question_id, input_tokens, output_tokens,
            cache_creation_input_tokens, cache_read_input_tokens, duration_ms,
            stop_reason, request_id, error)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            utc_now(), purpose, getattr(response, "model", None) or model, question_id,
            (usage.input_tokens or 0) if usage else 0,
            (usage.output_tokens or 0) if usage else 0,
            (usage.cache_creation_input_tokens or 0) if usage else 0,
            (usage.cache_read_input_tokens or 0) if usage else 0,
            duration_ms, getattr(response, "stop_reason", None), request_id, error,
        ),
    )
    conn.commit()
    return cur.lastrowid


def structured_call(conn, *, model, effort, system, content, schema, purpose,
                    question_id=None, fallback=True, max_tokens=16000, client=None):
    """Send one request whose reply must match `schema`. Returns (data, info).

    system: list of system content blocks. content: list of user content blocks.
    Raises LLMError if no usable JSON comes back.
    """
    client = client or get_client()
    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": content}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
    )

    response, error, request_id, fatal = None, None, None, False
    started = time.monotonic()
    try:
        if fallback:
            # If the model declines, the API re-runs the request on Anthropic's fallback model.
            response = client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
        else:
            response = client.messages.create(**kwargs)
        request_id = getattr(response, "_request_id", None)
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError,
            anthropic.NotFoundError, anthropic.BadRequestError) as e:
        error, fatal = f"{e.status_code}: {e.message}", True
        request_id = getattr(e, "request_id", None)
    except anthropic.APIStatusError as e:  # 429 / 5xx after the SDK's own retries
        error = f"{e.status_code}: {e.message}"
        request_id = getattr(e, "request_id", None)
    except anthropic.APIConnectionError as e:
        error = f"connection error: {e}"
    duration_ms = int((time.monotonic() - started) * 1000)

    call_id = _log_call(conn, purpose=purpose, model=model, question_id=question_id,
                        response=response, duration_ms=duration_ms, error=error,
                        request_id=request_id)

    if error:
        raise LLMError("api_error", error, call_id, fatal=fatal)
    if response.stop_reason == "refusal":
        raise LLMError("refusal", "the model declined to mark this answer", call_id)
    if response.stop_reason == "max_tokens":
        raise LLMError("truncated", "the reply was cut off at max_tokens", call_id)

    text = next((b.text for b in response.content if b.type == "text"), None)
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        raise LLMError("invalid_output", "the reply was not valid JSON", call_id) from None

    u = response.usage
    info = {
        "api_call_id": call_id,
        "model": response.model,
        "usage": {
            "input_tokens": u.input_tokens or 0,
            "output_tokens": u.output_tokens or 0,
            "cache_creation_input_tokens": u.cache_creation_input_tokens or 0,
            "cache_read_input_tokens": u.cache_read_input_tokens or 0,
        },
    }
    return data, info
