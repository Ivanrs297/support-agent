import os
import platform
from datetime import datetime, timezone

from fastapi import FastAPI

app = FastAPI(title="Support Agent — Hello World")


@app.get("/")
def root():
    return {
        "message": "Hello from the agent host",
        "arch": platform.machine(),
        "python": platform.python_version(),
        "hostname": os.uname().nodename,
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/health")
def health():
    return {"status": "ok"}