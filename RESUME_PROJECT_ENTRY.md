# Resume project entry

## AbyFlow — AI-Powered Billing Intelligence Platform

**AI + Backend Engineering | Python, Flask, PostgreSQL, OpenAI API, pgvector, Redis, Celery, Stripe, Docker, AWS**

Built a multi-tenant SaaS billing platform that combines an LLM-powered billing assistant with a secure backend for customer, invoice, subscription, and payment workflows.

- Integrated an LLM assistant with structured function calling and persistent conversation history to answer workspace billing questions using read-only, tenant-scoped tools.
- Implemented a retrieval-augmented generation (RAG) pipeline with document ingestion, text chunking, embeddings, pgvector similarity search, and workspace-aware retrieval.
- Developed Flask REST APIs with JWT authentication, role-based authorization, PostgreSQL/SQLAlchemy data models, and tenant isolation.
- Implemented Stripe checkout and signed webhook processing, payment idempotency, and a transactional outbox with Celery workers and Redis.
- Added automated unit and integration tests, including PostgreSQL concurrency and RAG isolation tests; containerized the application with Docker and deployed the backend on AWS EC2.

**How to position it:** Lead with “AI + Backend Engineering” rather than describing it only as a billing API. Be prepared to explain the RAG flow, tool-call lifecycle, tenant isolation, idempotency, and outbox processing in technical interviews.
