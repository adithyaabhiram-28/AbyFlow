import os
from datetime import date
from decimal import Decimal

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


def _gemini_pricing():
    tier = os.getenv('GEMINI_BILLING_TIER', 'free').strip().lower()

    if tier == 'free':
        return Decimal('0'), Decimal('0')

    if tier != 'paid':
        raise ValueError(
            "GEMINI_BILLING_TIER must be either 'free' or 'paid'"
        )

    if date.today() < date(2027, 1, 1):
        input_per_million = Decimal('0.75')
        output_per_million = Decimal('3.75')
    else:
        input_per_million = Decimal('1.50')
        output_per_million = Decimal('7.50')

    return (
        input_per_million / Decimal('1000000'),
        output_per_million / Decimal('1000000')
    )


def calculate_token_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int
) -> Decimal:
    """Calculate estimated USD cost for configured model token usage."""
    for token_name, token_count in (
        ('prompt_tokens', prompt_tokens),
        ('completion_tokens', completion_tokens),
    ):
        if isinstance(token_count, bool) or not isinstance(token_count, int):
            raise ValueError(
                f'{token_name} must be a non-negative integer'
            )
        if token_count < 0:
            raise ValueError(f'{token_name} cannot be negative')

    if model.startswith('gemini-'):
        prompt_rate, completion_rate = _gemini_pricing()
    elif model in PRICING_MAP:
        pricing = PRICING_MAP[model]
        prompt_rate = pricing['prompt_cost']
        completion_rate = pricing['completion_cost']
    else:
        raise ValueError(
            f'No token pricing is configured for model: {model}'
        )

    total = (
        Decimal(prompt_tokens) * prompt_rate
        + Decimal(completion_tokens) * completion_rate
    )
    return total.quantize(Decimal('0.000001'))
