"""Product Service – product catalog, categories and inventory."""
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import Column, Float, ForeignKey, Integer, String, create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./product.db")
INTERNAL_KEY = os.getenv("INTERNAL_KEY", "internal-dev-key")  # shared secret for service-to-service calls

engine = create_engine(
    DATABASE_URL, pool_pre_ping=True,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False)
Base = declarative_base()


class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)


class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True)
    name = Column(String(150), nullable=False)
    description = Column(String(500), default="")
    price = Column(Float, nullable=False)
    stock = Column(Integer, nullable=False, default=0)  # inventory
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_admin(x_user_role: str = Header("customer")):
    if x_user_role != "admin":
        raise HTTPException(403, "Admin access required")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="ShopEase – Product Service", lifespan=lifespan)


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ProductIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    description: str = Field(default="", max_length=500)
    price: float = Field(gt=0)
    stock: int = Field(default=0, ge=0)
    category_id: int | None = None


class StockChange(BaseModel):
    delta: int  # negative = reserve / sell, positive = restore / restock


def product_out(p: Product):
    return {"id": p.id, "name": p.name, "description": p.description, "price": p.price,
            "stock": p.stock, "category_id": p.category_id}


def get_or_404(db: Session, product_id: int) -> Product:
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(404, "Product not found")
    return product


def check_category(db: Session, category_id: int | None):
    if category_id is not None and not db.get(Category, category_id):
        raise HTTPException(400, "Category does not exist")


@app.get("/health")
def health():
    return {"service": "product", "status": "ok"}


# ---- categories
@app.get("/categories")
def list_categories(db: Session = Depends(get_db)):
    return [{"id": c.id, "name": c.name} for c in db.query(Category).all()]


@app.post("/categories", status_code=201, dependencies=[Depends(require_admin)])
def create_category(body: CategoryIn, db: Session = Depends(get_db)):
    if db.query(Category).filter_by(name=body.name).first():
        raise HTTPException(409, "Category already exists")
    cat = Category(name=body.name)
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return {"id": cat.id, "name": cat.name}


# ---- products
@app.get("/products")
def list_products(category_id: int | None = None, db: Session = Depends(get_db)):
    query = db.query(Product)
    if category_id is not None:
        query = query.filter_by(category_id=category_id)
    return [product_out(p) for p in query.all()]


@app.get("/products/{product_id}")
def read_product(product_id: int, db: Session = Depends(get_db)):
    return product_out(get_or_404(db, product_id))


@app.post("/products", status_code=201, dependencies=[Depends(require_admin)])
def create_product(body: ProductIn, db: Session = Depends(get_db)):
    check_category(db, body.category_id)
    product = Product(**body.model_dump())
    db.add(product)
    db.commit()
    db.refresh(product)
    return product_out(product)


@app.put("/products/{product_id}", dependencies=[Depends(require_admin)])
def update_product(product_id: int, body: ProductIn, db: Session = Depends(get_db)):
    product = get_or_404(db, product_id)
    check_category(db, body.category_id)
    for key, value in body.model_dump().items():
        setattr(product, key, value)
    db.commit()
    return product_out(product)


@app.delete("/products/{product_id}", status_code=204, dependencies=[Depends(require_admin)])
def delete_product(product_id: int, db: Session = Depends(get_db)):
    db.delete(get_or_404(db, product_id))
    db.commit()
    return Response(status_code=204)


@app.patch("/products/{product_id}/stock")
def change_stock(product_id: int, body: StockChange, db: Session = Depends(get_db),
                 x_internal_key: str | None = Header(None), x_user_role: str = Header("customer")):
    """Used by Order Service (internal key) or an admin to adjust inventory."""
    if x_internal_key != INTERNAL_KEY and x_user_role != "admin":
        raise HTTPException(403, "Not allowed to change stock")
    product = db.query(Product).filter_by(id=product_id).with_for_update().first()
    if not product:
        raise HTTPException(404, "Product not found")
    if product.stock + body.delta < 0:
        raise HTTPException(409, f"Insufficient stock for product {product_id}")
    product.stock += body.delta
    db.commit()
    return product_out(product)
