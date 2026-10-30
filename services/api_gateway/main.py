"""API Gateway – single entry point: JWT check + request routing."""
import os

import httpx
import jwt
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
ROUTES = {
    "auth": os.getenv("AUTH_SERVICE_URL", "http://localhost:8001"),
    "users": os.getenv("USER_SERVICE_URL", "http://localhost:8002"),
    "products": os.getenv("PRODUCT_SERVICE_URL", "http://localhost:8003"),
    "categories": os.getenv("PRODUCT_SERVICE_URL", "http://localhost:8003"),
    "orders": os.getenv("ORDER_SERVICE_URL", "http://localhost:8004"),
    "payments": os.getenv("PAYMENT_SERVICE_URL", "http://localhost:8005"),
}
PUBLIC_POST = {"auth/register", "auth/login"}   # no token needed
PUBLIC_GET_PREFIXES = ("products", "categories")  # browsing the catalog is public

app = FastAPI(title="ShopEase – API Gateway")


@app.get("/health")
def health():
    return {"service": "gateway", "status": "ok"}


def is_public(method: str, path: str) -> bool:
    if method == "POST" and path in PUBLIC_POST:
        return True
    return method == "GET" and path.split("/")[0] in PUBLIC_GET_PREFIXES


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(path: str, request: Request):
    path = path.strip("/")
    target = ROUTES.get(path.split("/")[0])
    if not target:
        return JSONResponse({"detail": "Route not found"}, status_code=404)

    # never trust identity headers coming from the client
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in {"host", "content-length", "x-user-id", "x-user-role", "x-internal-key"}}

    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            claims = jwt.decode(auth[7:], SECRET_KEY, algorithms=["HS256"])
            headers["X-User-Id"], headers["X-User-Role"] = claims["sub"], claims.get("role", "customer")
        except jwt.PyJWTError:
            return JSONResponse({"detail": "Invalid or expired token"}, status_code=401)
    elif not is_public(request.method, path):
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.request(request.method, f"{target}/{path}", params=request.query_params,
                                        content=await request.body(), headers=headers)
    except httpx.HTTPError:
        return JSONResponse({"detail": f"Service for '/{path.split('/')[0]}' is unavailable"}, status_code=503)
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type=resp.headers.get("content-type"))
