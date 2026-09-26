from marshmallow import Schema, fields, validate, post_load


class StripeWebhookSchema(Schema):
    """Schema for validating essential Stripe webhook event payload data."""
    event_type = fields.String(
        data_key="type",
        required=True,
        validate=validate.Length(min=1)
    )
    user_email = fields.Email(
        data_key="user_email",
        required=True
    )

    @post_load
    def make_payload(self, data, **kwargs):
        # Normalize email
        if 'user_email' in data and data['user_email']:
            data['user_email'] = data['user_email'].strip().lower()
        return data