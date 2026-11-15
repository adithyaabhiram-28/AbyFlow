from decimal import Decimal


# Pricing per token in USD. Keep this map limited to models whose pricing
# has been explicitly configured and verified.
PRICING_MAP = {
    'gpt-4o-mini': {
        'prompt_cost': Decimal('0.00000015'),
        'completion_cost': Decimal('0.00000060'),
    },
    'gpt-4o': {
        'prompt_cost': Decimal('0.0000025'),
        'completion_cost': Decimal('0.0000100'),
    },
}


def calculate_token_cost(model: str, prompt_tokens: int, completion_tokens: int) -> Decimal:
    """Calculate estimated USD cost for a configured model's token usage."""
    if model not in PRICING_MAP:
        raise ValueError(f"No token pricing is configured for model: {model}")

    for token_name, token_count in (
        ("prompt_tokens", prompt_tokens),
        ("completion_tokens", completion_tokens),
    ):
        if isinstance(token_count, bool) or not isinstance(token_count, int):
            raise ValueError(f"{token_name} must be a non-negative integer")
        if token_count < 0:
            raise ValueError(f"{token_name} cannot be negative")

    pricing = PRICING_MAP[model]
    prompt_cost = Decimal(prompt_tokens) * pricing['prompt_cost']
    completion_cost = Decimal(completion_tokens) * pricing['completion_cost']
    total = (prompt_cost + completion_cost).quantize(Decimal('0.000001'))
    return total
