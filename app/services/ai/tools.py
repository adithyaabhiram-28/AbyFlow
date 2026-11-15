import logging
from typing import Dict, Any
from app.repositories.billing_repository import BillingRepository
from app.services.rag.retrieval import search_similar_chunks

logger = logging.getLogger(__name__)

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_billing_overview",
            "description": "Retrieve current plan tier, lifetime spend, customer count, and billing overview for the authenticated account.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_invoice_summary",
            "description": "Retrieve recent invoices for the current workspace with issue dates, due dates, statuses, and totals.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of invoices to return (1-50, default 5).",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["draft", "issued", "paid", "overdue", "cancelled"],
                        "description": "Optional status filter.",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_customer_summary",
            "description": "Retrieve customer list and contact information belonging to the active workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of customers to return (1-50, default 10).",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_payment_status",
            "description": "Retrieve Stripe payment history, charges, and payment statuses for the current user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Optional Stripe Checkout Session ID to look up a specific payment.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of payments to return (1-20, default 5).",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "explain_invoice",
            "description": "Explain an invoice in detail, including line items, subtotal, tax rate %, tax amount, and due date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "invoice_number": {
                        "type": "string",
                        "description": "The exact invoice number, e.g. INV-000001.",
                    },
                },
                "required": ["invoice_number"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_billing_activity",
            "description": "Summarize billing activity and payment volume over a recent time window (e.g. last 30 days).",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Number of days in the lookback window (1-365, default 30).",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_billing_docs",
            "description": "Search AbyFlow billing documentation, terms, and policies for help answering user questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query or keyword phrase.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of documentation matches to return (1-10, default 3).",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
]


def validate_tool_arguments(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Strictly validate AI tool arguments before execution."""
    allowed_args = {
        "get_billing_overview": set(),
        "get_invoice_summary": {"limit", "status"},
        "get_customer_summary": {"limit"},
        "get_payment_status": {"session_id", "limit"},
        "explain_invoice": {"invoice_number"},
        "get_billing_activity": {"days"},
        "search_billing_docs": {"query", "limit"},
    }

    if tool_name not in allowed_args:
        raise ValueError(f"Unknown or unauthorized tool: '{tool_name}'")

    if not isinstance(args, dict):
        raise ValueError("Tool arguments must be a dictionary")

    unexpected = set(args) - allowed_args[tool_name]
    if unexpected:
        raise ValueError(
            f"Unexpected argument(s): {', '.join(sorted(unexpected))}"
        )

    clean_args = {}

    def validate_integer(name, min_val, max_val):
        if name not in args:
            return

        value = args[name]

        # bool is a subclass of int in Python, so use exact type checking.
        if type(value) is not int:
            raise ValueError(f"{name} must be an integer")

        if not min_val <= value <= max_val:
            raise ValueError(
                f"{name} must be between {min_val} and {max_val}"
            )

        clean_args[name] = value

    def validate_string(name, max_len, required=False):
        if name not in args:
            if required:
                raise ValueError(f"{name} is required")
            return

        value = args[name]

        if not isinstance(value, str):
            raise ValueError(f"{name} must be a string")

        value = value.strip()

        if not value:
            raise ValueError(f"{name} must not be empty")

        if len(value) > max_len:
            raise ValueError(
                f"{name} must not exceed {max_len} characters"
            )

        clean_args[name] = value

    if tool_name == "get_billing_overview":
        pass

    elif tool_name == "get_invoice_summary":
        validate_integer("limit", 1, 50)

        if "status" in args:
            status = args["status"]

            if not isinstance(status, str):
                raise ValueError("status must be a string")

            status = status.strip().lower()

            if status not in (
                "draft", "issued", "paid", "overdue", "cancelled"
            ):
                raise ValueError("Invalid invoice status filter")

            clean_args["status"] = status

    elif tool_name == "get_customer_summary":
        validate_integer("limit", 1, 50)

    elif tool_name == "get_payment_status":
        validate_string("session_id", 255)
        validate_integer("limit", 1, 20)

    elif tool_name == "explain_invoice":
        validate_string("invoice_number", 100, required=True)

    elif tool_name == "get_billing_activity":
        validate_integer("days", 1, 365)

    elif tool_name == "search_billing_docs":
        validate_string("query", 1000, required=True)
        validate_integer("limit", 1, 10)

    return clean_args


def execute_tool(tool_name: str, raw_arguments: Dict[str, Any], user_id: int, tenant_id: int) -> Dict[str, Any]:
    """
    Execute a registered tool deterministically.
    Always uses the authenticated user_id and tenant_id from the server context.
    The model CANNOT supply or override user_id or tenant_id.
    """
    try:
        clean_args = validate_tool_arguments(tool_name, raw_arguments)
    except ValueError as e:
        logger.warning("Tool argument validation failed for %s: %s", tool_name, str(e))
        return {"error": f"Invalid arguments for {tool_name}: {str(e)}"}

    try:
        if tool_name == "get_billing_overview":
            return BillingRepository.get_billing_overview(user_id=user_id, tenant_id=tenant_id)

        elif tool_name == "get_invoice_summary":
            return {
                "invoices": BillingRepository.get_invoice_summary(
                    tenant_id=tenant_id,
                    limit=clean_args.get("limit", 5),
                    status=clean_args.get("status")
                )
            }

        elif tool_name == "get_customer_summary":
            return {
                "customers": BillingRepository.get_customer_summary(
                    tenant_id=tenant_id,
                    limit=clean_args.get("limit", 10)
                )
            }

        elif tool_name == "get_payment_status":
            return {
                "payments": BillingRepository.get_payment_status(
                    user_id=user_id,
                    session_id=clean_args.get("session_id"),
                    limit=clean_args.get("limit", 5)
                )
            }

        elif tool_name == "explain_invoice":
            return BillingRepository.explain_invoice(
                tenant_id=tenant_id,
                invoice_number=clean_args["invoice_number"]
            )

        elif tool_name == "get_billing_activity":
            return BillingRepository.get_billing_activity(
                user_id=user_id,
                tenant_id=tenant_id,
                days=clean_args.get("days", 30)
            )

        elif tool_name == "search_billing_docs":
            matches = search_similar_chunks(
                query=clean_args["query"],
                tenant_id=tenant_id,
                top_k=clean_args.get("limit", 3)
            )
            return {"matches": matches}

        else:
            return {"error": f"Tool '{tool_name}' is not recognized"}

    except Exception as e:
        logger.exception("Error executing tool %s: %s", tool_name, str(e))
        return {"error": f"Failed to execute {tool_name}"}

