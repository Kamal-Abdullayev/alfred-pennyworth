# pricing.py — attribution key for splitting a run's cost across its API messages.
#
# The authoritative number is ResultMessage.total_cost_usd: the Claude Code CLI takes the
# exact token counts the API returned and multiplies them by Anthropic's public API list
# prices for the model (input, output, cache read, cache write). On a Team/Max seat nothing
# is billed per token — that figure is what the run WOULD cost on the API, a consumption
# meter against the seat's rate limits. With an API key it would be a real charge.
#
# Per-message input/cache token counts are exact too; per-message OUTPUT tokens are not
# available (the stream carries a partial snapshot, the true total arrives once per run).
# runner.py weighs each message with the table below and pro-rates those weights so they
# add up to the run total. The table therefore only affects the split, not the total.
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
