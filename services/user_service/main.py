"""User Service – user profile and address management."""
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import Column, Integer, String, create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./user.db")
engine = create_engine(
    DATABASE_URL, pool_pre_ping=True,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False)
Base = declarative_base()


class Profile(Base):
    __tablename__ = "user_profiles"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, unique=True, nullable=False, index=True)  # id owned by Auth Service
    full_name = Column(String(100), default="")
    phone = Column(String(20), default="")


class Address(Base):
    __tablename__ = "addresses"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    line1 = Column(String(255), nullable=False)
    city = Column(String(100), nullable=False)
    state = Column(String(100), nullable=False)
    pincode = Column(String(10), nullable=False)


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


app = FastAPI(title="ShopEase – User Service", lifespan=lifespan)


class ProfileIn(BaseModel):
    full_name: str = Field(max_length=100)
    phone: str = Field(default="", max_length=20)


class AddressIn(BaseModel):
    line1: str = Field(min_length=1, max_length=255)
    city: str = Field(min_length=1, max_length=100)
    state: str = Field(min_length=1, max_length=100)
    pincode: str = Field(min_length=3, max_length=10)


def profile_out(p: Profile):
    return {"user_id": p.user_id, "full_name": p.full_name, "phone": p.phone}


def address_out(a: Address):
    return {"id": a.id, "line1": a.line1, "city": a.city, "state": a.state, "pincode": a.pincode}


def get_profile(db: Session, user_id: int) -> Profile:
    profile = db.query(Profile).filter_by(user_id=user_id).first()
    if not profile:
        profile = Profile(user_id=user_id)
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


def own_address(db: Session, user_id: int, address_id: int) -> Address:
    addr = db.query(Address).filter_by(id=address_id, user_id=user_id).first()
    if not addr:
        raise HTTPException(404, "Address not found")
    return addr


@app.get("/health")
def health():
    return {"service": "user", "status": "ok"}


@app.get("/users/me")
def read_profile(x_user_id: int = Header(), db: Session = Depends(get_db)):
    return profile_out(get_profile(db, x_user_id))


@app.put("/users/me")
def update_profile(body: ProfileIn, x_user_id: int = Header(), db: Session = Depends(get_db)):
    profile = get_profile(db, x_user_id)
    profile.full_name, profile.phone = body.full_name, body.phone
    db.commit()
    return profile_out(profile)


@app.post("/users/me/addresses", status_code=201)
def add_address(body: AddressIn, x_user_id: int = Header(), db: Session = Depends(get_db)):
    addr = Address(user_id=x_user_id, **body.model_dump())
    db.add(addr)
    db.commit()
    db.refresh(addr)
    return address_out(addr)


@app.get("/users/me/addresses")
def list_addresses(x_user_id: int = Header(), db: Session = Depends(get_db)):
    return [address_out(a) for a in db.query(Address).filter_by(user_id=x_user_id).all()]


@app.put("/users/me/addresses/{address_id}")
def update_address(address_id: int, body: AddressIn, x_user_id: int = Header(), db: Session = Depends(get_db)):
    addr = own_address(db, x_user_id, address_id)
    for key, value in body.model_dump().items():
        setattr(addr, key, value)
    db.commit()
    return address_out(addr)


@app.delete("/users/me/addresses/{address_id}", status_code=204)
def delete_address(address_id: int, x_user_id: int = Header(), db: Session = Depends(get_db)):
    db.delete(own_address(db, x_user_id, address_id))
    db.commit()
    return Response(status_code=204)
