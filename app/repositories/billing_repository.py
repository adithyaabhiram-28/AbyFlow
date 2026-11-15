from datetime import datetime, timedelta, timezone
from decimal import Decimal
from sqlalchemy import func, select
from app.extensions import db
from app.models import User, Tenant, Customer, Invoice, Payment


class BillingRepository:
    """
    Deterministic billing query repository.
    Strictly scoped by tenant_id and user_id.
    Never allows arbitrary or unvalidated queries.
    """

    @staticmethod
    def get_billing_overview(user_id: int, tenant_id: int) -> dict:
        user = User.query.filter_by(id=user_id).first()
        tenant = Tenant.query.filter_by(id=tenant_id).first()

        # Count customer records
        customer_count = Customer.query.filter_by(tenant_id=tenant_id).count()

        # Count invoices and sum totals
        invoices = Invoice.query.filter_by(tenant_id=tenant_id).all()
        invoice_count = len(invoices)
        total_invoiced = sum((inv.total for inv in invoices), Decimal('0.00'))

        # Count successful payments and total paid cents
        payments = Payment.query.filter_by(user_id=user_id, status='succeeded').all()
        payment_count = len(payments)
        total_paid_cents = sum((p.amount_cents for p in payments), 0)
        total_paid_usd = f"{Decimal(total_paid_cents) / Decimal(100):.2f}"

        return {
            'user_email': user.email if user else None,
            'plan_tier': user.plan_tier if user else 'free',
            'platform_role': user.platform_role if user else 'user',
            'tenant_name': tenant.name if tenant else None,
            'tenant_slug': tenant.slug if tenant else None,
            'customer_count': customer_count,
            'invoice_count': invoice_count,
            'total_invoiced_usd': str(total_invoiced),
            'successful_payments_count': payment_count,
            'total_paid_usd': total_paid_usd,
            'currency': 'USD',
        }

    @staticmethod
    def get_invoice_summary(tenant_id: int, limit: int = 5, status: str = None) -> list:
        limit = max(1, min(limit or 5, 50))
        query = Invoice.query.filter_by(tenant_id=tenant_id)
        if status and status in ('draft', 'issued', 'paid', 'overdue', 'cancelled'):
            query = query.filter_by(status=status)

        invoices = (
            query
            .order_by(Invoice.created_at.desc(), Invoice.id.desc())
            .limit(limit)
            .all()
        )

        return [
            {
                'invoice_number': inv.invoice_number,
                'customer_id': inv.customer_id,
                'status': inv.status,
                'currency': inv.currency,
                'issue_date': inv.issue_date.isoformat() if inv.issue_date else None,
                'due_date': inv.due_date.isoformat() if inv.due_date else None,
                'subtotal': str(inv.subtotal),
                'tax_rate': str(inv.tax_rate),
                'tax_amount': str(inv.tax_amount),
                'total': str(inv.total),
                'item_count': len(inv.items),
            }
            for inv in invoices
        ]

    @staticmethod
    def get_customer_summary(tenant_id: int, limit: int = 10) -> list:
        limit = max(1, min(limit or 10, 50))
        customers = (
            Customer.query
            .filter_by(tenant_id=tenant_id)
            .order_by(Customer.created_at.desc(), Customer.id.desc())
            .limit(limit)
            .all()
        )

        return [
            {
                'id': c.id,
                'name': c.name,
                'email': c.email,
                'company_name': c.company_name,
                'phone': c.phone,
                'created_at': c.created_at.isoformat() if c.created_at else None,
            }
            for c in customers
        ]

    @staticmethod
    def get_payment_status(user_id: int, session_id: str = None, limit: int = 5) -> list:
        limit = max(1, min(limit or 5, 20))
        query = Payment.query.filter_by(user_id=user_id)
        if session_id:
            query = query.filter_by(stripe_checkout_session_id=session_id.strip())

        payments = (
            query
            .order_by(Payment.created_at.desc(), Payment.id.desc())
            .limit(limit)
            .all()
        )

        return [
            {
                'stripe_checkout_session_id': p.stripe_checkout_session_id,
                'amount_cents': p.amount_cents,
                'amount_usd': f"{Decimal(p.amount_cents) / Decimal(100):.2f}",
                'currency': p.currency,
                'status': p.status,
                'created_at': p.created_at.isoformat() if p.created_at else None,
            }
            for p in payments
        ]

    @staticmethod
    def explain_invoice(tenant_id: int, invoice_number: str) -> dict:
        if not invoice_number or not isinstance(invoice_number, str):
            return {'error': 'Invalid invoice number format'}

        inv = Invoice.query.filter_by(
            tenant_id=tenant_id,
            invoice_number=invoice_number.strip()
        ).first()

        if not inv:
            return {'error': f"Invoice '{invoice_number}' not found in this tenant"}

        customer = Customer.query.filter_by(id=inv.customer_id, tenant_id=tenant_id).first()

        items = [
            {
                'description': item.description,
                'quantity': str(item.quantity),
                'unit_price': str(item.unit_price),
                'line_total': str(item.line_total),
            }
            for item in inv.items
        ]

        return {
            'invoice_number': inv.invoice_number,
            'customer_name': customer.name if customer else 'Unknown',
            'customer_email': customer.email if customer else None,
            'status': inv.status,
            'currency': inv.currency,
            'issue_date': inv.issue_date.isoformat(),
            'due_date': inv.due_date.isoformat(),
            'subtotal': str(inv.subtotal),
            'tax_rate_percent': f"{inv.tax_rate}%",
            'tax_amount': str(inv.tax_amount),
            'total': str(inv.total),
            'notes': inv.notes,
            'items': items,
        }

    @staticmethod
    def get_billing_activity(user_id: int, tenant_id: int, days: int = 30) -> dict:
        days = max(1, min(days or 30, 365))
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        recent_invoices = (
            Invoice.query
            .filter(Invoice.tenant_id == tenant_id, Invoice.created_at >= cutoff)
            .all()
        )

        recent_payments = (
            Payment.query
            .filter(Payment.user_id == user_id, Payment.created_at >= cutoff)
            .all()
        )

        return {
            'window_days': days,
            'invoices_created_in_window': len(recent_invoices),
            'invoices_total_usd': str(sum((i.total for i in recent_invoices), Decimal('0.00'))),
            'payments_attempted_in_window': len(recent_payments),
            'payments_succeeded_in_window': sum(1 for p in recent_payments if p.status == 'succeeded'),
            'payments_total_usd': f"{Decimal(sum((p.amount_cents for p in recent_payments if p.status == 'succeeded'), 0)) / Decimal(100):.2f}",
        }

