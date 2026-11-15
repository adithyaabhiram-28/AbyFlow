import json
import logging
from typing import List, Dict, Any, Optional
from flask import current_app

logger = logging.getLogger(__name__)


class AIClientError(Exception):
    """Base exception for AI client errors."""


class AIProviderError(AIClientError):
    """Raised when the upstream AI provider returns an error."""


class AITimeoutError(AIClientError):
    """Raised when an AI request times out."""


class BaseAIClient:
    """Abstract AI Client interface."""

    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError


class OpenAIClient(BaseAIClient):
    """Production OpenAI client using official openai SDK."""

    def __init__(self, api_key: str = None, timeout: float = 15.0):
        self.api_key = api_key or current_app.config.get('OPENAI_API_KEY')
        self.timeout = timeout or current_app.config.get('AI_REQUEST_TIMEOUT', 15.0)

        if not self.api_key:
            raise AIClientError("OPENAI_API_KEY is not configured")

        from openai import OpenAI
        self.client = OpenAI(api_key=self.api_key, timeout=self.timeout)

    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        import openai

        model = model or current_app.config.get('AI_MODEL', 'gpt-4o-mini')
        temperature = temperature if temperature is not None else current_app.config.get('AI_TEMPERATURE', 0.0)
        max_tokens = max_tokens or current_app.config.get('AI_MAX_TOKENS', 1024)

        kwargs = {
            'model': model,
            'messages': messages,
            'temperature': temperature,
            'max_tokens': max_tokens,
        }
        if tools:
            kwargs['tools'] = tools
            kwargs['tool_choice'] = 'auto'

        try:
            response = self.client.chat.completions.create(**kwargs)
            choice = response.choices[0]
            message = choice.message

            tool_calls_data = []
            if message.tool_calls:
                for tc in message.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                    except json.JSONDecodeError:
                        args = {}
                    tool_calls_data.append({
                        'id': tc.id,
                        'name': tc.function.name,
                        'arguments': args,
                    })

            return {
                'content': message.content,
                'tool_calls': tool_calls_data,
                'prompt_tokens': response.usage.prompt_tokens if response.usage else 0,
                'completion_tokens': response.usage.completion_tokens if response.usage else 0,
                'total_tokens': response.usage.total_tokens if response.usage else 0,
            }

        except openai.APITimeoutError as e:
            logger.error("OpenAI API request timed out: %s", str(e))
            raise AITimeoutError("AI service request timed out") from e
        except openai.RateLimitError as e:
            logger.error("OpenAI API rate limit exceeded: %s", str(e))
            raise AIProviderError("AI service rate limit exceeded. Please try again shortly.") from e
        except openai.APIError as e:
            logger.error("OpenAI API error: %s", str(e))
            raise AIProviderError(f"AI service provider error: {str(e)}") from e
        except Exception as e:
            logger.exception("Unexpected error communicating with OpenAI: %s", str(e))
            raise AIProviderError("Unexpected error from AI service") from e


class MockAIClient(BaseAIClient):
    """
    Mock AI client for offline testing.
    Responds deterministically based on message content or tool triggers.
    """

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

        # Inspect latest message to determine simulated behavior
        latest_message = messages[-1] if messages else {}
        role = latest_message.get('role')
        content = (latest_message.get('content') or "").lower()

        # If previous message was a tool result, summarize it
        if role == 'tool':
            tool_content = latest_message.get('content', '')
            return {
                'content': f"Here is the verified information: {tool_content[:200]}",
                'tool_calls': [],
                'prompt_tokens': 120,
                'completion_tokens': 35,
                'total_tokens': 155,
            }

        # Check for tool intent in user message
        if 'overview' in content or 'plan' in content or 'spend' in content:
            return {
                'content': None,
                'tool_calls': [{
                    'id': 'call_mock_overview_1',
                    'name': 'get_billing_overview',
                    'arguments': {}
                }],
                'prompt_tokens': 85,
                'completion_tokens': 20,
                'total_tokens': 105,
            }
        elif 'invoice' in content and 'explain' in content:
            return {
                'content': None,
                'tool_calls': [{
                    'id': 'call_mock_explain_1',
                    'name': 'explain_invoice',
                    'arguments': {'invoice_number': 'INV-000001'}
                }],
                'prompt_tokens': 90,
                'completion_tokens': 25,
                'total_tokens': 115,
            }
        elif 'invoices' in content or 'invoice' in content:
            return {
                'content': None,
                'tool_calls': [{
                    'id': 'call_mock_invoices_1',
                    'name': 'get_invoice_summary',
                    'arguments': {'limit': 5}
                }],
                'prompt_tokens': 85,
                'completion_tokens': 22,
                'total_tokens': 107,
            }
        elif 'customer' in content or 'customers' in content:
            return {
                'content': None,
                'tool_calls': [{
                    'id': 'call_mock_cust_1',
                    'name': 'get_customer_summary',
                    'arguments': {'limit': 10}
                }],
                'prompt_tokens': 85,
                'completion_tokens': 20,
                'total_tokens': 105,
            }
        elif 'faq' in content or 'policy' in content or 'refund' in content or 'terms' in content:
            return {
                'content': None,
                'tool_calls': [{
                    'id': 'call_mock_search_1',
                    'name': 'search_billing_docs',
                    'arguments': {'query': content, 'limit': 3}
                }],
                'prompt_tokens': 95,
                'completion_tokens': 28,
                'total_tokens': 123,
            }

        # Default conversational response
        return {
            'content': "Hello! I am your AbyFlow Billing Intelligence assistant. How can I assist you with your billing, invoices, or subscription today?",
            'tool_calls': [],
            'prompt_tokens': 50,
            'completion_tokens': 25,
            'total_tokens': 75,
        }


def get_ai_client() -> BaseAIClient:
    """Factory function resolving either live OpenAI client or MockAIClient."""
    testing = current_app.config.get('TESTING', False)
    api_key = current_app.config.get('OPENAI_API_KEY')

    if testing:
        return MockAIClient()

    is_mock_key = (
        not api_key
        or api_key.startswith('test-')
        or api_key == 'sk-test-dummy'
        or api_key == 'mock-key-for-testing'
    )

    if is_mock_key:
        if current_app.config.get('APP_ENV') == 'production' or current_app.config.get('ENV') == 'production':
            raise AIClientError(
                'A real OPENAI_API_KEY is required in production.'
            )
        return MockAIClient()

    return OpenAIClient(api_key=api_key)

