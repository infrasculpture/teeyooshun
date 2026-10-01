"""Send one rendered page to the model and handle what comes back.

- A cover page (any page with a name, class or index box) is skipped: its
  render is deleted at once and nothing the model returned is kept.
- A page that isn't upright is rotated and read again.
- A failed call is retried once; then the page is marked failed and the run continues.
  Errors that would repeat on every page (bad key or model name) stop the run.
"""

import base64
from dataclasses import dataclass, field
from pathlib import Path

from app.ingest import prompts
from app.ingest.render import rotate_clockwise
from app.llm import LLMError, structured_call
from app.pricing import estimate_usd

USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


@dataclass
class PageOutcome:
    status: str  # done / skipped / failed
    page_type: str = None
    rotation: int = 0
    printed_page: str = ""
    data: dict = None  # the model's JSON; None for cover and failed pages
    model: str = None
    usage: dict = field(default_factory=lambda: {k: 0 for k in USAGE_KEYS})
    cost_usd: float = None
    error: str = None
    cover_has_other_content: bool = False


def _image_block(png: Path) -> dict:
    data = base64.standard_b64encode(png.read_bytes()).decode("ascii")
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}}


def _call_with_retry(conn, png, system, message, settings, client):
    """(data, info) or raises LLMError after one retry (fatal errors are not retried)."""
    content = [_image_block(png), {"type": "text", "text": message}]
    last = None
    for _ in range(2):
        try:
            return structured_call(
                conn, model=settings.ingest_model, effort=settings.ingest_effort, system=system,
                content=content, schema=prompts.PAGE_SCHEMA, purpose="ingest",
                fallback=settings.refusal_fallback, client=client,
            )
        except LLMError as e:
            if e.fatal:
                raise
            last = e
    raise last


def process_page(conn, *, png, material_type, subject, message, settings, topic_list=None, client=None) -> PageOutcome:
    png = Path(png)
    system = prompts.system_blocks(material_type, subject, topic_list)
    outcome = PageOutcome(status="done")

    def add_usage(info):
        outcome.model = info["model"]
        for k in USAGE_KEYS:
            outcome.usage[k] += info["usage"].get(k, 0)

    try:
        data, info = _call_with_retry(conn, png, system, message, settings, client)
        add_usage(info)
        if data["page_type"] != "cover" and data["rotation_needed"] in (90, 180, 270):
            outcome.rotation = data["rotation_needed"]
            rotate_clockwise(png, outcome.rotation)
            data, info = _call_with_retry(conn, png, system, message, settings, client)
            add_usage(info)
    except LLMError as e:
        if e.fatal:
            raise
        outcome.status, outcome.error = "failed", str(e)
        outcome.cost_usd = estimate_usd(outcome.model, outcome.usage) if outcome.model else None
        return outcome

    outcome.page_type = data["page_type"]
    outcome.cost_usd = estimate_usd(outcome.model, outcome.usage)
    if data["page_type"] == "cover":
        # Never keep anything from a page with a name box: delete the render, drop the output.
        png.unlink(missing_ok=True)
        outcome.status = "skipped"
        outcome.cover_has_other_content = bool(data.get("cover_has_other_content"))
        return outcome
    outcome.printed_page = data.get("printed_page_number", "")
    outcome.data = data
    if data["page_type"] in ("blank", "other"):
        outcome.status = "skipped"
    return outcome
