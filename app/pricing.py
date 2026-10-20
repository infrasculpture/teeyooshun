"""Estimated API cost from token counts.

List prices in US dollars per million tokens (supplied by James, October 2026).
Edit here if prices change. Costs shown in the app are estimates.
"""

import logging

log = logging.getLogger("uvicorn.error")

# model: (input, output, cache read, cache write)
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10, 2.50),
    "claude-haiku-4-5": (1.00, 5.00, 0.10, 1.25),
}


def price_for(model: str):
    """The (input, output, cache read, cache write) prices for a model, or None if unknown.

    A dated name such as claude-haiku-4-5-20251001 uses the price of its undated name.
    """
    if not model:
        return None
    if model in PRICES:
        return PRICES[model]
    matches = [k for k in PRICES if model.startswith(k + "-")]
    return PRICES[max(matches, key=len)] if matches else None


def estimate_usd(model: str, usage: dict):
    """Estimated cost of one call, or None if the model's price isn't known."""
    price = price_for(model)
    if price is None or not usage:
        return None
    input_price, output_price, cache_read_price, cache_write_price = price
    return (
        usage.get("input_tokens", 0) * input_price
        + usage.get("output_tokens", 0) * output_price
        + usage.get("cache_creation_input_tokens", 0) * cache_write_price
        + usage.get("cache_read_input_tokens", 0) * cache_read_price
    ) / 1_000_000


def unpriced_models(settings) -> list[str]:
    """Models named in the settings (marking, light, ingest) that have no entry in PRICES."""
    named = {settings.marking_model, settings.light_model, settings.ingest_model}
    return sorted(m for m in named if m and price_for(m) is None)


def warn_unpriced(settings) -> list[str]:
    """Log a warning for each configured model with no price. Returns them."""
    missing = unpriced_models(settings)
    for model in missing:
        log.warning("Model %s has no entry in app/pricing.py PRICES, so its API cost will show as unpriced", model)
    return missing
