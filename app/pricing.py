"""Estimated API cost from token counts.

List prices in US dollars per million tokens (Anthropic, September 2026).
Edit here if prices change. Costs shown in the app are estimates.
"""

# model: (input, output, cache read). Cache writes cost 1.25x input.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-4-5": (1.00, 5.00, 0.10),
}
CACHE_WRITE_MULTIPLIER = 1.25


def estimate_usd(model: str, usage: dict):
    """Estimated cost of one call, or None if the model's price isn't known."""
    price = PRICES.get(model)
    if price is None or not usage:
        return None
    input_price, output_price, cache_read_price = price
    return (
        usage.get("input_tokens", 0) * input_price
        + usage.get("output_tokens", 0) * output_price
        + usage.get("cache_creation_input_tokens", 0) * input_price * CACHE_WRITE_MULTIPLIER
        + usage.get("cache_read_input_tokens", 0) * cache_read_price
    ) / 1_000_000
