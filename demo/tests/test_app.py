import base64
import io
import pytest
from PIL import Image
from app import create_app
@pytest.fixture
def client():
    app = create_app({"TESTING": True})
    return app.test_client()

def issue(client, qr_type="EVENT_TICKET"):
    return client.post("/api/qr", json={
        "type": qr_type,
        "referenceId": "TICKET-TEST-1",
        "label": "Seminar demo",
    })

def test_generate_resolve_and_serve_scannable_png(client):
    response = issue(client, "PRODUCT")
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["type"] == "PRODUCT"
    assert payload["referenceId"] == "TICKET-TEST-1"
    png = base64.b64decode(payload["imagePngBase64"])
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG"
    assert image.width > 0
    assert client.get(payload["imageUrl"].replace("http://localhost:8080", "")).mimetype == "image/png"
    resolved = client.get(f"/api/qr/resolve/{payload['token']}")
    assert resolved.status_code == 200
    assert resolved.get_json()["status"] == "ISSUED"

def test_ticket_can_only_be_checked_in_once(client):
    token = issue(client).get_json()["token"]
    first = client.post("/api/check-ins", json={"token": token})
    second = client.post("/api/check-ins", json={"token": token})
    assert first.status_code == 200
    assert first.get_json()["result"] == "CHECKED_IN"
    assert second.status_code == 409

def test_rejects_product_checkin_and_invalid_requests(client):
    product_token = issue(client, "PRODUCT").get_json()["token"]
    assert client.post("/api/check-ins", json={"token": product_token}).status_code == 400
    assert client.post("/api/qr", json={"type": "NOPE", "referenceId": "x", "label": "x"}).status_code == 400
    assert client.post("/api/qr", json={"type": "PRODUCT", "referenceId": "", "label": "x"}).status_code == 400
    assert client.get("/api/qr/resolve/not-a-token").status_code == 404
