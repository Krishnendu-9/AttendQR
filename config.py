import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


class Config:
    SECRET_KEY = os.environ.get(
        "SECRET_KEY",
        "attendance-system-secret-key"
    )

    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        "sqlite:///attendance.db"
    )

    DEFAULT_PRINCIPAL_EMAIL = os.environ.get(
        "DEFAULT_PRINCIPAL_EMAIL",
        "principal@example.com"
    )

    DEFAULT_PRINCIPAL_PASSWORD = os.environ.get(
        "DEFAULT_PRINCIPAL_PASSWORD",
        "ChangeMe123!"
    )

    ALLOWED_STAFF_DOMAINS = [
        d.strip().lower()
        for d in os.environ.get("ALLOWED_STAFF_DOMAINS", "@rcciit.org.in,@gmail.com").split(",")
        if d.strip()
    ]

    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB max upload size

    SQLALCHEMY_TRACK_MODIFICATIONS = False
