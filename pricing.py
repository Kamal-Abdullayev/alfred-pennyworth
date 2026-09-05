# pricing.py — per-turn cost estimate at Anthropic API list price.
#
# The SDK reports cost only per RUN (ResultMessage.total_cost_usd). Per-message
# token counts are exact (AssistantMessage.usage); to attribute cost to a turn we
# apply the published list prices below. This is the only place Alfred does
# pricing arithmetic. The run-level SDK figure remains the authoritative number;
# the UI shows both so any drift is visible.
#
# USD per 1M tokens. Cache write priced at the 1-hour tier (what the SDK uses).
PRICES = {
    "opus":   {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_write": 10.00},
    "sonnet": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 6.00},
    "haiku":  {"input": 1.00, "output": 5.00,  "cache_read": 0.10, "cache_write": 2.00},
}
BASIS = "alfred-list-estimate"


def table_for(model: str) -> dict | None:
    m = (model or "").lower()
    for key, t in PRICES.items():
        if key in m:
            return t
    return None


def estimate(model: str, usage: dict) -> float:
    """usage keys as the API returns them: input_tokens, output_tokens,
    cache_read_input_tokens, cache_creation_input_tokens."""
    t = table_for(model)
    if not t or not usage:
        return 0.0
    g = lambda k: float(usage.get(k) or 0)
    return (g("input_tokens") * t["input"] + g("output_tokens") * t["output"]
            + g("cache_read_input_tokens") * t["cache_read"]
            + g("cache_creation_input_tokens") * t["cache_write"]) / 1_000_000
