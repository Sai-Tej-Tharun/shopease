"""Run all six services locally (SQLite, no Docker needed).  Stop with Ctrl+C."""
import os
import subprocess
import sys
import time

PORTS = {"auth_service": 8001, "user_service": 8002, "product_service": 8003,
         "order_service": 8004, "payment_service": 8005, "api_gateway": 8000}
root = os.path.dirname(os.path.abspath(__file__))
procs = []
try:
    for name, port in PORTS.items():
        folder = os.path.join(root, "services", name)
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "main:app", "--port", str(port), "--log-level", "warning"],
            cwd=folder))
    time.sleep(2)
    print("ShopEase is running  ->  Gateway: http://localhost:8000/docs")
    print("Admin login: admin@shopease.com / admin123      (Ctrl+C to stop)")
    for p in procs:
        p.wait()
except KeyboardInterrupt:
    pass
finally:
    for p in procs:
        p.terminate()
