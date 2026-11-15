# AbyFlow API

[![Tests](https://img.shields.io/badge/tests-automated-brightgreen.svg)](tests/)
[![Python](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/flask-3.1.3-black.svg)](https://flask.palletsprojects.com/)
[![PostgreSQL](https://img.shields.io/badge/postgresql-15%20%7C%20pgvector-blue.svg)](https://www.postgresql.org/)
[![Celery](https://img.shields.io/badge/celery-5.6.3-green.svg)](https://docs.celeryq.dev/)
[![Docker](https://img.shields.io/badge/docker-compose-blue.svg)](https://www.docker.com/)

**AbyFlow** is a resilient, enterprise-grade SaaS billing, multi-tenant workspace management, and AI intelligence backend built with Flask, SQLAlchemy, Celery, Redis, PostgreSQL, and Stripe.

It features a high-throughput **Transactional Outbox** pattern with deterministic row locking (`SKIP LOCKED`), fencing tokens, and exponential backoff; strict database-level **Multi-Tenant Isolation**; exact **Deterministic Financial Calculations** (integer cents and `Decimal`); and a **Tenant-Aware Retrieval-Augmented Generation (RAG) AI Assistant** with read-only database tool calling.

> **Note on Payment Model:** AbyFlow currently implements **one-time payments** (`mode='payment'`) via Stripe Checkout for lifetime/fixed-tier access upgrades ($20.00 USD / 2000 cents). It is structurally architected to support recurring subscriptions in future versions.

---



## Project Summary

**AbyFlow — AI-Powered Billing Intelligence Platform**

A multi-tenant SaaS billing platform combining production-oriented backend engineering with an LLM-powered billing assistant and retrieval-augmented generation (RAG). The AI assistant uses structured, read-only tools to retrieve workspace billing data and tenant-scoped knowledge, while the backend handles authentication, authorization, invoicing, Stripe billing, asynchronous processing, and auditability.

**AI engineering:** LLM integration, function/tool calling, conversation persistence, token-usage tracking, embeddings, document chunking, pgvector retrieval, and tenant-aware RAG.

**Backend engineering:** Flask REST APIs, PostgreSQL, SQLAlchemy, multi-tenancy, JWT authentication, Stripe webhooks, idempotency, transactional outbox, Celery, Redis, Docker, and AWS EC2 deployment.

## High-Level Architecture

```mermaid
graph TD
    Client[Client / Web Browser / Mobile] -->|HTTP / HTTPS| Nginx[Nginx Reverse Proxy]
    Nginx -->|Proxy :5000| Gunicorn[Gunicorn WSGI Server]
    Gunicorn --> Flask[Flask REST API Application]

    subgraph "Core Storage & Cache"
        Flask -->|ORM Queries & Vector Search| DB[(PostgreSQL 15 + pgvector)]
        Flask -->|Enqueue Outbox Pulse| Redis[(Redis 7 Queue)]
    end

    subgraph "Stripe Payment Lifecycle"
        Flask -->|Create Checkout Session| Stripe[Stripe API]
        Stripe -->|Signed Webhook /api/webhooks/stripe| Flask
        Flask -->|Atomic Write: ProcessedEvent + OutboxEvent| DB
    end

    subgraph "Transactional Outbox Engine"
        Redis -->|Consume Task| Worker[Celery Worker Pool]
        Worker -->|SELECT ... FOR UPDATE SKIP LOCKED| DB
        Worker -->|Idempotent Payment & Tier Upgrade| DB
    end

    subgraph "Platform Operations & Audit"
        OpsAdmin[Platform Operator / Admin] -->|Audit & Replay /api/ops/outbox/*| Flask
        Flask -->|Atomic Replay + Audit Trail| DB
    end

    subgraph "AI Intelligence & RAG"
        User[Tenant User] -->|POST /api/ai/chat| Flask
        Flask -->|Semantic Retrieval + Isolation| DB
        Flask -->|Strict Read-Only Context Tools| DB
        Flask -->|LLM Completion Prompt| OpenAI[OpenAI GPT-4o-mini]
    end
```

---

## Core System Capabilities

### 1. Multi-Tenancy & Workspace Governance
- **Personal Workspace Provisioning:** Atomic creation of personal tenant workspaces upon user registration.
- **Role Hierarchy:**
  - **Platform Roles:** `user`, `operator`, `admin` (stored on user record, provisioned via secure host CLI).
  - **Workspace Roles:** `owner`, `admin`, `member` (stored on tenant memberships).
- **Tenant Isolation:** Explicit workspace boundaries enforced across queries, invoices, customer entities, and vector retrieval.

### 2. Transactional Outbox & Reliable Stripe Billing
- **Cryptographic Signature Verification:** Webhooks verified against raw byte payload with replay attack rejection.
- **Atomic Event Ingestion:** Webhook requests atomically persist `ProcessedEvent`, `BillingEvent`, and `OutboxEvent` before returning HTTP 200.
- **Worker Concurrency & Fencing:** Workers claim outbox events using `SELECT ... FOR UPDATE SKIP LOCKED`, assigning a cryptographically secure UUID lease token. Stale workers are prevented from updating the database.
- **Backoff & Dead-Letter Handling:** Exponential backoff scheduling (`10s`, `20s`, `40s`...) up to 5 attempts before marking as `failed`.
- **Payment Idempotency:** Duplicate checkout events and payment intents are safely deduplicated.

### 3. Operations API & Replay Audit
- Dedicated platform endpoints (`/api/ops/outbox/*`) restricted to users with `operator` or `admin` platform roles.
- Operator replay resets retry state atomically, records operator identity, mandatory audit rationale, and previous attempt history.
- Zero public self-promotion routes. First operator provisioned via `scripts/promote_operator.py`.

### 4. Deterministic Billing & Invoicing
- **Customer Management:** Full CRUD scoped strictly to active tenant.
- **Invoice Sequencing:** Atomic per-tenant sequence numbers formatted as `INV-000001` using row-level locking.
- **Financial Exactness:** All line items, taxes, and totals calculated using integer cents and exact `Decimal` precision. No floating-point inaccuracies.
- **Immutable Issued State:** Issued invoices cannot be edited or deleted.

### 5. AI Engineering & Tenant-Aware RAG Engine
- **Tenant-Aware Retrieval:** RAG retrieval applies workspace-aware filtering so tenant queries are scoped to the active workspace.
- **Dual-Dialect Vector Storage:** `EmbeddingVector` custom SQLAlchemy type seamlessly uses PostgreSQL `pgvector` in production and `JSON` in SQLite test environments.
- **Deterministic Read-Only Billing Tools:** LLM tool calls execute against audited repository query boundaries; LLMs are strictly forbidden from performing financial computations or mutative actions.
- **Token Usage Auditing:** Prompt, completion, and total token usage are persisted per interaction, alongside an estimated USD cost. The current pricing table explicitly covers `gpt-4o-mini` and `gpt-4o`; the fallback rate is a baseline estimate for other model names, not verified provider pricing.

---

## Project Structure

```
AbyFlow/
├── .dockerignore
├── .env.example                     # Environment configuration template
├── .gitignore
├── Dockerfile                       # Hardened, non-root Python 3.12 container
├── docker-compose.yml               # Local development stack (db, redis, web, worker)
├── docker-compose.prod.yml          # Production stack (EC2 deployment ready)
├── requirements.txt                 # Runtime dependencies
├── requirements-dev.txt             # Test coverage tooling
├── wsgi.py                          # Production WSGI application entry point
├── app/
│   ├── __init__.py                  # Application factory with /health check
│   ├── config.py                    # Environment configuration classes
│   ├── extensions.py                # Initialized SQLAlchemy, Bcrypt, JWT extensions
│   ├── extentions.py                # Backward-compatibility alias
│   ├── models.py                    # Complete domain, billing, outbox, and AI models
│   ├── tasks.py                     # Celery outbox poller and processor tasks
│   ├── repositories/
│   │   └── billing_repository.py    # Read-only tenant-scoped billing queries for AI tools
│   ├── routes/
│   │   ├── ai.py                    # /api/ai/chat, /conversations, /usage
│   │   ├── auth.py                  # /api/auth/register, /login, /dashboard
│   │   ├── customers.py             # /api/customers CRUD
│   │   ├── invoices.py              # /api/invoices CRUD and issue
│   │   ├── operations.py            # /api/ops/outbox failed inspection and replay
│   │   ├── subscriptions.py         # /create-checkout, /success, /cancel
│   │   ├── tenants.py               # Workspace details and membership management
│   │   └── webhooks.py              # Cryptographic Stripe webhook receiver
│   ├── schemas/
│   │   └── stripe_schema.py         # Webhook payload schemas
│   └── services/
│       ├── ai/                      # AI assistant, tools, prompts, usage accounting
│       ├── outbox_operations.py     # Outbox inspection and replay logic
│       ├── platform_authorization.py# Platform operator/admin decorators
│       ├── rag/                     # Chunking, embeddings, retrieval, ingestion
│       ├── stripe_service.py        # Stripe checkout session generator
│       ├── tenant_service.py        # Multi-tenant workspace and membership operations
│       └── user_service.py          # Plan tier upgrades
├── docs/
│   └── knowledge_base/              # Seed documentation (FAQ, policies, terms)
├── migrations/                      # Alembic schema migrations
├── scripts/
│   ├── init_db.py                   # Automated database initialization
│   └── promote_operator.py          # Secure host CLI to grant platform roles
└── tests/
    ├── conftest.py                  # Isolated SQLite testing fixtures
    ├── postgres/                    # True PostgreSQL concurrency & locking tests
    ├── test_ai_layer.py             # AI RAG, tools, and usage tests
    ├── test_auth.py                 # Auth and multi-tenant isolation tests
    ├── test_customers.py            # Customer domain tests
    ├── test_invoices.py             # Invoice calculations and sequence tests
    ├── test_operations_api.py       # Platform ops replay & audit tests
    ├── test_outbox.py               # Outbox leasing, backoff, and idempotency tests
    ├── test_stripe.py               # Stripe checkout & webhook tests
    └── test_tenant_management.py    # Workspace membership CRUD tests
```

---

## API Endpoints Reference

### 1. System
| Method | Endpoint | Description | Auth Required |
|---|---|---|---|
| `GET` | `/` | API status and version metadata | No |
| `GET` | `/health` | Live health probe checking DB and Redis | No |

### 2. Authentication
| Method | Endpoint | Description | Auth Required |
|---|---|---|---|
| `POST` | `/api/auth/register` | Register new user + creates personal workspace | No |
| `POST` | `/api/auth/login` | Authenticate and obtain JWT access token | No |
| `GET` | `/api/auth/dashboard` | Access user profile and plan tier | Bearer JWT |

### 3. Workspaces & Multi-Tenancy
| Method | Endpoint | Description | Auth Required |
|---|---|---|---|
| `GET` | `/api/tenants` | List workspaces accessible to the authenticated user | Bearer JWT |
| `POST` | `/api/tenants` | Create new tenant workspace | Bearer JWT |
| `GET` | `/api/tenants/current` | Get current workspace details and role | Bearer JWT + `X-Tenant-ID` |
| `GET` | `/api/tenants/current/members` | List workspace members | Bearer JWT + `X-Tenant-ID` |
| `POST` | `/api/tenants/current/members` | Invite new member (owner/admin only) | Bearer JWT + `X-Tenant-ID` |
| `PATCH` | `/api/tenants/current/members/<id>` | Update member role (owner only) | Bearer JWT + `X-Tenant-ID` |
| `DELETE` | `/api/tenants/current/members/<id>` | Remove member (owner/admin) | Bearer JWT + `X-Tenant-ID` |

### 4. Customers & Invoicing
| Method | Endpoint | Description | Auth Required |
|---|---|---|---|
| `GET` | `/api/customers` | List workspace customers | Bearer JWT + `X-Tenant-ID` |
| `POST` | `/api/customers` | Create customer | Bearer JWT + `X-Tenant-ID` |
| `GET` | `/api/customers/<id>` | Get customer details | Bearer JWT + `X-Tenant-ID` |
| `PATCH` | `/api/customers/<id>` | Update customer | Bearer JWT + `X-Tenant-ID` |
| `DELETE` | `/api/customers/<id>` | Delete customer | Bearer JWT + `X-Tenant-ID` |
| `GET` | `/api/invoices` | List workspace invoices | Bearer JWT + `X-Tenant-ID` |
| `POST` | `/api/invoices` | Create draft invoice with line items | Bearer JWT + `X-Tenant-ID` |
| `GET` | `/api/invoices/<id>` | Get invoice details | Bearer JWT + `X-Tenant-ID` |
| `PATCH` | `/api/invoices/<id>` | Update invoice | Bearer JWT + `X-Tenant-ID` |
| `DELETE` | `/api/invoices/<id>` | Delete invoice | Bearer JWT + `X-Tenant-ID` |
| `POST` | `/api/invoices/<id>/issue` | Issue draft invoice (locks editing) | Bearer JWT + `X-Tenant-ID` |

### 5. Payments & Webhooks
| Method | Endpoint | Description | Auth Required |
|---|---|---|---|
| `POST` | `/api/subscriptions/create-checkout` | Generate Stripe Checkout URL | Bearer JWT |
| `GET` | `/api/subscriptions/success` | Checkout success callback | No |
| `GET` | `/api/subscriptions/cancel` | Checkout cancellation callback | No |
| `POST` | `/api/webhooks/stripe` | Process signed Stripe webhook | `Stripe-Signature` header |

### 6. Platform Operations (Admin/Operator Only)
| Method | Endpoint | Description | Auth Required |
|---|---|---|---|
| `GET` | `/api/ops/outbox/failed` | List failed outbox events | Platform Operator/Admin JWT |
| `GET` | `/api/ops/outbox/<id>` | Inspect failed outbox event details | Platform Operator/Admin JWT |
| `POST` | `/api/ops/outbox/<id>/replay` | Replay failed event and record audit | Platform Operator/Admin JWT |
| `GET` | `/api/ops/outbox/replay-audit` | List replay audit history | Platform Operator/Admin JWT |

### 7. AI Assistant & Tenant-Aware RAG
| Method | Endpoint | Description | Auth Required |
|---|---|---|---|
| `POST` | `/api/ai/chat` | Chat with AI (RAG context + read-only tools) | Bearer JWT + `X-Tenant-ID` |
| `GET` | `/api/ai/conversations` | List user conversations in workspace | Bearer JWT + `X-Tenant-ID` |
| `GET` | `/api/ai/conversations/<id>` | Fetch conversation message history | Bearer JWT + `X-Tenant-ID` |
| `GET` | `/api/ai/usage` | Inspect token usage and costs | Bearer JWT + `X-Tenant-ID` |
| `POST` | `/api/ai/knowledge/seed` | Seed platform knowledge documentation | Platform Operator/Admin JWT |

---

## Quickstart & Local Development

### 1. Configure Environment
```bash
cp .env.example .env
```
Ensure your `.env` contains valid credentials or development defaults:
```env
FLASK_ENV=development
DATABASE_URL=postgresql://abyflow_user:abyflow_password@db:5432/abyflow_db
REDIS_URL=redis://redis:6379/0
JWT_SECRET_KEY=dev_jwt_secret_key_change_me
STRIPE_SECRET_KEY=sk_test_your_key
STRIPE_WEBHOOK_SECRET=whsec_your_key
# For local development without OpenAI credentials:
OPENAI_API_KEY=
AI_ALLOW_MOCK_EMBEDDINGS=true
```

### 2. Start Application Stack
```bash
docker compose up -d --build
```

### 3. Apply Database Migrations
Apply the tracked Alembic migrations before using the API:
```bash
docker compose exec web flask db upgrade
```
The same operation can be run with `python scripts/init_db.py` from a host
environment configured to reach the target database. Do not use `db.create_all()`
as a substitute for the migration history.
Containers started:
- `abyflow-db-1`: PostgreSQL 15 with pgvector extension support.
- `abyflow-redis-1`: Redis 7 broker.
- `abyflow-web-1`: Flask REST API on port `5000`.
- `abyflow-worker-1`: Celery background outbox processor.

### 4. Provisioning a Platform Operator
Workspace owners cannot manage platform infrastructure. Provision an operator using the secure host CLI:
```bash
python scripts/promote_operator.py admin@example.com --role operator
```

---

## Automated Test Suites

AbyFlow provides comprehensive automated testing:
- **Unit & Integration Suite:** In-memory SQLite with mocked external APIs for deterministic testing.
- **PostgreSQL Integration Suite:** PostgreSQL row-locking, concurrency, pgvector retrieval, and tenant-isolation validation against a PostgreSQL 15 test database.

### Install development tools:
```bash
pip install -r requirements-dev.txt
```

### Run the complete suite:
Set `ABYFLOW_TEST_DATABASE_URL` to a dedicated PostgreSQL test database
named `abyflow_concurrency_test`, then run:
```bash
pytest -v
```

For the SQLite-only suite:
```bash
pytest -v --ignore=tests/postgres
```

### Generate coverage:
```bash
pytest --cov=app --cov-report=term-missing --cov-report=html
```
Open `htmlcov/index.html` to inspect uncovered lines.

---

## Production Deployment (AWS EC2)

AbyFlow is optimized for zero-cost or low-cost deployment on AWS EC2 (`t3.micro` or `t4g.small`):
1. **Launch Ubuntu 24.04 LTS EC2 instance** with Security Group allowing 22 (SSH), 80 (HTTP), 443 (HTTPS).
2. Install Docker, Docker Compose, and Nginx.
3. Configure `.env` with production credentials and run:
   ```bash
   docker compose -f docker-compose.prod.yml up -d --build
   ```
4. Apply migrations once, from a single web container:
   ```bash
   docker compose -f docker-compose.prod.yml exec web flask db upgrade
   ```
5. Configure the host Nginx reverse proxy to forward HTTPS traffic to `127.0.0.1:5000`, and provision TLS with Certbot. The Nginx configuration is managed on the host and is not included in this repository.
