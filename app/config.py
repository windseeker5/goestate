import os
import secrets

from dotenv import load_dotenv

load_dotenv()


class Config:
    """Base configuration for Estate Copilot."""

    SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
    TEMPLATES_AUTO_RELOAD = True

    # Single-user local auth. Set LIQUIDATOR_PASSWORD in .env.
    LIQUIDATOR_PASSWORD = os.environ.get("LIQUIDATOR_PASSWORD")

    # SQLite database location (instance/ is gitignored).
    DATABASE_PATH = os.environ.get("DATABASE_PATH", "instance/estate.db")

    # Dev server port, used by app.py (python app.py).
    PORT = int(os.environ.get("FLASK_RUN_PORT", 5000))


class DevConfig(Config):
    DEBUG = True


class ProdConfig(Config):
    DEBUG = False
