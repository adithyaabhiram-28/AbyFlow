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

## AbyFlow Setup Guide

These steps are the recommended way to run AbyFlow on a new device using Docker.

### Prerequisites

* Git
* Docker Desktop

### 1. Clone the Repository

```bash
git clone https://github.com/adithyaabhiram-28/AbyFlow.git
cd AbyFlow
```

### 2. Create `.env`

Create a `.env` file in the project root:

```env
DATABASE_URL=postgresql://abyflow_user:abyflow_password@db:5432/abyflow_db
REDIS_URL=redis://redis:6379/0
JWT_SECRET_KEY=your-secret-key
STRIPE_SECRET_KEY=your-stripe-secret-key
STRIPE_PUBLISHABLE_KEY=your-stripe-publishable-key
```

### 3. Build and Start All Containers

```bash
docker compose up --build -d
```

This starts:

* Flask API
* Celery Worker
* PostgreSQL
* Redis

### 4. Create Database Tables

Right now, creating the tables is still a manual one-time step for Docker setup.

Open Python inside the web container:

```bash
docker compose exec web python
```

Run:

```python
from app import create_app
from app.extentions import db

app = create_app()

with app.app_context():
    db.create_all()
```

Exit Python:

```python
exit()
```

Verify the tables:

```bash
docker compose exec db psql -U abyflow_user -d abyflow_db -c "\dt"
```

Expected tables:

* `processed_event`
* `user`

### 5. Verify Containers

```bash
docker ps
```

Expected containers:

* `abyflow-web-1`
* `abyflow-worker-1`
* `abyflow-db-1`
* `abyflow-redis-1`

### 6. API Available

```text
http://localhost:5000
```

Note:

* The project currently imports `app.extentions` with that spelling, so the manual table-creation command above is written to match the codebase exactly.

---

## API Test Flow

### 1. Register

```bash
curl -X POST http://127.0.0.1:5000/api/auth/register -H "Content-Type: application/json" -d "{\"email\":\"test@test.com\",\"password\":\"mypassword123\"}"
```

### 2. Login

```bash
curl -X POST http://127.0.0.1:5000/api/auth/login -H "Content-Type: application/json" -d "{\"email\":\"test@test.com\",\"password\":\"mypassword123\"}"
```

Copy the JWT token from the login response.

### 3. Check Dashboard (Free)

```bash
curl -X GET http://127.0.0.1:5000/api/auth/dashboard -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

Expected response:

```json
{
  "plan_tier": "free"
}
```

### 4. Simulate Successful Payment

```bash
curl -X POST http://127.0.0.1:5000/api/webhooks/stripe -H "Content-Type: application/json" -d "{\"id\":\"evt_success_1\",\"type\":\"checkout.session.completed\",\"data\":{\"object\":{\"metadata\":{\"user_email\":\"test@test.com\"}}}}"
```

### 5. Check Dashboard Again

```bash
curl -X GET http://127.0.0.1:5000/api/auth/dashboard -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

Expected response:

```json
{
  "plan_tier": "pro"
}
```

### 6. Simulate Failed Payment

```bash
curl -X POST http://127.0.0.1:5000/api/webhooks/stripe -H "Content-Type: application/json" -d "{\"id\":\"evt_failed_1\",\"type\":\"invoice.payment_failed\",\"data\":{\"object\":{\"metadata\":{\"user_email\":\"test@test.com\"}}}}"
```

### 7. Check Dashboard Again

```bash
curl -X GET http://127.0.0.1:5000/api/auth/dashboard -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

Expected response:

```json
{
  "plan_tier": "free"
}
```

---

## Daily Usage

### Start AbyFlow

```bash
cd AbyFlow
docker compose up -d
```

### Check Containers

```bash
docker ps
```

### Stop AbyFlow

```bash
docker compose down
```

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
