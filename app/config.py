import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data")).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
DATABASE_URL = os.getenv("DATABASE_URL") or f"sqlite:///{DATA_DIR / 'app.db'}"
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "10")) * 1024 * 1024

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM") or SMTP_USER
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
# Email verification is enforced only when SMTP is configured
EMAIL_VERIFICATION_ENABLED = bool(SMTP_HOST)

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "").strip().lower()

CATEGORIES = ["Cloud", "DevOps", "SRE", "AI Engineering"]
ALLOWED_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".md", ".txt"}
PAGE_SIZE = 12
