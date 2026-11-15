import logging
from flask import Blueprint, request, jsonify, g
from app.services.tenant_authorization import tenant_required
from app.services.platform_authorization import platform_role_required
from app.services.ai.assistant import process_chat_message
from app.services.ai.conversation import list_conversations, get_conversation_history
from app.services.ai.client import AIClientError, AIProviderError, AITimeoutError
from app.services.rag.ingestion import seed_platform_documentation
from app.models import AIConversation, AIUsage

logger = logging.getLogger(__name__)

ai_bp = Blueprint('ai', __name__)


@ai_bp.route('/chat', methods=['POST'])
@tenant_required()
def chat_route():
    """Chat endpoint for the AI Billing Assistant."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'Invalid JSON request body'}), 400

    message = data.get('message')
    if not message or not isinstance(message, str) or not message.strip():
        return jsonify({'error': 'Message is required and must be non-empty'}), 400

    conversation_id = data.get('conversation_id')
    if conversation_id and not isinstance(conversation_id, str):
        return jsonify({'error': 'conversation_id must be a string'}), 400

    try:
        result = process_chat_message(
            user_id=g.current_user.id,
            tenant_id=g.current_tenant.id,
            message=message.strip(),
            conversation_id=conversation_id
        )
        return jsonify(result), 200

    except AITimeoutError:
        return jsonify({'error': 'AI service timed out. Please try again.'}), 504
    except AIProviderError as e:
        return jsonify({'error': str(e)}), 502
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.exception("Unexpected error in /api/ai/chat: %s", str(e))
        return jsonify({'error': 'An internal error occurred processing your request'}), 500


@ai_bp.route('/conversations', methods=['GET'])
@tenant_required()
def list_conversations_route():
    """List recent AI conversations for the authenticated user and workspace."""
    limit = request.args.get('limit', 20)
    try:
        limit = int(limit)
    except (ValueError, TypeError):
        limit = 20

    conversations = list_conversations(
        user_id=g.current_user.id,
        tenant_id=g.current_tenant.id,
        limit=limit
    )
    return jsonify({
        'conversations': conversations,
        'count': len(conversations)
    }), 200


@ai_bp.route('/conversations/<string:conversation_id>', methods=['GET'])
@tenant_required()
def get_conversation_route(conversation_id):
    """Retrieve message history for an AI conversation."""
    conv = AIConversation.query.filter_by(
        id=conversation_id,
        tenant_id=g.current_tenant.id,
        user_id=g.current_user.id
    ).first()

    if not conv:
        return jsonify({'error': 'Conversation not found'}), 404

    history = get_conversation_history(conversation_id=conversation_id, limit=50)

    return jsonify({
        'conversation': {
            'id': conv.id,
            'title': conv.title,
            'status': conv.status,
            'created_at': conv.created_at.isoformat() if conv.created_at else None,
            'updated_at': conv.updated_at.isoformat() if conv.updated_at else None,
            'messages': history
        }
    }), 200


@ai_bp.route('/usage', methods=['GET'])
@tenant_required()
def get_usage_route():
    """Retrieve AI token and cost metrics for the current workspace."""
    records = (
        AIUsage.query
        .filter_by(tenant_id=g.current_tenant.id)
        .order_by(AIUsage.created_at.desc())
        .limit(50)
        .all()
    )

    total_tokens = sum(r.total_tokens for r in records)
    total_cost = sum(r.estimated_cost_usd for r in records)

    return jsonify({
        'total_tokens': total_tokens,
        'total_cost_usd': str(total_cost),
        'recent_queries_count': len(records),
        'records': [
            {
                'id': r.id,
                'model': r.model,
                'prompt_tokens': r.prompt_tokens,
                'completion_tokens': r.completion_tokens,
                'total_tokens': r.total_tokens,
                'estimated_cost_usd': str(r.estimated_cost_usd),
                'latency_ms': r.latency_ms,
                'created_at': r.created_at.isoformat() if r.created_at else None
            }
            for r in records[:10]
        ]
    }), 200


@ai_bp.route('/knowledge/seed', methods=['POST'])
@platform_role_required({'operator', 'admin'})
def seed_knowledge_route():
    """Seed baseline knowledge documentation (operator/admin only)."""
    docs = seed_platform_documentation()
    return jsonify({
        'message': f"Successfully seeded {len(docs)} platform documents",
        'documents': [
            {'title': d.title, 'slug': d.slug, 'category': d.category}
            for d in docs
        ]
    }), 200

