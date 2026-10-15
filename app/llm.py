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

    kind: api_error / schema_error / refusal / truncated / invalid_output.
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


def _is_schema_error(e) -> bool:
    """A 400 about the output schema itself (for example "The compiled grammar is too large")."""
    message = (e.message or "").lower()
    return e.status_code == 400 and ("grammar" in message or "schema" in message)


def check_against_schema(value, schema, path="reply") -> str | None:
    """The first way `value` breaks `schema` (types, required keys, enums), or None. For calls made without strict."""
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            return f"{path} is not an object"
        for key in schema.get("required", []):
            if key not in value:
                return f"{path} is missing {key}"
        for key, sub in schema.get("properties", {}).items():
            if key in value and (problem := check_against_schema(value[key], sub, f"{path}.{key}")):
                return problem
        return None
    if kind == "array":
        if not isinstance(value, list):
            return f"{path} is not a list"
        for i, item in enumerate(value):
            if problem := check_against_schema(item, schema.get("items", {}), f"{path}[{i}]"):
                return problem
        return None
    expected = {"string": str, "integer": int, "boolean": bool}.get(kind)
    if expected and (not isinstance(value, expected) or (kind == "integer" and isinstance(value, bool))):
        return f"{path} is not a {kind}"
    if "enum" in schema and value not in schema["enum"]:
        return f"{path} is not one of {schema['enum']}"
    return None


def _json_from(text: str):
    """Parse a reply that may be wrapped in a code fence."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    return json.loads(text)


def structured_call(conn, *, model, effort, system, content, schema, purpose,
                    question_id=None, fallback=True, max_tokens=16000, client=None, strict=True):
    """Send one request whose reply must match `schema`. Returns (data, info).

    system: list of system content blocks. content: list of user content blocks.
    strict=False sends the schema as instructions instead of a constrained-output grammar (for a schema
    the API says is too large) and checks the reply in code. Raises LLMError if no usable JSON comes back.
    """
    client = client or get_client()
    output_config = {}
    if strict:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    else:
        system = system + [{"type": "text", "text": "Reply with only one JSON object that follows this JSON schema "
                            "exactly (every property required, no others):\n" + json.dumps(schema)}]
    if effort:  # some models (e.g. Haiku 4.5) reject the effort parameter
        output_config["effort"] = effort
    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": content}],
    )
    if output_config:
        kwargs["output_config"] = output_config

    response, error, request_id, fatal, schema_error = None, None, None, False, False
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
        schema_error = _is_schema_error(e)
    except anthropic.APIStatusError as e:  # 429 / 5xx after the SDK's own retries
        error = f"{e.status_code}: {e.message}"
        request_id = getattr(e, "request_id", None)
    except anthropic.APIConnectionError as e:
        error = f"connection error: {e}"
    duration_ms = int((time.monotonic() - started) * 1000)

    call_id = _log_call(conn, purpose=purpose, model=model, question_id=question_id,
                        response=response, duration_ms=duration_ms, error=error,
                        request_id=request_id)

    if schema_error:
        raise LLMError("schema_error", f"the API rejected the output schema ({error}). This is a problem with "
                       "the program's schema, not the API key or model name.", call_id, fatal=True)
    if error:
        raise LLMError("api_error", error, call_id, fatal=fatal)
    if response.stop_reason == "refusal":
        raise LLMError("refusal", "the model declined to mark this answer", call_id)
    if response.stop_reason == "max_tokens":
        raise LLMError("truncated", "the reply was cut off at max_tokens", call_id)

    text = next((b.text for b in response.content if b.type == "text"), None)
    try:
        data = json.loads(text) if strict else _json_from(text)
    except (TypeError, ValueError):
        raise LLMError("invalid_output", "the reply was not valid JSON", call_id) from None
    if not strict and (problem := check_against_schema(data, schema)):
        raise LLMError("invalid_output", f"the reply did not match the schema: {problem}", call_id)

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
