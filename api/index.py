"""
Vercel Serverless Entry Point for Querity FastAPI Backend.

Vercel's Python runtime invokes this file as an ASGI handler.
It imports the FastAPI `app` from backend/main.py after adding the
backend directory to sys.path so all relative imports work correctly.
"""

import sys
import os
from pathlib import Path

# Add the backend directory to Python path so all backend modules can be imported
backend_dir = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(backend_dir))

# Vercel runs in a read-only filesystem except for /tmp
# Point temp uploads to /tmp which is writable in serverless environments
os.environ.setdefault("TEMP_UPLOAD_DIR", "/tmp/querity_uploads")

# Import the FastAPI app — Vercel's ASGI adapter will wrap this
from main import app  # noqa: E402  (import after sys.path modification is intentional)

# Vercel expects the variable to be named `app` for ASGI detection
__all__ = ["app"]
