"""Order Service – order creation, order history and order status.

Order creation talks to two other services over REST:
  Product Service  -> validate availability, reserve / restore stock
  Payment Service  -> process the payment
"""
import datetime as dt
import logging
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, create_engine
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./order.db")
PRODUCT_URL = os.getenv("PRODUCT_SERVICE_URL", "http://localhost:8003")
PAYMENT_URL = os.getenv("PAYMENT_SERVICE_URL", "http://localhost:8005")
INTERNAL_KEY = os.getenv("INTERNAL_KEY", "internal-dev-key")
TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "5"))

log = logging.getLogger("order-service")
engine = create_engine(
    DATABASE_URL, pool_pre_ping=True,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False)
Base = declarative_base()


def now():
    return dt.datetime.now(dt.timezone.utc)


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    total_amount = Column(Float, nullable=False)
    status = Column(String(20), nullable=False, default="PENDING")
    payment_method = Column(String(20), default="card")
    created_at = Column(DateTime(timezone=True), default=now)
    items = relationship("OrderItem", cascade="all, delete-orphan")


class OrderItem(Base):
    __tablename__ = "order_items"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    product_id = Column(Integer, nullable=False)  # id owned by Product Service (no cross-db FK)
    product_name = Column(String(150), nullable=False)
    quantity = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)         # unit price at the time of the order


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="ShopEase – Order Service", lifespan=lifespan)


class ItemIn(BaseModel):
    product_id: int
    quantity: int = Field(gt=0, le=100)


class OrderIn(BaseModel):
    items: list[ItemIn] = Field(min_length=1)
    payment_method: str = Field(default="card", max_length=20)


class StatusIn(BaseModel):
    status: str


def order_out(o: Order):
    return {"id": o.id, "user_id": o.user_id, "status": o.status, "total_amount": o.total_amount,
            "payment_method": o.payment_method,
            "created_at": o.created_at.isoformat() if o.created_at else None,
            "items": [{"product_id": i.product_id, "product_name": i.product_name,
                       "quantity": i.quantity, "price": i.price} for i in o.items]}


# ------------------------------------------------------------------ service calls
def call(service: str, method: str, url: str, **kwargs) -> httpx.Response:
    """REST call with a timeout; any connection problem becomes 503."""
    try:
        return httpx.request(method, url, timeout=TIMEOUT, **kwargs)
    except httpx.HTTPError as exc:
        log.warning("%s service unreachable: %s", service, exc)
        raise HTTPException(503, f"{service} service unavailable")


def change_stock(product_id: int, delta: int) -> httpx.Response:
    return call("Product", "PATCH", f"{PRODUCT_URL}/products/{product_id}/stock",
                json={"delta": delta}, headers={"X-Internal-Key": INTERNAL_KEY})


def restore_stock(lines: list[dict]):
    """Compensating action: put reserved stock back (best effort)."""
    for line in lines:
        try:
            change_stock(line["product_id"], line["quantity"])
        except HTTPException:
            log.error("Could not restore stock for product %s", line["product_id"])


# ------------------------------------------------------------------ endpoints
@app.get("/health")
def health():
    return {"service": "order", "status": "ok"}


@app.post("/orders", status_code=201)
def create_order(body: OrderIn, x_user_id: int = Header(), db: Session = Depends(get_db)):
    # 1. Validate every item with the Product Service (price comes from there, not the client)
    lines = []
    for item in body.items:
        resp = call("Product", "GET", f"{PRODUCT_URL}/products/{item.product_id}")
        if resp.status_code == 404:
            raise HTTPException(404, f"Product {item.product_id} not found")
        if resp.status_code != 200:
            raise HTTPException(503, "Product service error")
        product = resp.json()
        if product["stock"] < item.quantity:
            raise HTTPException(409, f"Insufficient stock for '{product['name']}'")
        lines.append({"product_id": product["id"], "product_name": product["name"],
                      "quantity": item.quantity, "price": product["price"]})

    # 2. Reserve stock (undo what was reserved if any step fails)
    reserved = []
    try:
        for line in lines:
            resp = change_stock(line["product_id"], -line["quantity"])
            if resp.status_code == 409:
                raise HTTPException(409, f"Insufficient stock for '{line['product_name']}'")
            if resp.status_code != 200:
                raise HTTPException(503, "Product service error")
            reserved.append(line)
    except HTTPException:
        restore_stock(reserved)
        raise

    # 3. Save the order as PENDING
    total = round(sum(l["price"] * l["quantity"] for l in lines), 2)
    order = Order(user_id=x_user_id, total_amount=total, status="PENDING", payment_method=body.payment_method,
                  items=[OrderItem(**l) for l in lines])
    db.add(order)
    db.commit()
    db.refresh(order)

    # 4. Ask the Payment Service to charge
    try:
        resp = call("Payment", "POST", f"{PAYMENT_URL}/payments", headers={"X-Internal-Key": INTERNAL_KEY},
                    json={"order_id": order.id, "user_id": x_user_id, "amount": total,
                          "method": body.payment_method})
    except HTTPException:
        order.status = "FAILED"
        db.commit()
        restore_stock(lines)
        raise HTTPException(503, f"Payment service unavailable – order {order.id} cancelled, stock restored")

    if resp.status_code != 201 or resp.json().get("status") != "SUCCESS":
        order.status = "FAILED"
        db.commit()
        restore_stock(lines)
        raise HTTPException(402, f"Payment failed – order {order.id} cancelled, stock restored")

    # 5. Success
    order.status = "CONFIRMED"
    db.commit()
    return order_out(order)


@app.get("/orders")
def order_history(x_user_id: int = Header(), db: Session = Depends(get_db)):
    rows = db.query(Order).filter_by(user_id=x_user_id).order_by(Order.id.desc()).all()
    return [order_out(o) for o in rows]


@app.get("/orders/{order_id}")
def read_order(order_id: int, x_user_id: int = Header(), x_user_role: str = Header("customer"),
               db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if not order or (order.user_id != x_user_id and x_user_role != "admin"):
        raise HTTPException(404, "Order not found")
    return order_out(order)


# status changes allowed from each state
TRANSITIONS = {"CONFIRMED": {"SHIPPED", "CANCELLED"}, "SHIPPED": {"DELIVERED"}}


@app.patch("/orders/{order_id}/status")
def update_status(order_id: int, body: StatusIn, x_user_id: int = Header(), x_user_role: str = Header("customer"),
                  db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if not order or (order.user_id != x_user_id and x_user_role != "admin"):
        raise HTTPException(404, "Order not found")
    new = body.status.upper()
    if x_user_role != "admin" and new != "CANCELLED":
        raise HTTPException(403, "Customers can only cancel their own orders")
    if new not in TRANSITIONS.get(order.status, set()):
        raise HTTPException(409, f"Cannot change status from {order.status} to {new}")
    if new == "CANCELLED":
        restore_stock([{"product_id": i.product_id, "quantity": i.quantity} for i in order.items])
    order.status = new
    db.commit()
    return order_out(order)
