"""Authentication Service – registration, login and JWT issuing."""
import datetime as dt
import hashlib
import os
import secrets
from contextlib import asynccontextmanager

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Column, Integer, String, create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./auth.db")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
TOKEN_MINUTES = int(os.getenv("TOKEN_MINUTES", "60"))
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@shopease.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

engine = create_engine(
    DATABASE_URL, pool_pre_ping=True,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False)
Base = declarative_base()


class AuthUser(Base):
    __tablename__ = "auth_users"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="customer")


def hash_pw(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 100_000).hex()
    return f"{salt}${digest}"


def verify_pw(password: str, stored: str) -> bool:
    salt, digest = stored.split("$")
    return secrets.compare_digest(hash_pw(password, salt).split("$")[1], digest)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    with SessionLocal() as db:  # seed one admin so products can be created
        if not db.query(AuthUser).filter_by(email=ADMIN_EMAIL).first():
            db.add(AuthUser(email=ADMIN_EMAIL, password_hash=hash_pw(ADMIN_PASSWORD), role="admin"))
            db.commit()
    yield


app = FastAPI(title="ShopEase – Authentication Service", lifespan=lifespan)


class Credentials(BaseModel):
    email: str = Field(min_length=5, max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=6, max_length=100)


@app.get("/health")
def health():
    return {"service": "auth", "status": "ok"}


@app.post("/auth/register", status_code=201)
def register(body: Credentials, db: Session = Depends(get_db)):
    email = body.email.lower()
    if db.query(AuthUser).filter_by(email=email).first():
        raise HTTPException(409, "Email already registered")
    user = AuthUser(email=email, password_hash=hash_pw(body.password), role="customer")
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"id": user.id, "email": user.email, "role": user.role}


@app.post("/auth/login")
def login(body: Credentials, db: Session = Depends(get_db)):
    user = db.query(AuthUser).filter_by(email=body.email.lower()).first()
    if not user or not verify_pw(body.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")
    expires = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=TOKEN_MINUTES)
    token = jwt.encode({"sub": str(user.id), "role": user.role, "exp": expires}, SECRET_KEY, algorithm="HS256")
    return {"access_token": token, "token_type": "bearer", "expires_in": TOKEN_MINUTES * 60}


@app.get("/auth/me")
def me(x_user_id: int = Header(), db: Session = Depends(get_db)):
    user = db.get(AuthUser, x_user_id)
    if not user:
        raise HTTPException(404, "User not found")
    return {"id": user.id, "email": user.email, "role": user.role}
