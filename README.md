# ShopEase – Microservices E-Commerce (simple version)

Six small FastAPI services behind one API Gateway. Every service owns its own database and talks to the others only through REST APIs.

```
Client ──> API Gateway :8000 ──> Auth :8001     (auth_db)
                             ├─> User :8002     (user_db)
                             ├─> Product :8003  (product_db)
                             ├─> Order :8004    (order_db) ──REST──> Product, Payment
                             └─> Payment :8005  (payment_db)
```

**Stack:** Python, FastAPI, SQLAlchemy, Pydantic, JWT, MySQL (Docker) / SQLite (local), Docker Compose.

## Run it

### Option A – Docker (MySQL, one database per service)
```bash
docker compose up --build
```
Gateway Swagger UI: http://localhost:8000/docs  (each service also has its own `/docs` on ports 8001–8005)

### Option B – Local, no Docker (SQLite)
```bash
pip install fastapi uvicorn sqlalchemy httpx pyjwt
python run_local.py
```

### Test the whole flow
```bash
python test_flow.py        # 25 checks through the gateway
```

**Admin account** (seeded automatically): `admin@shopease.com` / `admin123`
Customers register themselves with `POST /auth/register`.

## Endpoints (all through the gateway, http://localhost:8000)

| Service | Endpoint | Who |
|---|---|---|
| Auth | `POST /auth/register`, `POST /auth/login`, `GET /auth/me` | public / logged in |
| User | `GET, PUT /users/me` · `POST, GET /users/me/addresses` · `PUT, DELETE /users/me/addresses/{id}` | logged in |
| Product | `GET /products`, `GET /products/{id}`, `GET /categories` | public |
| Product | `POST /categories`, `POST, PUT, DELETE /products…` | admin |
| Order | `POST /orders` · `GET /orders` · `GET /orders/{id}` · `PATCH /orders/{id}/status` | logged in (admin can ship/deliver) |
| Payment | `GET /payments/history` · `GET /payments/order/{order_id}` · `GET /payments/{id}` | logged in |

Internal only (needs the `X-Internal-Key` header, used by Order Service): `PATCH /products/{id}/stock`, `POST /payments`.

## How an order works
1. Client sends `POST /orders` with a JWT. The gateway checks the token and adds `X-User-Id` / `X-User-Role` headers.
2. Order Service asks Product Service for each product (price and stock come from there).
3. Order Service reserves stock (`PATCH /products/{id}/stock`) and saves the order as `PENDING`.
4. Order Service calls Payment Service (`POST /payments`).
5. Success → order `CONFIRMED`. Payment failed (`payment_method: "fail_test"` simulates this) → `402`, order `FAILED`, stock restored. Payment or Product service down → `503`, stock restored.

Order statuses: `PENDING → CONFIRMED → SHIPPED → DELIVERED`, or `CANCELLED` (restores stock) / `FAILED`.

## Example requests
```bash
# register + login
curl -X POST localhost:8000/auth/register -H "Content-Type: application/json" -d '{"email":"me@example.com","password":"Secret@123"}'
curl -X POST localhost:8000/auth/login    -H "Content-Type: application/json" -d '{"email":"me@example.com","password":"Secret@123"}'

# place an order (use the access_token from login)
curl -X POST localhost:8000/orders -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
     -d '{"items":[{"product_id":1,"quantity":2}],"payment_method":"upi"}'
```

## Project layout
```
ShopEase/
├── docker-compose.yml        MySQL + 6 services
├── docker/mysql-init.sql     creates the 5 databases
├── run_local.py              start everything without Docker
├── test_flow.py              end-to-end test
└── services/
    ├── api_gateway/   auth_service/   user_service/
    └── product_service/   order_service/   payment_service/
        (each: main.py, requirements.txt, Dockerfile)
```

## Simplifications (good next steps)
- Tables are created on startup; add **Alembic** migrations next.
- Payments are simulated; there is no real payment gateway.
- One MySQL server hosts the five databases (still fully separate databases and no cross-service access).
- Secrets (`SECRET_KEY`, `INTERNAL_KEY`) have dev defaults – set real values in production.
