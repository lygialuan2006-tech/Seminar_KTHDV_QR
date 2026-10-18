import base64
import io
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from app import create_app


@pytest.fixture
def client():
    return TestClient(create_app({"TESTING": True}))


def issue(client, qr_type="EVENT_CHECKIN"):
    return client.post(
        "/api/qr",
        json={
            "type": qr_type,
            "referenceId": "TICKET-TEST-1",
            "label": "Seminar demo",
        },
    )


def test_generate_resolve_and_serve_scannable_png(client):
    response = issue(client, "PRODUCT")
    assert response.status_code == 201
    payload = response.json()
    assert payload["type"] == "PRODUCT"
    assert payload["referenceId"] == "TICKET-TEST-1"
    png = base64.b64decode(payload["imagePngBase64"])
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG"
    assert image.width > 0
    image_url = payload["imageUrl"].replace("http://localhost:8000", "")
    assert client.get(image_url).headers["content-type"] == "image/png"
    resolved = client.get(f"/api/qr/resolve/{payload['token']}")
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "ISSUED"


def test_ticket_can_only_be_checked_in_once(client):
    token = issue(client).json()["token"]
    first = client.post("/api/check-ins", json={"token": token})
    second = client.post("/api/check-ins", json={"token": token})
    assert first.status_code == 200
    assert first.json()["result"] == "CHECKED_IN"
    assert second.status_code == 409


def test_rejects_product_checkin_and_invalid_requests(client):
    product_token = issue(client, "PRODUCT").json()["token"]
    assert (
        client.post("/api/check-ins", json={"token": product_token}).status_code == 400
    )
    assert (
        client.post(
            "/api/qr", json={"type": "NOPE", "referenceId": "x", "label": "x"}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/qr", json={"type": "PRODUCT", "referenceId": "", "label": "x"}
        ).status_code
        == 400
    )
    assert client.get("/api/qr/resolve/not-a-token").status_code == 404


def test_home_page_serves_ui(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Tạo QR" in response.text
    assert 'id="live-card"' in response.text
    assert client.get("/static/styles.css").status_code == 200


def test_ticket_page_is_readable_html(client):
    token = issue(client).json()["token"]
    page = client.get(f"/t/{token}")
    assert page.status_code == 200
    assert "Check-in sự kiện" in page.text
    assert "Hợp lệ" in page.text
    assert client.get("/t/khong-ton-tai").status_code == 404


def test_product_page_shows_info_without_checkin(client):
    response = client.post(
        "/api/qr",
        json={
            "type": "PRODUCT",
            "referenceId": "SKU-1001",
            "label": "Bình giữ nhiệt",
            "batch": "LOT-01",
            "info": "Bảo hành 12 tháng",
        },
    )
    token = response.json()["token"]
    page = client.get(f"/t/{token}").text
    assert "LOT-01" in page
    assert "Bảo hành 12 tháng" in page
    assert 'id="checkin"' not in page


def test_digital_ticket_shows_holder_and_checkin(client):
    response = client.post(
        "/api/qr",
        json={
            "type": "DIGITAL_TICKET",
            "referenceId": "TICKET-B12",
            "label": "Vé điện tử",
            "holder": "Nguyễn Văn B",
            "seat": "B12",
        },
    )
    token = response.json()["token"]
    page = client.get(f"/t/{token}").text
    assert "Nguyễn Văn B" in page
    assert "B12" in page
    assert 'id="checkin"' in page


def test_stats_follow_checkin_and_reset(client):
    token = issue(client).json()["token"]
    client.post("/api/check-ins", json={"token": token})
    stats = client.get("/api/stats").json()
    assert stats["checkedIn"] == 1
    assert stats["tickets"] == 1
    assert stats["issued"] == 0
    assert stats["recent"][0]["referenceId"] == "TICKET-TEST-1"

    # Đổi loại QR sẽ gọi /api/reset -> đếm lại từ 0
    assert client.post("/api/reset").json() == {"result": "RESET"}
    assert client.get("/api/stats").json()["checkedIn"] == 0
    assert client.post("/api/check-ins", json={"token": token}).status_code == 200


def test_reset_can_target_one_type(client):
    event_token = issue(client, "EVENT_CHECKIN").json()["token"]
    digital_token = issue(client, "DIGITAL_TICKET").json()["token"]
    client.post("/api/check-ins", json={"token": event_token})
    client.post("/api/check-ins", json={"token": digital_token})
    assert client.get("/api/stats").json()["checkedIn"] == 2

    # Chỉ xoá lịch sử của EVENT_CHECKIN
    client.post("/api/reset", params={"type": "EVENT_CHECKIN"})
    stats = client.get("/api/stats").json()
    assert stats["checkedIn"] == 1
    assert stats["recent"][0]["type"] == "DIGITAL_TICKET"
    assert client.post("/api/check-ins", json={"token": event_token}).status_code == 200
    assert (
        client.post("/api/check-ins", json={"token": digital_token}).status_code == 409
    )
