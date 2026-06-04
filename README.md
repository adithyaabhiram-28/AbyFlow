# AbyFlow API

A scalable, asynchronous SaaS billing backend built with Python, designed to handle high-throughput payment webhooks without blocking the main application server.

## Architecture

This project demonstrates a microservices-oriented architecture using Docker:

* **Web Server (Gunicorn + Flask):** Handles synchronous HTTP requests (Auth, Checkouts).
* **Message Broker (Redis):** Acts as a task queue for asynchronous processing.
* **Background Worker (Celery):** Consumes tasks from Redis to process heavy operations (Database updates) offline.
* **Database (PostgreSQL):** Relational data storage for users and subscriptions.
* **3rd Party:** Stripe (Payments), Marshmallow (Validation), JWT (Auth).

## End-to-End Flow

1. `POST /api/auth/register` -> Creates user in Postgres.
2. `POST /api/subscriptions/create-checkout` -> Generates Stripe Checkout URL.
3. `POST /api/webhooks/stripe` -> Receives Stripe event, instantly returns `200 OK`, pushes task to Redis.
4. *(Background)* Celery worker picks up task, updates `plan_tier` to 'pro' in Postgres.
5. `GET /api/auth/dashboard` -> Returns updated user status.


## Tech Stack

### Backend

* Python
* Flask
* SQLAlchemy
* Marshmallow

### Authentication

* Flask-Bcrypt
* Flask-JWT-Extended

### Database

* PostgreSQL

### Asynchronous Processing

* Redis
* Celery

### Payments

* Stripe API
* Stripe Webhooks

### Deployment

* Docker
* Docker Compose
* Gunicorn

### Testing

* Pytest

---

## Project Structure

```
AbyFlow/
│
├── app/
│   ├── routes/
│   │   ├── auth.py
│   │   ├── subscriptions.py
│   │   └── webhooks.py
│   │
│   ├── services/
│   │   ├── stripe_service.py
│   │   └── user_service.py
│   │
│   ├── models/
│   │   └── user.py
│   │
│   ├── tasks.py
│   └── __init__.py
│
├── tests/
│
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```

---

## Running with Docker

### Prerequisites

* Docker Desktop
* Docker Compose

### Start Services

```
docker-compose up --build
```

Services started:

* Flask API
* PostgreSQL
* Redis
* Celery Worker

Application URL:

http://localhost:5000

---

## API Endpoints

### Authentication

```
POST /api/auth/register
POST /api/auth/login
GET  /api/auth/dashboard
```

### Subscriptions

```
POST /api/subscriptions/create-checkout
```

### Webhooks

```
POST /api/webhooks/stripe
```
