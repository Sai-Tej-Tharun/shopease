"""Payment Service – payment records, transaction history and payment status.

Payments are simulated: they succeed unless the method is "fail_test"
(handy for demonstrating the failure path of the order flow).
"""
import datetime as dt
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, Float, Integer, String, create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./payment.db")
INTERNAL_KEY = os.getenv("INTERNAL_KEY", "internal-dev-key")

engine = create_engine(
    DATABASE_URL, pool_pre_ping=True,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False)
Base = declarative_base()


def now():
    return dt.datetime.now(dt.timezone.utc)


class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, nullable=False, index=True)  # id owned by Order Service
    user_id = Column(Integer, nullable=False, index=True)   # id owned by Auth Service
    amount = Column(Float, nullable=False)
    method = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False)             # SUCCESS / FAILED
    reference = Column(String(40), nullable=False)          # transaction reference
    created_at = Column(DateTime(timezone=True), default=now)


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


app = FastAPI(title="ShopEase – Payment Service", lifespan=lifespan)


class PaymentIn(BaseModel):
    order_id: int
    user_id: int
    amount: float = Field(gt=0)
    method: str = Field(default="card", max_length=20)


def payment_out(p: Payment):
    return {"id": p.id, "order_id": p.order_id, "user_id": p.user_id, "amount": p.amount,
            "method": p.method, "status": p.status, "reference": p.reference,
            "created_at": p.created_at.isoformat() if p.created_at else None}


@app.get("/health")
def health():
    return {"service": "payment", "status": "ok"}


@app.post("/payments", status_code=201)
def create_payment(body: PaymentIn, db: Session = Depends(get_db), x_internal_key: str | None = Header(None)):
    """Called by Order Service only."""
    if x_internal_key != INTERNAL_KEY:
        raise HTTPException(403, "Internal endpoint")
    status = "FAILED" if body.method == "fail_test" else "SUCCESS"
    payment = Payment(order_id=body.order_id, user_id=body.user_id, amount=body.amount,
                      method=body.method, status=status, reference=uuid.uuid4().hex[:16])
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return payment_out(payment)


# NOTE: /history must be declared before /{payment_id}
@app.get("/payments/history")
def history(x_user_id: int = Header(), db: Session = Depends(get_db)):
    rows = db.query(Payment).filter_by(user_id=x_user_id).order_by(Payment.id.desc()).all()
    return [payment_out(p) for p in rows]


@app.get("/payments/order/{order_id}")
def payments_for_order(order_id: int, x_user_id: int = Header(), x_user_role: str = Header("customer"),
                       db: Session = Depends(get_db)):
    rows = db.query(Payment).filter_by(order_id=order_id).all()
    if x_user_role != "admin":
        rows = [p for p in rows if p.user_id == x_user_id]
    if not rows:
        raise HTTPException(404, "No payment found for this order")
    return [payment_out(p) for p in rows]


@app.get("/payments/{payment_id}")
def read_payment(payment_id: int, x_user_id: int = Header(), x_user_role: str = Header("customer"),
                 db: Session = Depends(get_db)):
    payment = db.get(Payment, payment_id)
    if not payment or (payment.user_id != x_user_id and x_user_role != "admin"):
        raise HTTPException(404, "Payment not found")
    return payment_out(payment)
