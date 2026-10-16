"""QR seminar demo: generation, resolution, and single-use ticket check-in."""
from __future__ import annotations
import base64
import io
import os
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
import qrcode
from flask import Flask, jsonify, request, send_file
from werkzeug.exceptions import BadRequest
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
    """Raised when a token is missing or no longer valid."""

class AlreadyUsedError(Exception):
    """Raised when a ticket has already been checked in."""

def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        QR_PUBLIC_BASE_URL=os.environ.get("QR_PUBLIC_BASE_URL", "http://localhost:8080").rstrip("/"),
        QR_VALIDITY_SECONDS=int(os.environ.get("QR_VALIDITY_SECONDS", str(24 * 60 * 60))),
        QR_SIZE=int(os.environ.get("QR_SIZE", "8")),
        QR_BORDER=int(os.environ.get("QR_BORDER", "4")),
    )
    if test_config:
        app.config.update(test_config)
    app.extensions["qr_credentials"] = {}

    def credentials() -> dict[str, Credential]:
        return app.extensions["qr_credentials"]

    def resolve_url(token: str) -> str:
        return f"{app.config['QR_PUBLIC_BASE_URL']}/api/qr/resolve/{token}"

    def encode_qr(content: str) -> bytes:
        code = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=app.config["QR_SIZE"],
            border=app.config["QR_BORDER"],
        )
        code.add_data(content)
        code.make(fit=True)
        image = code.make_image(fill_color="black", back_color="white")
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    def valid_credential(token: str) -> Credential:
        credential = credentials().get(token)
        if credential is None:
            raise InvalidQrError("QR token was not found")
        if credential.expires_at <= datetime.now(timezone.utc):
            raise InvalidQrError("QR token has expired")
        return credential
    @app.errorhandler(InvalidQrError)
    def invalid_qr(error: InvalidQrError):
        return jsonify(error=str(error)), 404
    @app.errorhandler(AlreadyUsedError)
    def already_used(error: AlreadyUsedError):
        return jsonify(error=str(error)), 409
    @app.errorhandler(BadRequest)
    def invalid_json(_error: BadRequest):
        return jsonify(error="Request body must be valid JSON"), 400
    @app.post("/api/qr")
    def create_qr():
        body: Any = request.get_json(silent=False)
        if not isinstance(body, dict):
            return jsonify(error="JSON object is required"), 400
        try:
            qr_type = QrType(body.get("type"))
        except (ValueError, TypeError):
            return jsonify(error="type must be PRODUCT, EVENT_TICKET, or DIGITAL_TICKET"), 400
        reference_id = body.get("referenceId")
        label = body.get("label")
        if not isinstance(reference_id, str) or not reference_id.strip() or len(reference_id) > 100:
            return jsonify(error="referenceId must be a non-empty string of at most 100 characters"), 400
        if not isinstance(label, str) or not label.strip() or len(label) > 200:
            return jsonify(error="label must be a non-empty string of at most 200 characters"), 400
        token = secrets.token_urlsafe(24)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=app.config["QR_VALIDITY_SECONDS"])
        png = encode_qr(resolve_url(token))
        credential = Credential(token, qr_type, reference_id.strip(), label.strip(), expires_at, png)
        credentials()[token] = credential
        return jsonify(
            token=token,
            type=qr_type.value,
            referenceId=credential.reference_id,
            label=credential.label,
            url=resolve_url(token),
            imagePngBase64=base64.b64encode(png).decode("ascii"),
            imageUrl=f"{app.config['QR_PUBLIC_BASE_URL']}/api/qr/image/{token}",
            expiresAt=expires_at.isoformat(),
        ), 201
    @app.get("/api/qr/resolve/<token>")
    def resolve_qr(token: str):
        credential = valid_credential(token)
        return jsonify(
            token=credential.token,
            type=credential.qr_type.value,
            referenceId=credential.reference_id,
            label=credential.label,
            expiresAt=credential.expires_at.isoformat(),
            status="USED" if credential.used_at else "ISSUED",
        )

    @app.get("/api/qr/image/<token>")
    def qr_image(token: str):
        credential = valid_credential(token)
        return send_file(io.BytesIO(credential.image_png), mimetype="image/png", download_name="qr.png")

    @app.post("/api/check-ins")
    def check_in():
        body: Any = request.get_json(silent=False)
        if not isinstance(body, dict) or not isinstance(body.get("token"), str) or not body["token"].strip():
            return jsonify(error="token is required"), 400
        credential = valid_credential(body["token"].strip())
        if credential.qr_type not in (QrType.EVENT_TICKET, QrType.DIGITAL_TICKET):
            return jsonify(error="QR is not a ticket"), 400
        with credential.lock:
            if credential.used_at is not None:
                raise AlreadyUsedError("Ticket has already been checked in")
            credential.used_at = datetime.now(timezone.utc)
        return jsonify(
            result="CHECKED_IN",
            referenceId=credential.reference_id,
            checkedInAt=credential.used_at.isoformat(),
        )

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    return app
app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), debug=False)

