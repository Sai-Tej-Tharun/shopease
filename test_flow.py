"""End-to-end test of the whole ShopEase flow through the API Gateway.
Start the system first (python run_local.py  or  docker compose up), then:  python test_flow.py
"""
import sys
import uuid

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
c = httpx.Client(base_url=BASE, timeout=15)
failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS  " if cond else "FAIL  ") + name + (f"   {extra}" if extra and not cond else ""))
    failures += 0 if cond else 1


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


email = f"user_{uuid.uuid4().hex[:6]}@example.com"

# 1. registration + login (JWT)
r = c.post("/auth/register", json={"email": email, "password": "Secret@123"})
check("register user -> 201", r.status_code == 201, r.text)
check("duplicate email -> 409", c.post("/auth/register", json={"email": email, "password": "Secret@123"}).status_code == 409)
check("wrong password -> 401", c.post("/auth/login", json={"email": email, "password": "nope-nope"}).status_code == 401)
user = bearer(c.post("/auth/login", json={"email": email, "password": "Secret@123"}).json()["access_token"])
admin = bearer(c.post("/auth/login", json={"email": "admin@shopease.com", "password": "admin123"}).json()["access_token"])
check("protected route without token -> 401", c.get("/orders").status_code == 401)
check("fake token -> 401", c.get("/orders", headers=bearer("abc.def.ghi")).status_code == 401)

# 2. profile + address
check("update profile", c.put("/users/me", json={"full_name": "Test User", "phone": "9999999999"}, headers=user).status_code == 200)
addr = c.post("/users/me/addresses", json={"line1": "1 Main St", "city": "Hyderabad", "state": "TS", "pincode": "500001"}, headers=user)
check("add address -> 201", addr.status_code == 201, addr.text)

# 3. catalog (admin creates, anyone browses)
check("customer cannot create product -> 403",
      c.post("/products", json={"name": "X", "price": 1, "stock": 1}, headers=user).status_code == 403)
cat = c.post("/categories", json={"name": f"Gadgets-{uuid.uuid4().hex[:4]}"}, headers=admin).json()
prod = c.post("/products", json={"name": "Wireless Mouse", "price": 500, "stock": 10, "category_id": cat["id"]}, headers=admin)
check("admin creates product -> 201", prod.status_code == 201, prod.text)
pid = prod.json()["id"]
check("browse products without login", c.get("/products").status_code == 200)
stock = lambda: c.get(f"/products/{pid}").json()["stock"]

# 4. order -> availability check -> payment -> status
r = c.post("/orders", json={"items": [{"product_id": pid, "quantity": 2}], "payment_method": "upi"}, headers=user)
check("create order -> 201 CONFIRMED", r.status_code == 201 and r.json()["status"] == "CONFIRMED", r.text)
oid = r.json()["id"]
check("total = 2 x 500", r.json()["total_amount"] == 1000)
check("stock reduced 10 -> 8", stock() == 8, f"stock={stock()}")
check("payment recorded", c.get(f"/payments/order/{oid}", headers=user).json()[0]["status"] == "SUCCESS")

# 5. failure paths
r = c.post("/orders", json={"items": [{"product_id": pid, "quantity": 99}]}, headers=user)
check("insufficient stock -> 409", r.status_code == 409, r.text)
r = c.post("/orders", json={"items": [{"product_id": 99999, "quantity": 1}]}, headers=user)
check("unknown product -> 404", r.status_code == 404, r.text)
r = c.post("/orders", json={"items": [{"product_id": pid, "quantity": 3}], "payment_method": "fail_test"}, headers=user)
check("payment failure -> 402", r.status_code == 402, r.text)
check("stock restored after failed payment (still 8)", stock() == 8, f"stock={stock()}")

# 6. history, status, cancel
hist = c.get("/orders", headers=user).json()
check("order history has CONFIRMED + FAILED orders", {o["status"] for o in hist} >= {"CONFIRMED", "FAILED"})
check("customer cannot ship -> 403", c.patch(f"/orders/{oid}/status", json={"status": "SHIPPED"}, headers=user).status_code == 403)
check("admin ships order", c.patch(f"/orders/{oid}/status", json={"status": "SHIPPED"}, headers=admin).json()["status"] == "SHIPPED")
r2 = c.post("/orders", json={"items": [{"product_id": pid, "quantity": 1}]}, headers=user).json()
check("stock 8 -> 7 after 2nd order", stock() == 7)
check("customer cancels own order", c.patch(f"/orders/{r2['id']}/status", json={"status": "CANCELLED"}, headers=user).json()["status"] == "CANCELLED")
check("cancel restores stock (back to 8)", stock() == 8, f"stock={stock()}")
check("payment history", len(c.get("/payments/history", headers=user).json()) >= 2)

print("\nALL TESTS PASSED" if not failures else f"\n{failures} TEST(S) FAILED")
sys.exit(1 if failures else 0)
