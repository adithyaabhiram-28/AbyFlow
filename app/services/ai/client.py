import json
import logging
import uuid
from typing import List, Dict, Any, Optional, Tuple

from flask import current_app

logger = logging.getLogger(__name__)


class AIClientError(Exception):
    """Base exception for AI client errors."""


class AIProviderError(AIClientError):
    """Raised when the upstream AI provider returns an error."""


class AITimeoutError(AIClientError):
    """Raised when an AI request times out."""


class BaseAIClient:
    """Provider-independent AI client interface used by the assistant."""

    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError


def _json_schema_for_gemini(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Copy the app's JSON schema while removing OpenAI-only strictness hints."""
    if not isinstance(schema, dict):
        return schema
    cleaned = {}
    for key, value in schema.items():
        if key == 'additionalProperties':
            continue
        if isinstance(value, dict):
            cleaned[key] = _json_schema_for_gemini(value)
        elif isinstance(value, list):
            cleaned[key] = [
                _json_schema_for_gemini(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            cleaned[key] = value
    return cleaned


def _parse_tool_result(content: Any) -> Any:
    if not isinstance(content, str):
        return content
    try:
        return json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return {'text': content}


class GeminiClient(BaseAIClient):
    """Google Gemini client with manual, server-controlled function calling.

    The assistant continues to own tool execution and authorization. This class
    only translates the existing OpenAI-shaped internal message/tool contract
    into Gemini request/response objects.
    """

    def __init__(self, api_key: str = None, timeout: float = None):
        self.api_key = api_key or current_app.config.get('GEMINI_API_KEY')
        configured_timeout = current_app.config.get('AI_REQUEST_TIMEOUT', 15.0)
        self.timeout = float(timeout if timeout is not None else configured_timeout)
        if self.timeout <= 0:
            raise ValueError('AI request timeout must be positive')
        if not self.api_key:
            raise AIClientError('GEMINI_API_KEY is not configured')

        from google import genai
        self.client = genai.Client(api_key=self.api_key, http_options={'timeout': int(self.timeout * 1000)})

    @staticmethod
    def _build_gemini_tools(tools: Optional[List[Dict[str, Any]]], types):
        if not tools:
            return None

        declarations = []
        for item in tools:
            function = item.get('function', {}) if isinstance(item, dict) else {}
            if not function.get('name'):
                continue
            parameters = function.get('parameters') or {
                'type': 'object', 'properties': {}
            }
            declarations.append(types.FunctionDeclaration(
                name=function['name'],
                description=function.get('description', ''),
                parameters_json_schema=_json_schema_for_gemini(parameters),
            ))

        return [types.Tool(function_declarations=declarations)] if declarations else None

    @staticmethod
    def _convert_messages(messages: List[Dict[str, Any]], types) -> Tuple[Optional[str], List[Any]]:
        system_parts = []
        contents = []

        for message in messages or []:
            role = message.get('role')
            content = message.get('content')

            if role == 'system':
                if content:
                    system_parts.append(str(content))
                continue

            if role == 'user':
                if content is not None:
                    contents.append(types.Content(
                        role='user', parts=[types.Part.from_text(text=str(content))]
                    ))
                continue

            if role == 'assistant':
                tool_calls = message.get('tool_calls') or []
                if tool_calls:
                    # Gemini 3 function-call turns can include thought signatures.
                    # Preserve the original candidate Content in DB history and
                    # replay it verbatim instead of reconstructing only the args.
                    saved_content = next((
                        tc.get('provider_content') for tc in tool_calls
                        if tc.get('provider_content')
                    ), None)
                    if saved_content:
                        try:
                            gemini_content = types.Content.model_validate(saved_content)
                            contents.append(gemini_content)
                            continue
                        except Exception:
                            logger.warning('Could not restore saved Gemini function-call content; rebuilding calls.')

                    parts = []
                    if content:
                        parts.append(types.Part.from_text(text=str(content)))
                    for tool_call in tool_calls:
                        function = tool_call.get('function', {})
                        raw_args = function.get('arguments', '{}')
                        try:
                            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                        except json.JSONDecodeError:
                            args = {}
                        parts.append(types.Part.from_function_call(
                            name=function.get('name', tool_call.get('name', '')),
                            args=args or {},
                        ))
                    if parts:
                        contents.append(types.Content(role='model', parts=parts))
                elif content is not None:
                    contents.append(types.Content(
                        role='model', parts=[types.Part.from_text(text=str(content))]
                    ))
                continue

            if role == 'tool':
                tool_name = message.get('name')
                if not tool_name:
                    # Older persisted records can be correlated by tool-call id
                    # only by the caller; fail closed rather than invent a name.
                    logger.warning('Skipping historical tool response without a tool name.')
                    continue
                result = _parse_tool_result(content)
                contents.append(types.Content(
                    role='user',
                    parts=[types.Part.from_function_response(
                        name=tool_name,
                        response={'result': result},
                    )],
                ))

        return ('\n\n'.join(system_parts) if system_parts else None), contents

    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        from google.genai import types

        model = model or current_app.config.get('AI_MODEL', 'gemini-3.8-flash')
        temperature = temperature if temperature is not None else current_app.config.get('AI_TEMPERATURE', 0.0)
        max_tokens = max_tokens or current_app.config.get('AI_MAX_TOKENS', 1024)
        system_instruction, contents = self._convert_messages(messages, types)
        gemini_tools = self._build_gemini_tools(tools, types)

        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=temperature,
            max_output_tokens=max_tokens,
            tools=gemini_tools,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

        try:
            response = self.client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
            candidate_content = None
            if getattr(response, 'candidates', None):
                candidate_content = response.candidates[0].content

            provider_content = None
            if candidate_content is not None:
                provider_content = candidate_content.model_dump(mode='json', exclude_none=True)

            tool_calls_data = []
            for function_call in (getattr(response, 'function_calls', None) or []):
                tool_calls_data.append({
                    'id': str(uuid.uuid4()),
                    'name': function_call.name,
                    'arguments': dict(function_call.args or {}),
                    # Persist raw Gemini Content to preserve any thought signatures.
                    'provider_content': provider_content,
                })

            usage = getattr(response, 'usage_metadata', None)
            prompt_tokens = int(getattr(usage, 'prompt_token_count', 0) or 0)
            completion_tokens = int(getattr(usage, 'candidates_token_count', 0) or 0)
            total_tokens = int(getattr(usage, 'total_token_count', 0) or (prompt_tokens + completion_tokens))

            text_parts = [
                part.text
                for part in (getattr(candidate_content, 'parts', None) or [])
                if getattr(part, 'text', None)
            ]
            response_text = '\n'.join(text_parts) or None

            return {
                'content': response_text,
                'tool_calls': tool_calls_data,
                'prompt_tokens': prompt_tokens,
                'completion_tokens': completion_tokens,
                'total_tokens': total_tokens,
            }
        except TimeoutError as exc:
            logger.error('Gemini API request timed out.')
            raise AITimeoutError('AI service request timed out') from exc
        except Exception as exc:
            from google.genai.errors import APIError
            if isinstance(exc, APIError):
                status_code = getattr(exc, 'code', None)
                if status_code in (408, 504):
                    raise AITimeoutError('AI service request timed out') from exc
                if status_code == 429:
                    logger.warning('Gemini API rate limit reached.')
                    raise AIProviderError('Gemini rate limit reached. Please retry shortly.') from exc
            logger.exception('Gemini API request failed.')
            raise AIProviderError('Unexpected error from Gemini AI service') from exc


class MockAIClient(BaseAIClient):
    """Mock AI client for deterministic offline testing."""

    def __init__(self, responses: Optional[List[Dict[str, Any]]] = None):
        self.responses = responses or []
        self._call_count = 0

    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        self._call_count += 1
        if self.responses and len(self.responses) >= self._call_count:
            return self.responses[self._call_count - 1]

        latest_message = messages[-1] if messages else {}
        role = latest_message.get('role')
        content = (latest_message.get('content') or '').lower()

        if role == 'tool':
            tool_content = latest_message.get('content', '')
            return {
                'content': f'Here is the verified information: {tool_content[:200]}',
                'tool_calls': [], 'prompt_tokens': 120,
                'completion_tokens': 35, 'total_tokens': 155,
            }

        if 'overview' in content or 'plan' in content or 'spend' in content:
            name, args = 'get_billing_overview', {}
        elif 'invoice' in content and 'explain' in content:
            name, args = 'explain_invoice', {'invoice_number': 'INV-000001'}
        elif 'invoices' in content or 'invoice' in content:
            name, args = 'get_invoice_summary', {'limit': 5}
        elif 'customer' in content or 'customers' in content:
            name, args = 'get_customer_summary', {'limit': 10}
        elif any(term in content for term in ('faq', 'policy', 'refund', 'terms')):
            name, args = 'search_billing_docs', {'query': content, 'limit': 3}
        else:
            name, args = None, None

        if name:
            return {
                'content': None,
                'tool_calls': [{'id': 'call_mock_1', 'name': name, 'arguments': args}],
                'prompt_tokens': 85, 'completion_tokens': 20, 'total_tokens': 105,
            }

        return {
            'content': 'Hello! I am your AbyFlow Billing Intelligence assistant. How can I assist you with your billing, invoices, or subscription today?',
            'tool_calls': [], 'prompt_tokens': 50,
            'completion_tokens': 25, 'total_tokens': 75,
        }


def get_ai_client() -> BaseAIClient:
    """Return the mock client in tests and Gemini in configured environments."""
    if current_app.config.get('TESTING', False):
        return MockAIClient()

    api_key = (current_app.config.get('GEMINI_API_KEY') or '').strip()
    is_mock_key = (
        not api_key
        or api_key.startswith('test-')
        or api_key in {'mock-key-for-testing', 'test-gemini-key'}
    )
    if is_mock_key:
        if current_app.config.get('APP_ENV') == 'production' or current_app.config.get('ENV') == 'production':
            raise AIClientError('A real GEMINI_API_KEY is required in production.')
        return MockAIClient()

    return GeminiClient(api_key=api_key)
