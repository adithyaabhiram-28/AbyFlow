import uuid
from typing import Optional, List, Dict, Any
from app.extensions import db
from app.models import AIConversation, AIMessage
import json

def get_or_create_conversation(
    user_id: int,
    tenant_id: int,
    conversation_id: Optional[str] = None,
    title: str = "Billing Inquiry"
) -> AIConversation:
    """Retrieve an existing conversation or create a new one."""
    if conversation_id:
        conv = AIConversation.query.filter_by(
            id=conversation_id,
            tenant_id=tenant_id,
            user_id=user_id
        ).first()
        if conv:
            return conv
        # A supplied ID represents an existing conversation request; never
        # silently create a different conversation when it is unknown or unowned.
        raise ValueError('Conversation not found')

    # Create new conversation with a UUID only when no ID was supplied.
    new_id = str(uuid.uuid4())
    conv = AIConversation(
        id=new_id,
        tenant_id=tenant_id,
        user_id=user_id,
        title=title[:200] if title else "Billing Inquiry",
        status="active"
    )
    db.session.add(conv)
    db.session.commit()
    return conv


def add_message(
    conversation_id: str,
    role: str,
    content: Optional[str] = None,
    tool_call_id: Optional[str] = None,
    tool_name: Optional[str] = None,
    tool_arguments: Optional[dict] = None,
    tool_calls: Optional[List[Dict[str, Any]]] = None
) -> AIMessage:
    """Persist a message in the conversation history."""
    if role not in ('system', 'user', 'assistant', 'tool'):
        raise ValueError(f"Invalid message role: {role}")

    msg = AIMessage(
        conversation_id=conversation_id,
        role=role,
        content=content,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        tool_arguments=tool_calls if tool_calls is not None else tool_arguments
    )
    db.session.add(msg)
    db.session.commit()
    return msg


def get_conversation_history(
    conversation_id: str,
    limit: int = 15
) -> List[Dict[str, Any]]:
    """
    Retrieve conversation history and reconstruct OpenAI-compatible
    assistant tool-call messages.
    """
    limit = max(1, min(int(limit or 15), 100))
    # Select the newest N messages, then restore chronological order for the LLM.
    messages = (
        AIMessage.query
        .filter_by(conversation_id=conversation_id)
        .order_by(AIMessage.created_at.desc(), AIMessage.id.desc())
        .limit(limit)
        .all()
    )
    messages.reverse()

    history = []

    for m in messages:
        entry = {'role': m.role}

        if m.content is not None:
            entry['content'] = m.content

        if m.role == 'assistant' and isinstance(m.tool_arguments, list):
            entry['tool_calls'] = [
                {
                    'id': tc['id'],
                    'type': 'function',
                    'function': {
                        'name': tc['name'],
                        'arguments': json.dumps(
                            tc.get('arguments', {}),
                            separators=(',', ':')
                        )
                    }
                }
                for tc in m.tool_arguments
            ]

        if m.tool_call_id:
            entry['tool_call_id'] = m.tool_call_id

        if m.tool_name:
            entry['name'] = m.tool_name

        history.append(entry)

    return history


def list_conversations(user_id: int, tenant_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    """List recent conversations for a user in a tenant."""
    limit = max(1, min(limit or 20, 50))
    convs = (
        AIConversation.query
        .filter_by(user_id=user_id, tenant_id=tenant_id)
        .order_by(AIConversation.updated_at.desc())
        .limit(limit)
        .all()
    )

    return [
        {
            'id': c.id,
            'title': c.title,
            'status': c.status,
            'message_count': len(c.messages),
            'created_at': c.created_at.isoformat() if c.created_at else None,
            'updated_at': c.updated_at.isoformat() if c.updated_at else None,
        }
        for c in convs
    ]

