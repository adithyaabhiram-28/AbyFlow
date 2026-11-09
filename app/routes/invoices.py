
from flask import Blueprint, jsonify, request, g

from app.services.tenant_authorization import tenant_required
from app.services.invoice_service import (
    InvoiceNotFoundError,
    InvoiceValidationError,
    InvoiceCustomerNotFoundError,
    InvoiceStateError,
    create_invoice,
    get_invoice,
    list_invoices,
    update_invoice,
    delete_invoice,
    issue_invoice,
)


invoice_bp = Blueprint('invoices', __name__)


def serialize_invoice_item(item):
    """Convert an invoice item into a JSON-safe dictionary."""
    return {
        'id': item.id,
        'description': item.description,
        'quantity': str(item.quantity),
        'unit_price': str(item.unit_price),
        'line_total': str(item.line_total),
    }


def serialize_invoice(invoice):
    """Convert an invoice and its items into a JSON-safe dictionary."""
    return {
        'id': invoice.id,
        'tenant_id': invoice.tenant_id,
        'customer_id': invoice.customer_id,
        'invoice_number': invoice.invoice_number,
        'status': invoice.status,
        'currency': invoice.currency,
        'issue_date': invoice.issue_date.isoformat(),
        'due_date': invoice.due_date.isoformat(),
        'subtotal': str(invoice.subtotal),
        'tax_rate': str(invoice.tax_rate),
        'tax_amount': str(invoice.tax_amount),
        'total': str(invoice.total),
        'notes': invoice.notes,
        'items': [
            serialize_invoice_item(item)
            for item in invoice.items
        ],
        'created_at': (
            invoice.created_at.isoformat()
            if invoice.created_at else None
        ),
        'updated_at': (
            invoice.updated_at.isoformat()
            if invoice.updated_at else None
        ),
    }


@invoice_bp.route('', methods=['POST'])
@tenant_required()
def create_invoice_route():
    """Create a draft invoice in the active tenant."""
    data = request.get_json(silent=True)

    try:
        invoice = create_invoice(
            g.current_tenant.id,
            data,
        )

        return jsonify({
            'message': 'Invoice created successfully',
            'invoice': serialize_invoice(invoice),
        }), 201

    except InvoiceValidationError as error:
        return jsonify({'error': str(error)}), 400

    except InvoiceCustomerNotFoundError as error:
        return jsonify({'error': str(error)}), 404


@invoice_bp.route('', methods=['GET'])
@tenant_required()
def list_invoices_route():
    """List invoices belonging to the active tenant."""
    try:
        result = list_invoices(
            tenant_id=g.current_tenant.id,
            page=request.args.get('page', 1),
            per_page=request.args.get('per_page', 20),
            status=request.args.get('status'),
        )

        return jsonify({
            'invoices': [
                serialize_invoice(invoice)
                for invoice in result['invoices']
            ],
            'pagination': {
                'total': result['total'],
                'page': result['page'],
                'per_page': result['per_page'],
                'pages': result['pages'],
            },
        }), 200

    except InvoiceValidationError as error:
        return jsonify({'error': str(error)}), 400


@invoice_bp.route('/<int:invoice_id>', methods=['GET'])
@tenant_required()
def get_invoice_route(invoice_id):
    """Retrieve an invoice within the active tenant."""
    try:
        invoice = get_invoice(
            g.current_tenant.id,
            invoice_id,
        )

        return jsonify({
            'invoice': serialize_invoice(invoice),
        }), 200

    except InvoiceNotFoundError as error:
        return jsonify({'error': str(error)}), 404


@invoice_bp.route('/<int:invoice_id>', methods=['PATCH'])
@tenant_required()
def update_invoice_route(invoice_id):
    """Update a draft invoice within the active tenant."""
    data = request.get_json(silent=True)

    try:
        invoice = update_invoice(
            g.current_tenant.id,
            invoice_id,
            data,
        )

        return jsonify({
            'message': 'Invoice updated successfully',
            'invoice': serialize_invoice(invoice),
        }), 200

    except InvoiceValidationError as error:
        return jsonify({'error': str(error)}), 400

    except InvoiceCustomerNotFoundError as error:
        return jsonify({'error': str(error)}), 404

    except InvoiceNotFoundError as error:
        return jsonify({'error': str(error)}), 404

    except InvoiceStateError as error:
        return jsonify({'error': str(error)}), 409


@invoice_bp.route('/<int:invoice_id>', methods=['DELETE'])
@tenant_required(allowed_roles={'owner', 'admin'})
def delete_invoice_route(invoice_id):
    """Delete a draft invoice; restricted to owners and admins."""
    try:
        delete_invoice(
            g.current_tenant.id,
            invoice_id,
        )

        return jsonify({
            'message': 'Invoice deleted successfully',
        }), 200

    except InvoiceNotFoundError as error:
        return jsonify({'error': str(error)}), 404

    except InvoiceStateError as error:
        return jsonify({'error': str(error)}), 409


@invoice_bp.route('/<int:invoice_id>/issue', methods=['POST'])
@tenant_required()
def issue_invoice_route(invoice_id):
    """Issue a draft invoice within the active tenant."""
    try:
        invoice = issue_invoice(
            g.current_tenant.id,
            invoice_id,
        )

        return jsonify({
            'message': 'Invoice issued successfully',
            'invoice': serialize_invoice(invoice),
        }), 200

    except InvoiceNotFoundError as error:
        return jsonify({'error': str(error)}), 404

    except InvoiceValidationError as error:
        return jsonify({'error': str(error)}), 400

    except InvoiceStateError as error:
        return jsonify({'error': str(error)}), 409
