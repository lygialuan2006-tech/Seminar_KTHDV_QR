"""Secure QR attendance: FastAPI app factory, configuration and CLI.

Run ``python app.py`` to serve, or ``python app.py --create-user`` to seed an
admin/staff account. Add ``--ngrok`` to expose the app over an HTTPS tunnel.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from models import Base, Event, EventStaff, StaffUser
from routes import admin_router, public_router, staff_router
from security import password_hasher

ROOT = Path(__file__).parent
STATIC_DIR = ROOT / "static"


def _config() -> dict[str, Any]:
    return {
        "DATABASE_URL": os.getenv(
            "DATABASE_URL", f"sqlite:///{ROOT / 'attendance.db'}"
        ),
        "SESSION_SECRET": os.getenv("SESSION_SECRET") or secrets.token_urlsafe(48),
        "SESSION_HTTPS_ONLY": os.getenv("SESSION_HTTPS_ONLY", "0") == "1",
        "QR_PUBLIC_BASE_URL": os.getenv(
            "QR_PUBLIC_BASE_URL", "http://localhost:8000"
        ).rstrip("/"),
        "APP_ENV": os.getenv("APP_ENV", "development"),
        "TRUSTED_HOSTS": [
            host.strip()
            for host in os.getenv(
                "TRUSTED_HOSTS", "localhost,127.0.0.1,testserver"
            ).split(",")
            if host.strip()
        ],
        "QR_VALIDITY_SECONDS": int(os.getenv("QR_VALIDITY_SECONDS", "86400")),
        "MAX_BODY_BYTES": int(os.getenv("MAX_BODY_BYTES", "16384")),
        "TESTING": False,
    }


def _check_production(cfg: dict[str, Any]) -> None:
    if cfg["APP_ENV"] != "production":
        return
    secret = os.getenv("SESSION_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError(
            "Production requires SESSION_SECRET with at least 32 characters"
        )
    if not cfg["SESSION_HTTPS_ONLY"]:
        raise RuntimeError("Production requires SESSION_HTTPS_ONLY=1")
    if not cfg["QR_PUBLIC_BASE_URL"].startswith("https://"):
        raise RuntimeError("Production requires QR_PUBLIC_BASE_URL to use HTTPS")


def create_app(test_config: dict[str, Any] | None = None) -> FastAPI:
    cfg = _config()
    if test_config:
        cfg.update(test_config)
    _check_production(cfg)

    engine = create_engine(
        cfg["DATABASE_URL"],
        connect_args=(
            {"check_same_thread": False}
            if cfg["DATABASE_URL"].startswith("sqlite")
            else {}
        ),
        pool_pre_ping=True,
    )
    if cfg["TESTING"]:
        Base.metadata.create_all(engine)

    app = FastAPI(
        title="Secure QR Attendance",
        docs_url=None if cfg["APP_ENV"] == "production" else "/docs",
        redoc_url=None,
        openapi_url=None if cfg["APP_ENV"] == "production" else "/openapi.json",
    )
    app.state.config = cfg
    app.state.engine = engine
    app.state.session_factory = sessionmaker(engine, expire_on_commit=False)
    app.state.rate = {}
    app.add_middleware(
        SessionMiddleware,
        secret_key=cfg["SESSION_SECRET"],
        session_cookie="qr_session",
        same_site="lax",
        https_only=cfg["SESSION_HTTPS_ONLY"],
        max_age=8 * 60 * 60,
    )
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=cfg["TRUSTED_HOSTS"] or ["localhost"]
    )
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.middleware("http")
    async def hardening(request: Request, call_next):
        if int(request.headers.get("content-length", "0") or 0) > cfg["MAX_BODY_BYTES"]:
            return JSONResponse({"error": "Request body too large"}, status_code=413)
        response = await call_next(request)
        response.headers.update(
            {
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "no-referrer",
                "Permissions-Policy": "camera=(self)",
                "Cache-Control": "no-store",
            }
        )
        if cfg["APP_ENV"] == "production":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException):
        return JSONResponse(
            {"error": exc.detail}, status_code=exc.status_code, headers=exc.headers
        )

    app.include_router(public_router)
    app.include_router(staff_router)
    app.include_router(admin_router)
    return app


def create_staff(
    username: str,
    password: str,
    role: str,
    event_id: int | None = None,
    database_url: str | None = None,
    all_events: bool = False,
) -> None:
    url = database_url or os.getenv(
        "DATABASE_URL", f"sqlite:///{ROOT / 'attendance.db'}"
    )
    engine = create_engine(
        url,
        connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
    )
    with sessionmaker(engine, expire_on_commit=False).begin() as session:
        if session.scalar(
            select(StaffUser).where(StaffUser.username == username.lower())
        ):
            raise SystemExit("Username already exists")
        user = StaffUser(
            username=username.lower(),
            password_hash=password_hasher.hash(password),
            role=role,
        )
        session.add(user)
        session.flush()
        if role == "staff":
            if all_events:
                events = session.scalars(
                    select(Event).where(Event.active.is_(True))
                ).all()
                if not events:
                    raise SystemExit("Staff account requires at least one active event")
                session.add_all(
                    EventStaff(user_id=user.id, event_id=event.id)
                    for event in events
                )
            else:
                if event_id is None or session.get(Event, event_id) is None:
                    raise SystemExit("Staff account requires an existing --event-id")
                session.add(EventStaff(user_id=user.id, event_id=event_id))
        print(f"Created {role} account: {username.lower()}")


def start_ngrok(port: int, timeout: float = 30.0) -> tuple[subprocess.Popen, str]:
    """Start the ngrok CLI and return its HTTPS URL from the local agent API."""
    executable = os.getenv("NGROK_EXECUTABLE") or shutil.which("ngrok")
    if not executable:
        raise SystemExit(
            "--ngrok was passed, but the ngrok CLI was not found in PATH. "
            "Install ngrok and configure its auth token first."
        )
    try:
        probe = subprocess.run(
            [executable, "version"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Cannot run ngrok at {executable}: {exc}") from exc
    if probe.returncode != 0:
        details = (probe.stderr or probe.stdout or "no version output").strip()
        raise RuntimeError(f"ngrok at {executable} did not run: {details}")
    process = subprocess.Popen(
        [executable, "http", str(port), "--log", "stdout"], stdin=subprocess.DEVNULL
    )
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(
                    "ngrok exited before creating a tunnel; check the CLI and its auth token."
                )
            try:
                with urllib.request.urlopen(
                    "http://127.0.0.1:4040/api/tunnels", timeout=1
                ) as response:
                    tunnels = json.load(response).get("tunnels", [])
                url = next(
                    (
                        item.get("public_url", "")
                        for item in tunnels
                        if item.get("public_url", "").startswith("https://")
                    ),
                    "",
                )
                if url:
                    return process, url.rstrip("/")
            except (OSError, urllib.error.URLError, ValueError):
                pass
            time.sleep(0.5)
        raise RuntimeError("Timed out waiting for ngrok to provide an HTTPS URL.")
    except Exception:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        raise


def _serve(app: FastAPI, use_ngrok: bool) -> None:
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    tunnel = None
    if use_ngrok:
        try:
            tunnel, public_url = start_ngrok(port)
        except (RuntimeError, OSError) as exc:
            raise SystemExit(f"Could not start ngrok: {exc}") from exc
        app.state.config["QR_PUBLIC_BASE_URL"] = public_url
        host_name = urlsplit(public_url).hostname
        for middleware in app.user_middleware:
            if middleware.cls is TrustedHostMiddleware:
                middleware.kwargs["allowed_hosts"] = [
                    host_name,
                    "localhost",
                    "127.0.0.1",
                ]
            elif middleware.cls is SessionMiddleware:
                middleware.kwargs["https_only"] = True
        app.state.config["SESSION_HTTPS_ONLY"] = True
        print(f"ngrok HTTPS URL: {public_url}")
        print("QR mới sẽ dùng URL này. URL có thể đổi khi khởi động lại ngrok.")
    try:
        uvicorn.run(app, host=host, port=port)
    finally:
        if tunnel and tunnel.poll() is None:
            tunnel.terminate()
            try:
                tunnel.wait(timeout=5)
            except subprocess.TimeoutExpired:
                tunnel.kill()


app = create_app()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Secure QR Attendance")
    parser.add_argument("--create-user", action="store_true")
    parser.add_argument("--username")
    parser.add_argument("--role", choices=("admin", "staff"), default="admin")
    parser.add_argument("--event-id", type=int)
    parser.add_argument(
        "--all-events",
        action="store_true",
        help="assign a staff account to every active event",
    )
    parser.add_argument(
        "--ngrok", action="store_true", help="start an ngrok HTTPS tunnel on boot"
    )
    args = parser.parse_args()
    if args.create_user:
        import getpass

        username = args.username or input("Username: ").strip()
        password = getpass.getpass("Password (12+ characters): ")
        if len(password) < 12:
            raise SystemExit("Password must have at least 12 characters")
        create_staff(
            username,
            password,
            args.role,
            args.event_id,
            all_events=args.all_events,
        )
    else:
        _serve(app, args.ngrok)
