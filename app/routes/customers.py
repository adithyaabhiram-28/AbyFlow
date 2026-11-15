
from flask import Blueprint, jsonify, request, g

from app.services.tenant_authorization import tenant_required
from app.services.customer_service import (
    CustomerAlreadyExistsError,
    CustomerNotFoundError,
    CustomerValidationError,
    list_customers,
    get_customer,
    create_customer,
    update_customer,
    delete_customer,
)

customer_bp = Blueprint('customers', __name__)


def serialize_customer(customer):
    """Convert a Customer model into an API response dictionary."""
    return {
        'id': customer.id,
        'tenant_id': customer.tenant_id,
        'name': customer.name,
        'email': customer.email,
        'company_name': customer.company_name,
        'phone': customer.phone,
        'created_at': (
            customer.created_at.isoformat()
            if customer.created_at else None
        ),
        'updated_at': (
            customer.updated_at.isoformat()
            if customer.updated_at else None
        ),
    }


@customer_bp.route('', methods=['POST'])
@tenant_required()
def create_customer_route():
    """Create a customer in the authenticated tenant."""
    data = request.get_json(silent=True)

    try:
        customer = create_customer(
            g.current_tenant.id,
            data,
        )

        return jsonify({
            'message': 'Customer created successfully',
            'customer': serialize_customer(customer),
        }), 201

    except CustomerValidationError as error:
        return jsonify({'error': str(error)}), 400

    except CustomerAlreadyExistsError as error:
        return jsonify({'error': str(error)}), 409


@customer_bp.route('', methods=['GET'])
@tenant_required()
def list_customers_route():
    """List customers belonging to the authenticated tenant."""
    customers = list_customers(g.current_tenant.id)

    return jsonify({
        'customers': [
            serialize_customer(customer)
            for customer in customers
        ],
        'count': len(customers),
    }), 200


@customer_bp.route('/<int:customer_id>', methods=['GET'])
@tenant_required()
def get_customer_route(customer_id):
    """Retrieve one customer within the authenticated tenant."""
    try:
        customer = get_customer(
            g.current_tenant.id,
            customer_id,
        )

        return jsonify({
            'customer': serialize_customer(customer),
        }), 200

    except CustomerNotFoundError as error:
        return jsonify({'error': str(error)}), 404


@customer_bp.route('/<int:customer_id>', methods=['PATCH'])
@tenant_required()
def update_customer_route(customer_id):
    """Update a customer belonging to the authenticated tenant."""
    data = request.get_json(silent=True)

    try:
        customer = update_customer(
            g.current_tenant.id,
            customer_id,
            data,
        )

        return jsonify({
            'message': 'Customer updated successfully',
            'customer': serialize_customer(customer),
        }), 200

    except CustomerValidationError as error:
        return jsonify({'error': str(error)}), 400

    except CustomerNotFoundError as error:
        return jsonify({'error': str(error)}), 404

    except CustomerAlreadyExistsError as error:
        return jsonify({'error': str(error)}), 409


@customer_bp.route('/<int:customer_id>', methods=['DELETE'])
@tenant_required(allowed_roles={'owner', 'admin'})
def delete_customer_route(customer_id):
    """Delete a customer; restricted to owners and admins."""
    try:
        delete_customer(
            g.current_tenant.id,
            customer_id,
        )

        return jsonify({
            'message': 'Customer deleted successfully',
        }), 200

    except CustomerNotFoundError as error:
        return jsonify({'error': str(error)}), 404
