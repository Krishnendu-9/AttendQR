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
        "principal@rcciit.org.in"
    )

    DEFAULT_PRINCIPAL_PASSWORD = os.environ.get(
        "DEFAULT_PRINCIPAL_PASSWORD",
        "RCC@qr2026"
    )

    SQLALCHEMY_TRACK_MODIFICATIONS = False
