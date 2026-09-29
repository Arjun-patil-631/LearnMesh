# Vercel serverless entrypoint — exposes the FastAPI app as `app`.
# Secrets are NEVER in code: set them in Vercel Dashboard → Project → Settings → Environment Variables.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Vercel filesystem is read-only except /tmp: keep SQLite there (resets on redeploy — demo-safe).
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/learnmesh.db")

from backend.main import app  # noqa: E402  (must import after env setup)
