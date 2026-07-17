"""Standalone "Letter From Your Future Self" application for Render."""
import os
from datetime import timedelta

from flask import Flask, redirect
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

from database import db
from routes import letter_bp


def create_app():
    app = Flask(__name__)
    # Render terminates HTTPS before forwarding the request to Gunicorn.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
    production = os.getenv("FLASK_ENV", "development").lower() == "production"
    secret = os.getenv("SECRET_KEY")
    if production and not secret:
        raise RuntimeError("SECRET_KEY must be set in production")
    app.config.update(
        SECRET_KEY=secret or "local-development-only",
        SQLALCHEMY_DATABASE_URI=os.getenv("DATABASE_URL", "sqlite:///letter.db"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=production,
        DEBUG=not production,
    )
    CORS(app, resources={r"/letter/api/*": {"origins": os.getenv("ALLOWED_ORIGINS", "*").split(",")}})
    db.init_app(app)
    app.register_blueprint(letter_bp)

    @app.get("/")
    def index():
        return redirect("/letter/")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    with app.app_context():
        db.create_all()
        _run_light_migrations()
    return app


def _run_light_migrations():
    """create_all() only adds missing TABLES, never new COLUMNS on tables
    that already exist — so any column added after the first deploy needs
    a guard here, or every request against a live/persistent DB breaks."""
    from sqlalchemy import inspect, text
    inspector = inspect(db.engine)
    if "lfs_orders" not in inspector.get_table_names():
        return
    existing = {c["name"] for c in inspector.get_columns("lfs_orders")}
    with db.engine.begin() as conn:
        if "share_count" not in existing:
            conn.execute(text(
                "ALTER TABLE lfs_orders ADD COLUMN share_count INTEGER DEFAULT 0"
            ))


app = create_app()
