from marshmallow import Schema, fields, post_load

class StripeWebhookSchema(Schema):
    event_type = fields.String(data_key="type", required=True)
    user_email = fields.String(data_key="user_email", required=True)

    @post_load
    def make_payload(self, data, **kwargs):
        return data