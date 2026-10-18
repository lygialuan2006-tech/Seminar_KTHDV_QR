"""QR seminar demo (FastAPI): tạo QR, tra cứu và check-in vé một lần."""

from __future__ import annotations

import base64
import io
import os
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

import qrcode
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse


class QrType(str, Enum):
    PRODUCT = "PRODUCT"
    EVENT_TICKET = "EVENT_TICKET"
    DIGITAL_TICKET = "DIGITAL_TICKET"


@dataclass
class Credential:
    token: str
    qr_type: QrType
    reference_id: str
    label: str
    expires_at: datetime
    image_png: bytes
    used_at: datetime | None = None
    lock: threading.Lock | None = None

    def __post_init__(self) -> None:
        self.lock = threading.Lock()


class InvalidQrError(Exception):
    """Token không tồn tại hoặc đã hết hạn."""


class AlreadyUsedError(Exception):
    """Vé đã được check-in."""


class BadRequestError(Exception):
    """Request không hợp lệ."""


def create_app(test_config: dict[str, Any] | None = None) -> FastAPI:
    app = FastAPI(title="QR Seminar Demo")
    app.state.config = {
        "QR_PUBLIC_BASE_URL": os.environ.get(
            "QR_PUBLIC_BASE_URL", "http://localhost:8000"
        ).rstrip("/"),
        "QR_VALIDITY_SECONDS": int(
            os.environ.get("QR_VALIDITY_SECONDS", str(24 * 60 * 60))
        ),
        "QR_SIZE": int(os.environ.get("QR_SIZE", "8")),
        "QR_BORDER": int(os.environ.get("QR_BORDER", "4")),
    }
    if test_config:
        app.state.config.update(test_config)
    app.state.credentials = {}

    config = app.state.config

    def resolve_url(token: str) -> str:
        return f"{config['QR_PUBLIC_BASE_URL']}/api/qr/resolve/{token}"

    def encode_qr(content: str) -> bytes:
        code = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=config["QR_SIZE"],
            border=config["QR_BORDER"],
        )
        code.add_data(content)
        code.make(fit=True)
        image = code.make_image(fill_color="black", back_color="white")
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    def valid_credential(token: str) -> Credential:
        credential = app.state.credentials.get(token)
        if credential is None:
            raise InvalidQrError("QR token was not found")
        if credential.expires_at <= datetime.now(timezone.utc):
            raise InvalidQrError("QR token has expired")
        return credential

    async def read_json(request: Request) -> Any:
        try:
            return await request.json()
        except Exception:
            raise BadRequestError("Request body must be valid JSON")

    @app.exception_handler(InvalidQrError)
    async def invalid_qr(_request: Request, error: InvalidQrError):
        return JSONResponse({"error": str(error)}, status_code=404)

    @app.exception_handler(AlreadyUsedError)
    async def already_used(_request: Request, error: AlreadyUsedError):
        return JSONResponse({"error": str(error)}, status_code=409)

    @app.exception_handler(BadRequestError)
    async def invalid_request(_request: Request, error: BadRequestError):
        return JSONResponse({"error": str(error)}, status_code=400)

    @app.post("/api/qr", status_code=201)
    async def create_qr(request: Request):
        body = await read_json(request)
        if not isinstance(body, dict):
            raise BadRequestError("JSON object is required")
        try:
            qr_type = QrType(body.get("type"))
        except (ValueError, TypeError):
            raise BadRequestError(
                "type must be PRODUCT, EVENT_TICKET, or DIGITAL_TICKET"
            )
        reference_id = body.get("referenceId")
        label = body.get("label")
        if (
            not isinstance(reference_id, str)
            or not reference_id.strip()
            or len(reference_id) > 100
        ):
            raise BadRequestError(
                "referenceId must be a non-empty string of at most 100 characters"
            )
        if not isinstance(label, str) or not label.strip() or len(label) > 200:
            raise BadRequestError(
                "label must be a non-empty string of at most 200 characters"
            )
        token = secrets.token_urlsafe(24)
        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=config["QR_VALIDITY_SECONDS"]
        )
        png = encode_qr(resolve_url(token))
        credential = Credential(
            token, qr_type, reference_id.strip(), label.strip(), expires_at, png
        )
        app.state.credentials[token] = credential
        return JSONResponse(
            {
                "token": token,
                "type": qr_type.value,
                "referenceId": credential.reference_id,
                "label": credential.label,
                "url": resolve_url(token),
                "imagePngBase64": base64.b64encode(png).decode("ascii"),
                "imageUrl": f"{config['QR_PUBLIC_BASE_URL']}/api/qr/image/{token}",
                "expiresAt": expires_at.isoformat(),
            },
            status_code=201,
        )

    @app.get("/api/qr/resolve/{token}")
    def resolve_qr(token: str):
        credential = valid_credential(token)
        return {
            "token": credential.token,
            "type": credential.qr_type.value,
            "referenceId": credential.reference_id,
            "label": credential.label,
            "expiresAt": credential.expires_at.isoformat(),
            "status": "USED" if credential.used_at else "ISSUED",
        }

    @app.get("/api/qr/image/{token}")
    def qr_image(token: str):
        credential = valid_credential(token)
        return Response(
            content=credential.image_png,
            media_type="image/png",
            headers={"Content-Disposition": 'inline; filename="qr.png"'},
        )

    @app.post("/api/check-ins")
    async def check_in(request: Request):
        body = await read_json(request)
        if (
            not isinstance(body, dict)
            or not isinstance(body.get("token"), str)
            or not body["token"].strip()
        ):
            raise BadRequestError("token is required")
        credential = valid_credential(body["token"].strip())
        if credential.qr_type not in (QrType.EVENT_TICKET, QrType.DIGITAL_TICKET):
            raise BadRequestError("QR is not a ticket")
        with credential.lock:
            if credential.used_at is not None:
                raise AlreadyUsedError("Ticket has already been checked in")
            credential.used_at = datetime.now(timezone.utc)
        return {
            "result": "CHECKED_IN",
            "referenceId": credential.reference_id,
            "checkedInAt": credential.used_at.isoformat(),
        }

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


def start_ngrok(port: int) -> str | None:
    """Mở tunnel công khai. Cần chạy `ngrok config add-authtoken ...` trước."""
    try:
        from pyngrok import ngrok
    except ImportError:
        print("[ngrok] Chưa cài pyngrok: pip install pyngrok")
        return None
    for attempt in range(3):
        try:
            return ngrok.connect(port, "http").public_url
        except Exception as error:
            print(f"[ngrok] Lỗi lần {attempt + 1}: {error}")
            time.sleep(2)
    return None


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    # Đặt USE_NGROK=0 để tắt ngrok
    if os.environ.get("USE_NGROK", "1") == "1":
        public_url = start_ngrok(port)
        if public_url:
            app.state.config["QR_PUBLIC_BASE_URL"] = public_url
            print(f"[ngrok] Public URL: {public_url}")
    uvicorn.run(app, host="0.0.0.0", port=port)
