import json
import time
import logging
from typing import Dict, Any, Optional
from flask import current_app
from app.extensions import db
from app.models import AIUsage
from app.services.ai.client import get_ai_client, BaseAIClient
from app.services.ai.prompts import format_system_prompt_with_context
from app.services.ai.tools import TOOL_DEFINITIONS, execute_tool
from app.services.ai.conversation import (
    get_or_create_conversation,
    add_message,
    get_conversation_history,
)
from app.services.ai.usage import calculate_token_cost
from app.services.rag.retrieval import search_similar_chunks

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 4


def process_chat_message(
    user_id: int,
    tenant_id: int,
    message: str,
    conversation_id: Optional[str] = None,
    client: Optional[BaseAIClient] = None,
) -> Dict[str, Any]:
    """
    Orchestrate an end-to-end Billing Intelligence chat turn.
    - Resolves conversation
    - Injects safety guardrails & RAG context
    - Dispatches to LLM with tool calling
    - Executes tools deterministically
    - Records usage and audit trail
    """
    start_time = time.perf_counter()

    if not message or not isinstance(message, str) or not message.strip():
        raise ValueError("Message cannot be empty")

    user_text = message.strip()

    # 1. Conversation resolution
    conv = get_or_create_conversation(
        user_id=user_id,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        title=user_text[:50]
    )

    # 2. Persist user message
    add_message(
        conversation_id=conv.id,
        role='user',
        content=user_text
    )

    # 3. Retrieve relevant documentation for context (RAG)
    try:
        rag_chunks = search_similar_chunks(query=user_text, tenant_id=tenant_id, top_k=2)
    except Exception as e:
        logger.warning("RAG retrieval failed: %s", str(e))
        rag_chunks = []

    system_prompt = format_system_prompt_with_context(rag_chunks)

    # 4. Construct messages payload
    history = get_conversation_history(conv.id, limit=12)

    api_messages = [{'role': 'system', 'content': system_prompt}]
    for h in history:
        msg_obj = {'role': h['role']}
        if 'content' in h and h['content'] is not None:
            msg_obj['content'] = h['content']
        # Tool-call declarations must be replayed with their corresponding tool results.
        if h.get('tool_calls'):
            msg_obj['tool_calls'] = h['tool_calls']
        if 'tool_call_id' in h and h['tool_call_id']:
            msg_obj['tool_call_id'] = h['tool_call_id']
        if 'name' in h and h['name']:
            msg_obj['name'] = h['name']
        api_messages.append(msg_obj)

    ai_client = client or get_ai_client()
    model_name = current_app.config.get('AI_MODEL', 'gpt-4o-mini')

    total_prompt_tokens = 0
    total_completion_tokens = 0
    executed_tool_calls = []
    final_content = ""

    # 5. LLM Call loop with tool calling support
    for iteration in range(MAX_TOOL_ITERATIONS):
        response = ai_client.chat_completion(
            messages=api_messages,
            tools=TOOL_DEFINITIONS,
            model=model_name
        )

        prompt_tokens = response.get('prompt_tokens', 0)
        comp_tokens = response.get('completion_tokens', 0)
        total_prompt_tokens += prompt_tokens
        total_completion_tokens += comp_tokens

        tool_calls = response.get('tool_calls', [])
        content = response.get('content')

        if not tool_calls:
            final_content = content or "I have processed your request."
            break

        # Assistant message requesting tool call(s)
        # Reconstruct the official OpenAI assistant tool-call message.
        assistant_tool_calls = [
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
            for tc in tool_calls
        ]

        assistant_tool_message = {
            'role': 'assistant',
            'content': content,
            'tool_calls': assistant_tool_calls
        }

        api_messages.append(assistant_tool_message)

        # Persist the assistant declaration before persisting tool responses.
        add_message(
            conversation_id=conv.id,
            role='assistant',
            content=content,
            tool_calls=tool_calls
        )

        for tc in tool_calls:
            tc_id = tc['id']
            tc_name = tc['name']
            tc_args = tc.get('arguments', {})

            # Execute tool deterministically with server context
            tool_result = execute_tool(
                tool_name=tc_name,
                raw_arguments=tc_args,
                user_id=user_id,
                tenant_id=tenant_id
            )

            executed_tool_calls.append({
                'tool_name': tc_name,
                'arguments': tc_args,
                'result': tool_result
            })

            # Record in DB & message array
            add_message(
                conversation_id=conv.id,
                role='tool',
                content=json.dumps(tool_result),
                tool_call_id=tc_id,
                tool_name=tc_name,
                tool_arguments=tc_args
            )

            api_messages.append({
                'role': 'tool',
                'tool_call_id': tc_id,
                'name': tc_name,
                'content': json.dumps(tool_result)
            })

    # If the loop ended after tool calls without final content, do a final turn
    if not final_content and executed_tool_calls:
        try:
            final_resp = ai_client.chat_completion(
                messages=api_messages,
                tools=None,
                model=model_name
            )
            final_content = final_resp.get('content', "Here is the requested billing information.")
            total_prompt_tokens += final_resp.get('prompt_tokens', 0)
            total_completion_tokens += final_resp.get('completion_tokens', 0)
        except Exception as e:
            logger.warning("Final synthesis turn failed: %s", str(e))
            final_content = "Here is the requested billing information based on your workspace records."

    # 6. Persist assistant final response
    add_message(
        conversation_id=conv.id,
        role='assistant',
        content=final_content
    )

    elapsed_ms = int((time.perf_counter() - start_time) * 1000)

    # 7. Record AI Usage
    total_tokens = total_prompt_tokens + total_completion_tokens
    cost = calculate_token_cost(model_name, total_prompt_tokens, total_completion_tokens)

    usage = AIUsage(
        conversation_id=conv.id,
        tenant_id=tenant_id,
        user_id=user_id,
        model=model_name,
        prompt_tokens=total_prompt_tokens,
        completion_tokens=total_completion_tokens,
        total_tokens=total_tokens,
        estimated_cost_usd=cost,
        latency_ms=elapsed_ms
    )
    db.session.add(usage)
    db.session.commit()

    return {
        'conversation_id': conv.id,
        'response': final_content,
        'tool_calls': executed_tool_calls,
        'usage': {
            'prompt_tokens': total_prompt_tokens,
            'completion_tokens': total_completion_tokens,
            'total_tokens': total_tokens,
            'estimated_cost_usd': str(cost),
            'latency_ms': elapsed_ms
        }
    }

