import json
import pytest
from datetime import date
from decimal import Decimal
from app.models import (
    User,
    Tenant,
    TenantMembership,
    Customer,
    Invoice,
    Payment,
    AIConversation,
    AIMessage,
    AIUsage,
    KnowledgeDocument,
    DocumentChunk,
)
from app.repositories.billing_repository import BillingRepository
from app.services.rag.chunking import chunk_text
from app.services.rag.embeddings import (
    generate_embedding,
    EMBEDDING_DIM,
    EmbeddingGenerationError,
    _validate_embedding,
)
from app.services.rag.ingestion import ingest_document, seed_platform_documentation
from app.services.rag.retrieval import search_similar_chunks
from app.services.ai.usage import calculate_token_cost
from app.services.ai.tools import (
    TOOL_DEFINITIONS,
    validate_tool_arguments,
    execute_tool,
)
from app.services.ai.client import MockAIClient
from app.services.ai.assistant import process_chat_message
from app.services.ai.conversation import (
    get_or_create_conversation,
    add_message,
    get_conversation_history,
)
from unittest.mock import patch

@pytest.fixture
def workspace_with_data(app, db, registered_user):
    """Seed customer, invoice, and payment into user workspace."""
    membership = TenantMembership.query.filter_by(user_id=registered_user.id).first()
    tenant = membership.tenant

    customer = Customer(
        tenant_id=tenant.id,
        name="Acme Client",
        email="acme@client.com",
        company_name="Acme Inc",
        phone="+15551234567"
    )
    db.session.add(customer)
    db.session.flush()

    invoice = Invoice(
        tenant_id=tenant.id,
        customer_id=customer.id,
        invoice_number="INV-000001",
        status="issued",
        currency="USD",
        issue_date=date(2026, 11, 1),
        due_date=date(2026, 11, 15),
        subtotal=Decimal("100.00"),
        tax_rate=Decimal("10.00"),
        tax_amount=Decimal("10.00"),
        total=Decimal("110.00"),
        notes="Monthly retainer"
    )
    db.session.add(invoice)

    payment = Payment(
        user_id=registered_user.id,
        stripe_checkout_session_id="cs_test_sample_123",
        stripe_payment_intent_id="pi_test_sample_123",
        amount_cents=2000,
        currency="USD",
        status="succeeded"
    )
    db.session.add(payment)
    db.session.commit()

    return {
        'tenant_id': tenant.id,
        'user_id': registered_user.id,
        'customer_id': customer.id,
        'invoice_number': invoice.invoice_number,
    }


# ===================================================================
# 1. Billing Repository Tests
# ===================================================================

def test_billing_repository_overview(app, db, registered_user, workspace_with_data):
    """Repository computes accurate overview numbers."""
    with app.app_context():
        overview = BillingRepository.get_billing_overview(
            user_id=workspace_with_data['user_id'],
            tenant_id=workspace_with_data['tenant_id']
        )
        assert overview['user_email'] == registered_user.email
        assert overview['customer_count'] == 1
        assert overview['invoice_count'] == 1
        assert overview['total_invoiced_usd'] == '110.00'
        assert overview['successful_payments_count'] == 1
        assert overview['total_paid_usd'] == '20.00'


def test_billing_repository_invoice_summary(app, db, workspace_with_data):
    """Repository lists recent invoices with correct metadata."""
    with app.app_context():
        summary = BillingRepository.get_invoice_summary(
            tenant_id=workspace_with_data['tenant_id'],
            limit=5
        )
        assert len(summary) == 1
        assert summary[0]['invoice_number'] == 'INV-000001'
        assert summary[0]['total'] == '110.00'
        assert summary[0]['status'] == 'issued'


def test_billing_repository_explain_invoice(app, db, workspace_with_data):
    """Repository explains an invoice breakdown accurately."""
    with app.app_context():
        explanation = BillingRepository.explain_invoice(
            tenant_id=workspace_with_data['tenant_id'],
            invoice_number='INV-000001'
        )
        assert explanation['invoice_number'] == 'INV-000001'
        assert explanation['subtotal'] == '100.00'
        assert explanation['tax_amount'] == '10.00'
        assert explanation['total'] == '110.00'
        assert explanation['customer_name'] == 'Acme Client'

        # Nonexistent invoice in tenant returns error
        not_found = BillingRepository.explain_invoice(
            tenant_id=workspace_with_data['tenant_id'],
            invoice_number='INV-999999'
        )
        assert 'not found' in not_found['error']


def test_billing_repository_payment_status(app, db, workspace_with_data):
    """Repository retrieves payment history."""
    with app.app_context():
        payments = BillingRepository.get_payment_status(
            user_id=workspace_with_data['user_id']
        )
        assert len(payments) == 1
        assert payments[0]['amount_cents'] == 2000
        assert payments[0]['amount_usd'] == '20.00'
        assert payments[0]['status'] == 'succeeded'


# ===================================================================
# 2. RAG Chunking and Embeddings Tests
# ===================================================================

def test_chunk_text_basic():
    """Text chunker breaks paragraphs and respects boundaries."""
    text = "Paragraph one with some text.\n\nParagraph two with more details.\n\nParagraph three."
    chunks = chunk_text(text, chunk_size=40, chunk_overlap=10)
    assert len(chunks) >= 2
    for c in chunks:
        assert len(c) > 0




def test_chunk_text_respects_maximum_size_for_long_sentences():
    text = "A" * 1200
    chunks = chunk_text(text, chunk_size=100, chunk_overlap=20)
    assert len(chunks) > 1
    assert all(0 < len(chunk) <= 100 for chunk in chunks)


@pytest.mark.parametrize(
    "chunk_size,chunk_overlap",
    [(0, 0), (-1, 0), (10, -1), (10, 10), (10, 11), (True, 0), (10, False)],
)
def test_chunk_text_rejects_invalid_chunk_parameters(chunk_size, chunk_overlap):
    with pytest.raises(ValueError):
        chunk_text("Some source text that needs chunking.", chunk_size, chunk_overlap)


def test_chunk_text_handles_empty_and_whitespace_input():
    assert chunk_text("") == []
    assert chunk_text("   ") == []



def test_chunk_text_respects_maximum_size_for_long_sentences():
    text = "A" * 1200
    chunks = chunk_text(text, chunk_size=100, chunk_overlap=20)
    assert len(chunks) > 1
    assert all(0 < len(chunk) <= 100 for chunk in chunks)


@pytest.mark.parametrize(
    "chunk_size,chunk_overlap",
    [(0, 0), (-1, 0), (10, -1), (10, 10), (10, 11), (True, 0), (10, False)],
)
def test_chunk_text_rejects_invalid_chunk_parameters(chunk_size, chunk_overlap):
    with pytest.raises(ValueError):
        chunk_text("Some source text that needs chunking.", chunk_size, chunk_overlap)


def test_chunk_text_handles_empty_and_whitespace_input():
    assert chunk_text("") == []
    assert chunk_text("   ") == []

def test_generate_embedding_dimension(app):
    """Embedding generator returns 1536-dimensional unit vector."""
    vec = generate_embedding("AbyFlow Pro plan pricing")
    assert len(vec) == EMBEDDING_DIM
    # Check approximately unit norm
    norm = sum(x * x for x in vec) ** 0.5
    assert abs(norm - 1.0) < 1e-4


def test_embedding_requires_api_key_outside_testing(app):
    """Real embeddings require credentials outside testing and mock mode."""
    app.config.update(
        TESTING=False,
        AI_ALLOW_MOCK_EMBEDDINGS=False,
        GEMINI_API_KEY=None,
    )

    with app.app_context():
        with pytest.raises(EmbeddingGenerationError, match="GEMINI_API_KEY"):
            generate_embedding("AbyFlow billing")


def test_embedding_allows_explicit_development_mock(app):
    """Development can explicitly enable deterministic mock embeddings."""
    app.config.update(
        TESTING=False,
        AI_ALLOW_MOCK_EMBEDDINGS=True,
        GEMINI_API_KEY=None,
    )

    with app.app_context():
        vector = generate_embedding("AbyFlow billing")

    assert len(vector) == EMBEDDING_DIM
    assert vector == generate_embedding_for_same_input(app)


def generate_embedding_for_same_input(app):
    with app.app_context():
        return generate_embedding("AbyFlow billing")


def test_embedding_rejects_wrong_dimensions():
    """Embedding vectors must have exactly 1536 dimensions."""
    with pytest.raises(EmbeddingGenerationError, match="1536"):
        _validate_embedding([0.1, 0.2, 0.3])


@pytest.mark.parametrize("invalid_value", [float("nan"), float("inf"), float("-inf")])
def test_embedding_rejects_non_finite_values(invalid_value):
    """Embedding vectors cannot contain NaN or infinite values."""
    vector = [0.1] * EMBEDDING_DIM
    vector[0] = invalid_value

    with pytest.raises(EmbeddingGenerationError, match="invalid numeric"):
        _validate_embedding(vector)


def test_embedding_api_failure_does_not_fallback_to_mock(app):
    """Gemini failures must propagate instead of silently generating mock vectors."""
    from unittest.mock import patch

    app.config.update(
        TESTING=False,
        AI_ALLOW_MOCK_EMBEDDINGS=False,
        GEMINI_API_KEY="AIzaSy-test-placeholder",
    )

    with app.app_context():
        with patch("google.genai.Client") as mock_gemini:
            mock_client = mock_gemini.return_value
            mock_client.models.embed_content.side_effect = RuntimeError(
                "Simulated provider failure"
            )

            with pytest.raises(
                EmbeddingGenerationError,
                match="configured provider",
            ):
                generate_embedding("AbyFlow billing")

# ===================================================================
# 3. Knowledge Ingestion & Tenant Document Isolation Tests
# ===================================================================

def test_tenant_aware_document_isolation(app, db, registered_user):
    """
    RAG strict boundary test:
    Tenant A must NEVER retrieve Tenant B's private documents.
    Both tenants can retrieve global system documents (tenant_id=None).
    """
    with app.app_context():
        membership = TenantMembership.query.filter_by(user_id=registered_user.id).first()
        t1_id = membership.tenant_id

        tenant2 = Tenant(name="Tenant Two Workspace", slug="tenant-two-workspace")
        db.session.add(tenant2)
        db.session.commit()
        t2_id = tenant2.id

        # Global platform doc
        ingest_document(
            title="Public FAQ",
            slug="public-faq",
            content="AbyFlow provides automatic invoice sequential numbering for all workspaces.",
            category="faq",
            tenant_id=None
        )

        # Tenant 1 private doc
        ingest_document(
            title="Tenant 1 Secret Contract",
            slug="tenant-1-contract",
            content="Confidential special discount rate of 50 percent for Tenant One.",
            category="tenant_doc",
            tenant_id=t1_id
        )

        # Tenant 2 private doc
        ingest_document(
            title="Tenant 2 Secret Roadmap",
            slug="tenant-2-roadmap",
            content="Secret product plans and internal credentials for Tenant Two.",
            category="tenant_doc",
            tenant_id=t2_id
        )

        # Query as Tenant 1
        t1_results = search_similar_chunks("Confidential special discount", tenant_id=t1_id, top_k=5)
        t1_slugs = [r['document_slug'] for r in t1_results]
        assert "tenant-1-contract" in t1_slugs
        assert "tenant-2-roadmap" not in t1_slugs

        # Query as Tenant 2
        t2_results = search_similar_chunks("Secret product plans", tenant_id=t2_id, top_k=5)
        t2_slugs = [r['document_slug'] for r in t2_results]
        assert "tenant-2-roadmap" in t2_slugs
        assert "tenant-1-contract" not in t2_slugs

        # Both can see public docs
        t1_public = search_similar_chunks("automatic invoice sequential numbering", tenant_id=t1_id, top_k=5)
        assert any(r['document_slug'] == 'public-faq' for r in t1_public)

        t2_public = search_similar_chunks("automatic invoice sequential numbering", tenant_id=t2_id, top_k=5)
        assert any(r['document_slug'] == 'public-faq' for r in t2_public)


def test_ingestion_failure_preserves_existing_document(app, db):
    """Failed replacement must preserve the original document and chunks."""
    from app.models import KnowledgeDocument, DocumentChunk

    with app.app_context():
        original = ingest_document(
            title="Original Policy",
            slug="rollback-policy",
            content="Original policy content that must remain unchanged.",
        )

        original_id = original.id
        original_chunks = [
            chunk.content
            for chunk in DocumentChunk.query.filter_by(
                document_id=original_id
            ).order_by(DocumentChunk.chunk_index).all()
        ]

        with patch(
            "app.services.rag.ingestion.generate_embedding",
            side_effect=RuntimeError("Simulated embedding failure"),
        ):
            with pytest.raises(RuntimeError, match="Simulated embedding failure"):
                ingest_document(
                    title="Updated Policy",
                    slug="rollback-policy",
                    content="Replacement content that must not be stored.",
                )

        db.session.expire_all()

        restored = db.session.get(KnowledgeDocument, original_id)
        restored_chunks = [
            chunk.content
            for chunk in DocumentChunk.query.filter_by(
                document_id=original_id
            ).order_by(DocumentChunk.chunk_index).all()
        ]

        assert restored is not None
        assert restored.title == "Original Policy"
        assert restored_chunks == original_chunks


def test_ingestion_failure_rolls_back_new_document(app, db):
    """Failed initial ingestion must leave no partial document or chunks."""
    from app.models import KnowledgeDocument, DocumentChunk

    with app.app_context():
        with patch(
            "app.services.rag.ingestion.generate_embedding",
            side_effect=RuntimeError("Simulated embedding failure"),
        ):
            with pytest.raises(RuntimeError, match="Simulated embedding failure"):
                ingest_document(
                    title="Incomplete Policy",
                    slug="incomplete-policy",
                    content="This document must not be partially stored.",
                )

        assert KnowledgeDocument.query.filter_by(
            tenant_id=None,
            slug="incomplete-policy",
            version=1,
        ).first() is None

        assert DocumentChunk.query.count() == 0


# ===================================================================
# 4. Tool Registry and Execution Tests
# ===================================================================

def test_tool_definitions_valid():
    """Tool definitions retain the provider-neutral function schema contract."""
    assert len(TOOL_DEFINITIONS) == 7
    names = [t['function']['name'] for t in TOOL_DEFINITIONS]
    assert 'get_billing_overview' in names
    assert 'get_invoice_summary' in names
    assert 'get_customer_summary' in names
    assert 'get_payment_status' in names
    assert 'explain_invoice' in names
    assert 'get_billing_activity' in names
    assert 'search_billing_docs' in names


def test_tool_validation_safeguards():
    """Tool argument validation rejects invalid types and bad filters."""

    # Unknown tool
    with pytest.raises(ValueError):
        validate_tool_arguments("delete_all_invoices", {})

    # Non-dictionary arguments
    with pytest.raises(ValueError):
        validate_tool_arguments("get_billing_overview", [])

    # Unexpected arguments, including attempts to supply server context
    with pytest.raises(ValueError):
        validate_tool_arguments(
            "get_billing_overview",
            {"tenant_id": 123}
        )

    with pytest.raises(ValueError):
        validate_tool_arguments(
            "get_invoice_summary",
            {"limit": 5, "user_id": 123}
        )

    # Invalid invoice status
    with pytest.raises(ValueError):
        validate_tool_arguments(
            "get_invoice_summary",
            {"status": "hacked_status"}
        )

    with pytest.raises(ValueError):
        validate_tool_arguments(
            "get_invoice_summary",
            {"status": 123}
        )

    # Strict integer validation: reject bools, floats and strings
    for invalid_limit in (True, 3.7, "5"):
        with pytest.raises(ValueError):
            validate_tool_arguments(
                "get_invoice_summary",
                {"limit": invalid_limit}
            )

    # Reject out-of-range values
    for invalid_limit in (0, 51):
        with pytest.raises(ValueError):
            validate_tool_arguments(
                "get_invoice_summary",
                {"limit": invalid_limit}
            )

    # Missing required query
    with pytest.raises(ValueError):
        validate_tool_arguments("search_billing_docs", {})

    # Blank and non-string invoice numbers
    for invalid_invoice in ("", "   ", 123):
        with pytest.raises(ValueError):
            validate_tool_arguments(
                "explain_invoice",
                {"invoice_number": invalid_invoice}
            )

    # Oversized invoice number
    with pytest.raises(ValueError):
        validate_tool_arguments(
            "explain_invoice",
            {"invoice_number": "A" * 101}
        )

    # Invalid payment session IDs
    for invalid_session in (123, "", "   ", "A" * 256):
        with pytest.raises(ValueError):
            validate_tool_arguments(
                "get_payment_status",
                {"session_id": invalid_session}
            )

    # Invalid or oversized documentation queries
    for invalid_query in ("", "   ", 123, "A" * 1001):
        with pytest.raises(ValueError):
            validate_tool_arguments(
                "search_billing_docs",
                {"query": invalid_query}
            )

    # Valid arguments remain accepted
    assert validate_tool_arguments(
        "get_invoice_summary",
        {"limit": 10, "status": "PAID"}
    ) == {"limit": 10, "status": "paid"}

    assert validate_tool_arguments(
        "explain_invoice",
        {"invoice_number": " INV-000001 "}
    ) == {"invoice_number": "INV-000001"}

    assert validate_tool_arguments(
        "get_billing_overview",
        {}
    ) == {}


def test_execute_tool_with_server_context(app, db, workspace_with_data):
    """Tool dispatcher enforces server-side tenant_id and user_id."""
    with app.app_context():
        # overview
        res = execute_tool(
            "get_billing_overview",
            {},
            user_id=workspace_with_data['user_id'],
            tenant_id=workspace_with_data['tenant_id']
        )
        assert res['total_invoiced_usd'] == '110.00'

        # explain invoice
        exp = execute_tool(
            "explain_invoice",
            {"invoice_number": "INV-000001"},
            user_id=workspace_with_data['user_id'],
            tenant_id=workspace_with_data['tenant_id']
        )
        assert exp['subtotal'] == '100.00'


# ===================================================================
# 5. Token and Cost Accounting Tests
# ===================================================================

def test_calculate_token_cost():
    """Exact token cost matches blueprint pricing formula."""
    cost = calculate_token_cost(
        model='gpt-4o-mini',
        prompt_tokens=1000,
        completion_tokens=500
    )
    # (1000 * 0.00000015) + (500 * 0.00000060) = 0.00015 + 0.00030 = 0.000450
    assert cost == Decimal('0.000450')



def test_calculate_gemini_free_tier_token_cost():
    """Gemini Developer API free-tier calls are recorded at zero USD cost."""
    cost = calculate_token_cost(
        model='gemini-3.8-flash',
        prompt_tokens=1000,
        completion_tokens=500,
    )
    assert cost == Decimal('0.000000')


def test_calculate_token_cost_rejects_unconfigured_model():
    with pytest.raises(ValueError, match="No token pricing is configured"):
        calculate_token_cost(
            model='unconfigured-model',
            prompt_tokens=100,
            completion_tokens=50
        )


@pytest.mark.parametrize("prompt_tokens,completion_tokens", [(-1, 0), (0, -1)])
def test_calculate_token_cost_rejects_negative_usage(prompt_tokens, completion_tokens):
    with pytest.raises(ValueError, match="cannot be negative"):
        calculate_token_cost(
            model='gpt-4o-mini',
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens
        )


# ===================================================================
# 6. Assistant Orchestration and Message Flow Tests
# ===================================================================

def test_process_chat_message_flow(app, db, workspace_with_data):
    """End-to-end assistant turn executes tool calling and persists records."""
    with app.app_context():
        result = process_chat_message(
            user_id=workspace_with_data['user_id'],
            tenant_id=workspace_with_data['tenant_id'],
            message="What is my current billing overview?",
            client=MockAIClient()
        )

        assert 'conversation_id' in result
        assert result['response'] is not None
        assert len(result['tool_calls']) >= 1
        assert result['tool_calls'][0]['tool_name'] == 'get_billing_overview'
        assert result['usage']['total_tokens'] > 0

        # Verify DB persistence
        conv = AIConversation.query.filter_by(id=result['conversation_id']).first()
        assert conv is not None
        assert len(conv.messages) >= 2  # user + assistant (+ tool)

        usage = AIUsage.query.filter_by(conversation_id=conv.id).first()
        assert usage is not None
        assert usage.total_tokens > 0


# ===================================================================
# 7. AI API Endpoints Tests
# ===================================================================

def test_chat_api_unauthorized(client):
    """Chat endpoint requires JWT authentication."""
    res = client.post('/api/ai/chat', json={'message': 'hello'})
    assert res.status_code == 401


def test_chat_api_requires_tenant_header(client, auth_headers):
    """Chat endpoint requires X-Tenant-ID header."""
    res = client.post('/api/ai/chat', headers=auth_headers, json={'message': 'hello'})
    assert res.status_code == 400
    assert 'X-Tenant-ID' in res.get_json()['error']


def test_chat_api_validation(client, auth_headers, registered_user):
    """Chat endpoint rejects empty message."""
    membership = TenantMembership.query.filter_by(user_id=registered_user.id).first()
    headers = {**auth_headers, 'X-Tenant-ID': str(membership.tenant_id)}

    res = client.post('/api/ai/chat', headers=headers, json={})
    assert res.status_code == 400

    res = client.post('/api/ai/chat', headers=headers, json={'message': '   '})
    assert res.status_code == 400


def test_chat_api_success(client, auth_headers, registered_user, workspace_with_data):
    """Chat endpoint responds with structured response and tool usage."""
    headers = {**auth_headers, 'X-Tenant-ID': str(workspace_with_data['tenant_id'])}

    res = client.post(
        '/api/ai/chat',
        headers=headers,
        json={'message': 'Can you show me my billing overview and plan?'}
    )
    assert res.status_code == 200
    data = res.get_json()
    assert 'conversation_id' in data
    assert 'response' in data
    assert 'tool_calls' in data
    assert 'usage' in data


def test_list_and_get_conversations(client, auth_headers, workspace_with_data):
    """Conversations and messages can be listed and retrieved."""
    headers = {**auth_headers, 'X-Tenant-ID': str(workspace_with_data['tenant_id'])}

    # Send a message to create a conversation
    chat_res = client.post(
        '/api/ai/chat',
        headers=headers,
        json={'message': 'What is my plan?'}
    )
    conv_id = chat_res.get_json()['conversation_id']

    # List conversations
    list_res = client.get('/api/ai/conversations', headers=headers)
    assert list_res.status_code == 200
    assert list_res.get_json()['count'] >= 1

    # Get conversation details
    get_res = client.get(f'/api/ai/conversations/{conv_id}', headers=headers)
    assert get_res.status_code == 200
    conv_data = get_res.get_json()['conversation']
    assert conv_data['id'] == conv_id
    assert len(conv_data['messages']) >= 2


def test_ai_usage_endpoint(client, auth_headers, workspace_with_data):
    """Usage endpoint returns token and cost accounting."""
    headers = {**auth_headers, 'X-Tenant-ID': str(workspace_with_data['tenant_id'])}

    # Generate a chat turn
    client.post(
        '/api/ai/chat',
        headers=headers,
        json={'message': 'What is my plan?'}
    )

    res = client.get('/api/ai/usage', headers=headers)
    assert res.status_code == 200
    data = res.get_json()
    assert data['total_tokens'] > 0
    assert 'total_cost_usd' in data
    assert data['recent_queries_count'] >= 1


def test_seed_knowledge_permissions(client, auth_headers, registered_user):
    """Seeding knowledge base requires operator or admin platform role."""
    membership = TenantMembership.query.filter_by(user_id=registered_user.id).first()
    headers = {**auth_headers, 'X-Tenant-ID': str(membership.tenant_id)}

    # Normal user denied
    res = client.post('/api/ai/knowledge/seed', headers=headers)
    assert res.status_code == 403

    # Operator accepted
    registered_user.platform_role = 'operator'
    from app.extensions import db
    db.session.commit()

    res = client.post('/api/ai/knowledge/seed', headers=headers)
    assert res.status_code == 200
    assert 'Successfully seeded' in res.get_json()['message']



def test_tool_call_history_round_trip(app, db, workspace_with_data):
    """Assistant tool calls survive persistence and history reconstruction."""
    with app.app_context():
        conv = get_or_create_conversation(
            user_id=workspace_with_data['user_id'],
            tenant_id=workspace_with_data['tenant_id'],
            title="Tool call history test",
        )

        tool_calls = [
            {
                "id": "call_test_1",
                "name": "get_billing_overview",
                "arguments": {},
            },
            {
                "id": "call_test_2",
                "name": "get_invoice_summary",
                "arguments": {"limit": 5},
            },
        ]

        add_message(
            conversation_id=conv.id,
            role="user",
            content="Show my billing overview and invoices.",
        )

        add_message(
            conversation_id=conv.id,
            role="assistant",
            content=None,
            tool_calls=tool_calls,
        )

        add_message(
            conversation_id=conv.id,
            role="tool",
            content='{"total_paid_usd":"20.00"}',
            tool_call_id="call_test_1",
            tool_name="get_billing_overview",
        )

        add_message(
            conversation_id=conv.id,
            role="tool",
            content='{"invoices":[]}',
            tool_call_id="call_test_2",
            tool_name="get_invoice_summary",
        )

        history = get_conversation_history(conv.id, limit=10)

        assistant_message = next(
            message for message in history
            if message["role"] == "assistant"
        )

        assert len(assistant_message["tool_calls"]) == 2

        first_call, second_call = assistant_message["tool_calls"]

        assert first_call["id"] == "call_test_1"
        assert first_call["type"] == "function"
        assert first_call["function"]["name"] == "get_billing_overview"
        assert first_call["function"]["arguments"] == "{}"

        assert second_call["id"] == "call_test_2"
        assert second_call["function"]["name"] == "get_invoice_summary"
        assert second_call["function"]["arguments"] == '{"limit":5}'

        tool_messages = [
            message for message in history
            if message["role"] == "tool"
        ]

        assert [m["tool_call_id"] for m in tool_messages] == [
            "call_test_1",
            "call_test_2",
        ]


def test_assistant_sends_valid_multiple_tool_call_sequence(
    app, db, workspace_with_data
):
    """The next model request contains the assistant declaration and matching tool results."""
    from copy import deepcopy

    class CapturingAIClient:
        def __init__(self):
            self.requests = []
            self.responses = [
                {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_live_1",
                            "name": "get_billing_overview",
                            "arguments": {},
                        },
                        {
                            "id": "call_live_2",
                            "name": "get_invoice_summary",
                            "arguments": {"limit": 5},
                        },
                    ],
                    "prompt_tokens": 100,
                    "completion_tokens": 30,
                },
                {
                    "content": "Here is your billing overview and invoice summary.",
                    "tool_calls": [],
                    "prompt_tokens": 150,
                    "completion_tokens": 40,
                },
            ]

        def chat_completion(self, messages, tools=None, **kwargs):
            self.requests.append(deepcopy(messages))
            return self.responses[len(self.requests) - 1]

    with app.app_context():
        ai_client = CapturingAIClient()

        result = process_chat_message(
            user_id=workspace_with_data["user_id"],
            tenant_id=workspace_with_data["tenant_id"],
            message="Show my billing overview and invoices.",
            client=ai_client,
        )

        assert result["response"] == (
            "Here is your billing overview and invoice summary."
        )
        assert len(ai_client.requests) == 2

        second_request = ai_client.requests[1]

        assistant_message = next(
            message for message in second_request
            if message["role"] == "assistant"
        )

        assert len(assistant_message["tool_calls"]) == 2

        declared_ids = [
            call["id"] for call in assistant_message["tool_calls"]
        ]

        tool_messages = [
            message for message in second_request
            if message["role"] == "tool"
        ]

        returned_ids = [
            message["tool_call_id"] for message in tool_messages
        ]

        assert declared_ids == ["call_live_1", "call_live_2"]
        assert returned_ids == declared_ids

        for call in assistant_message["tool_calls"]:
            assert call["type"] == "function"
            assert isinstance(call["function"]["arguments"], str)

        assert all(message.get("content") for message in tool_messages)


def test_rag_rejects_mismatched_document_and_chunk_tenants(
    app, db, registered_user
):
    """Retrieval rejects chunks whose tenant differs from their parent document."""
    from app.models import Tenant, KnowledgeDocument, DocumentChunk

    with app.app_context():
        tenant_a = Tenant.query.filter_by(
            id=TenantMembership.query.filter_by(
                user_id=registered_user.id
            ).first().tenant_id
        ).first()

        tenant_b = Tenant(
            name="RAG Isolation Tenant B",
            slug="rag-isolation-tenant-b",
        )
        db.session.add(tenant_b)
        db.session.flush()

        # Tenant B document with a Tenant A chunk: must not be visible to A.
        doc_b = KnowledgeDocument(
            tenant_id=tenant_b.id,
            title="Tenant B confidential document",
            slug="tenant-b-confidential",
            category="tenant_doc",
            version=1,
            is_active=True,
        )
        db.session.add(doc_b)
        db.session.flush()

        chunk_a = DocumentChunk(
            document_id=doc_b.id,
            tenant_id=tenant_a.id,
            chunk_index=0,
            content="MISMATCHED_SECRET_TENANT_A_CHUNK",
            embedding=generate_embedding("MISMATCHED_SECRET_TENANT_A_CHUNK"),
            token_count=5,
        )
        db.session.add(chunk_a)

        # Tenant A document with a Tenant B chunk: must not be visible to A.
        doc_a = KnowledgeDocument(
            tenant_id=tenant_a.id,
            title="Tenant A document",
            slug="tenant-a-document",
            category="tenant_doc",
            version=1,
            is_active=True,
        )
        db.session.add(doc_a)
        db.session.flush()

        chunk_b = DocumentChunk(
            document_id=doc_a.id,
            tenant_id=tenant_b.id,
            chunk_index=0,
            content="MISMATCHED_SECRET_TENANT_B_CHUNK",
            embedding=generate_embedding("MISMATCHED_SECRET_TENANT_B_CHUNK"),
            token_count=5,
        )
        db.session.add(chunk_b)
        db.session.commit()

        results = search_similar_chunks(
            query="MISMATCHED_SECRET_TENANT",
            tenant_id=tenant_a.id,
            top_k=10,
        )

        contents = [result["content"] for result in results]

        assert "MISMATCHED_SECRET_TENANT_A_CHUNK" not in contents
        assert "MISMATCHED_SECRET_TENANT_B_CHUNK" not in contents


def test_gemini_thought_signature_survives_conversation_round_trip(
    app, db, workspace_with_data
):
    """Gemini function-call content and thought signatures survive persistence."""
    from google.genai import types
    from app.services.ai.client import GeminiClient

    with app.app_context():
        conv = get_or_create_conversation(
            user_id=workspace_with_data["user_id"],
            tenant_id=workspace_with_data["tenant_id"],
            title="Gemini signature persistence test",
        )

        original_content = types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        name="get_billing_overview",
                        args={},
                    ),
                    thought_signature=b"test-thought-signature",
                )
            ],
        )

        provider_content = original_content.model_dump(
            mode="json",
            exclude_none=True,
        )

        add_message(
            conversation_id=conv.id,
            role="user",
            content="Show my billing overview.",
        )

        add_message(
            conversation_id=conv.id,
            role="assistant",
            content=None,
            tool_calls=[
                {
                    "id": "gemini_call_1",
                    "name": "get_billing_overview",
                    "arguments": {},
                    "provider_content": provider_content,
                }
            ],
        )

        add_message(
            conversation_id=conv.id,
            role="tool",
            content='{"customer_count":1}',
            tool_call_id="gemini_call_1",
            tool_name="get_billing_overview",
        )

        history = get_conversation_history(conv.id, limit=10)

        assistant_message = next(
            message for message in history
            if message["role"] == "assistant"
        )

        assert assistant_message["tool_calls"][0]["provider_content"] == provider_content

        system_instruction, contents = GeminiClient._convert_messages(
            history, types
        )

        assert contents[1].role == "model"
        restored_part = contents[1].parts[0]

        assert restored_part.function_call.name == "get_billing_overview"
        assert restored_part.thought_signature == b"test-thought-signature"

        assert contents[2].role == "user"
        assert contents[2].parts[0].function_response.name == "get_billing_overview"
        assert contents[2].parts[0].function_response.response == {
            "result": {"customer_count": 1}
        }

def test_calculate_token_cost_gemini_paid_tier(monkeypatch):
    monkeypatch.setenv('GEMINI_BILLING_TIER', 'paid')

    cost = calculate_token_cost(
        model='gemini-3.8-flash',
        prompt_tokens=1_000_000,
        completion_tokens=1_000_000,
    )

    assert cost == Decimal('4.500000')


def test_calculate_token_cost_rejects_invalid_gemini_tier(monkeypatch):
    monkeypatch.setenv('GEMINI_BILLING_TIER', 'unknown')

    with pytest.raises(ValueError, match='GEMINI_BILLING_TIER'):
        calculate_token_cost(
            model='gemini-3.8-flash',
            prompt_tokens=100,
            completion_tokens=100,
        )
