import base64
import io
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import sessionmaker

from app import (Base, Event, EventStaff, StaffUser, Ticket, create_app,
                 decrypt_payload, encrypt_payload, password_hasher, token_digest)


@pytest.fixture
def env(tmp_path):
    url = f"sqlite:///{tmp_path / 'attendance.db'}"
    app = create_app({"TESTING": True, "DATABASE_URL": url, "SESSION_SECRET": "test-session-secret-that-is-long-enough"})
    factory = app.state.session_factory
    with factory.begin() as session:
        admin = StaffUser(username="admin", password_hash=password_hasher.hash("admin-password-123"), role="admin")
        staff = StaffUser(username="staff", password_hash=password_hasher.hash("staff-password-123"), role="staff")
        outsider = StaffUser(username="outsider", password_hash=password_hasher.hash("outsider-password-123"), role="staff")
        event = Event(name="Seminar")
        session.add_all([admin, staff, outsider, event]); session.flush()
        session.add(EventStaff(user_id=staff.id, event_id=event.id))
    return app, factory, event.id


def login(client, username="admin", password="admin-password-123"):
    csrf = client.get("/api/csrf").json()["csrfToken"]
    response = client.post("/api/login", headers={"X-CSRF-Token": csrf}, json={"username": username, "password": password})
    assert response.status_code == 200
    return response.json()["csrfToken"]


def issue(client, csrf, event_id, qr_type="EVENT_CHECKIN", ref="TICKET-1"):
    return client.post("/api/qr", headers={"X-CSRF-Token": csrf}, json={"type": qr_type, "eventId": event_id if qr_type != "PRODUCT" else None, "referenceId": ref, "label": "Seminar"})


def test_admin_issues_opaque_qr_and_staff_checks_in_once(env):
    app, _, event_id = env
    admin, staff = TestClient(app), TestClient(app)
    csrf = login(admin)
    created = issue(admin, csrf, event_id)
    assert created.status_code == 201
    ticket = created.json()
    assert len(ticket["token"]) > 40 and "url" not in ticket and "imageUrl" not in ticket
    with app.state.session_factory() as session:
        row = session.query(Ticket).one()
        assert row.token_hash == token_digest(ticket["token"])
        assert ticket["token"] not in row.token_hash
    png = Image.open(io.BytesIO(base64.b64decode(ticket["imagePngBase64"])))
    assert png.format == "PNG"
    unauth_csrf = staff.get("/api/csrf").json()["csrfToken"]
    assert staff.post("/api/check-ins", headers={"X-CSRF-Token": unauth_csrf}, json={"token": ticket["token"]}).status_code == 401
    staff_csrf = login(staff, "staff", "staff-password-123")
    response = staff.post("/api/check-ins", headers={"X-CSRF-Token": staff_csrf}, json={"token": ticket["token"]})
    assert response.status_code == 200
    assert staff.post("/api/check-ins", headers={"X-CSRF-Token": staff_csrf}, json={"token": ticket["token"]}).status_code == 409


def test_unauthorized_issuance_and_invalid_token_rejected(env):
    app, _, event_id = env
    client = TestClient(app)
    csrf = client.get("/api/csrf").json()["csrfToken"]
    assert issue(client, csrf, event_id).status_code == 401
    staff = TestClient(app)
    staff_csrf = login(staff, "staff", "staff-password-123")
    assert issue(staff, staff_csrf, event_id).status_code == 403
    response = staff.post("/api/check-ins", headers={"X-CSRF-Token": staff_csrf}, json={"token": "random-invalid-token"})
    assert response.status_code == 404


def test_expired_and_revoked_tickets(env):
    app, factory, event_id = env
    admin, staff = TestClient(app), TestClient(app)
    csrf = login(admin)
    expired = issue(admin, csrf, event_id, ref="EXPIRED").json()
    revoked = issue(admin, csrf, event_id, ref="REVOKED").json()
    with factory.begin() as session:
        rows = {t.reference_id: t for t in session.query(Ticket).all()}
        rows["EXPIRED"].expires_at = __import__("datetime").datetime(2000, 1, 1, tzinfo=__import__("datetime").timezone.utc)
    revoked_row = next(t for t in admin.get("/api/tickets", headers={"X-CSRF-Token": csrf}).json()["tickets"] if t["referenceId"] == "REVOKED")
    assert admin.post(f"/api/tickets/{revoked_row['id']}/revoke", headers={"X-CSRF-Token": csrf}).status_code == 200
    staff_csrf = login(staff, "staff", "staff-password-123")
    assert staff.post("/api/check-ins", headers={"X-CSRF-Token": staff_csrf}, json={"token": expired["token"]}).status_code == 410
    assert staff.post("/api/check-ins", headers={"X-CSRF-Token": staff_csrf}, json={"token": revoked["token"]}).status_code == 410


def test_staff_event_scope_and_product_is_not_checkin(env):
    app, _, event_id = env
    admin, staff, outsider = TestClient(app), TestClient(app), TestClient(app)
    csrf = login(admin)
    event_ticket = issue(admin, csrf, event_id).json()
    product = issue(admin, csrf, event_id, "PRODUCT", "SKU-1").json()
    outsider_csrf = login(outsider, "outsider", "outsider-password-123")
    assert outsider.post("/api/check-ins", headers={"X-CSRF-Token": outsider_csrf}, json={"token": event_ticket["token"]}).status_code == 403
    staff_csrf = login(staff, "staff", "staff-password-123")
    assert staff.post("/api/check-ins", headers={"X-CSRF-Token": staff_csrf}, json={"token": product["token"]}).status_code == 404


def test_concurrent_checkin_has_exactly_one_winner(env):
    app, _, event_id = env
    admin = TestClient(app)
    csrf = login(admin)
    token = issue(admin, csrf, event_id).json()["token"]
    clients = [TestClient(app), TestClient(app)]
    csrf_tokens = [login(c, "staff", "staff-password-123") for c in clients]
    def submit(i):
        return clients[i].post("/api/check-ins", headers={"X-CSRF-Token": csrf_tokens[i]}, json={"token": token}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(submit, [0, 1]))
    assert sorted(statuses) == [200, 409]


def test_no_token_in_url_logs_or_frontend_and_no_public_reset(env, caplog):
    app, _, event_id = env
    admin = TestClient(app)
    csrf = login(admin)
    response = issue(admin, csrf, event_id)
    token = response.json()["token"]
    assert token not in response.json().get("url", "")
    assert token not in admin.get("/").text
    assert token not in caplog.text
    assert admin.post("/api/reset").status_code == 410
    assert admin.get(f"/t/{token}").status_code == 410


def test_product_lookup_uses_body_api_and_legacy_url_is_gone(env):
    app, _, event_id = env
    admin = TestClient(app)
    csrf = login(admin)
    product = issue(admin, csrf, event_id, "PRODUCT", "SKU").json()
    assert admin.get(f"/api/qr/resolve/{product['token']}").status_code == 410
    lookup = admin.post("/api/products/lookup", headers={"X-CSRF-Token": csrf}, json={"token": product["token"]})
    assert lookup.status_code == 200 and lookup.json()["referenceId"] == "SKU"
    assert "Tra cứu sản phẩm" in admin.get("/").text


def test_product_public_qr_page_is_repeatable_and_never_checkin(env):
    app, _, event_id = env
    admin, staff = TestClient(app), TestClient(app)
    csrf = login(admin)
    product = admin.post("/api/qr", headers={"X-CSRF-Token": csrf}, json={"type": "PRODUCT", "referenceId": "SKU-7", "label": "Bình giữ nhiệt", "batch": "LOT-7", "info": "Bảo hành 12 tháng"}).json()
    assert product["publicUrl"].startswith("http://localhost:8000/p/")
    assert "token=" not in product["publicUrl"]
    for _ in range(2):
        page = staff.get(product["publicUrl"])
        assert page.status_code == 200 and "Bảo hành 12 tháng" in page.text
        details = staff.get(product["publicUrl"].replace("/p/", "/api/products/"))
        assert details.status_code == 200 and details.json()["referenceId"] == "SKU-7"
    staff_csrf = login(staff, "staff", "staff-password-123")
    assert staff.post("/api/check-ins", headers={"X-CSRF-Token": staff_csrf}, json={"token": product["publicUrl"]}).status_code == 404


def test_digital_ticket_lookup_does_not_consume_but_staff_can_redeem(env):
    app, _, event_id = env
    admin, staff = TestClient(app), TestClient(app)
    csrf = login(admin)
    created = issue(admin, csrf, event_id, "DIGITAL_TICKET", "DIGITAL-1").json()
    assert created["publicUrl"].startswith("http://localhost:8000/v/")
    for _ in range(2):
        check = staff.get(created["publicUrl"])
        assert check.status_code == 200 and "Vé hợp lệ" in check.text
        api_status = staff.get(created["publicUrl"].replace("/v/", "/api/tickets/public/"))
        assert api_status.status_code == 200 and api_status.json()["status"] == "VALID"
    staff_csrf = login(staff, "staff", "staff-password-123")
    redeemed = staff.post("/api/check-ins", headers={"X-CSRF-Token": staff_csrf}, json={"token": created["publicUrl"]})
    assert redeemed.status_code == 200
    assert staff.get(created["publicUrl"].replace("/v/", "/api/tickets/public/")).json()["status"] == "USED"


def test_database_persists_after_app_restart(env):
    app, _, event_id = env
    client = TestClient(app); csrf = login(client)
    token = issue(client, csrf, event_id).json()["token"]
    restarted = create_app({"TESTING": True, "DATABASE_URL": app.state.config["DATABASE_URL"], "SESSION_SECRET": "second-long-test-secret-value"})
    with restarted.state.session_factory() as session:
        row = session.query(Ticket).one()
        assert row.token_hash == token_digest(token)


def test_qra1_tamper_rejected_and_aad_bound_to_event():
    key = b"k" * 32
    payload = encrypt_payload(b"sensitive", key, 15, kid="key-v1")
    keys = {"key-v1": key}
    assert decrypt_payload(payload, keys, 15) == b"sensitive"
    prefix, kid, encoded = payload.split(".")
    raw = bytearray(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    raw[-1] ^= 1
    changed = prefix + "." + kid + "." + base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    with pytest.raises(ValueError): decrypt_payload(changed, keys, 15)
    with pytest.raises(ValueError): decrypt_payload(payload, keys, 16)
    with pytest.raises(ValueError): encrypt_payload(b"x", b"short", 15)


def test_invalid_production_and_aes_configuration_fail_closed(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    with pytest.raises(RuntimeError): create_app()
    monkeypatch.setenv("SESSION_SECRET", "long-enough-production-session-secret-value")
    monkeypatch.setenv("QR_AES_KEYS", '{"v1":"not-base64"}')
    with pytest.raises(RuntimeError): create_app()


def test_login_errors_do_not_reveal_account_existence(env):
    app, _, _ = env
    client = TestClient(app); csrf = client.get("/api/csrf").json()["csrfToken"]
    one = client.post("/api/login", headers={"X-CSRF-Token": csrf}, json={"username": "unknown", "password": "wrong"})
    another_csrf = client.get("/api/csrf").json()["csrfToken"]
    two = client.post("/api/login", headers={"X-CSRF-Token": another_csrf}, json={"username": "admin", "password": "wrong"})
    assert one.status_code == two.status_code == 401
    assert one.json() == two.json()


def test_login_form_scanner_and_auth_headers(env):
    app, _, _ = env
    client = TestClient(app)
    assert "Đăng nhập" in client.get("/login").text
    assert client.get("/scanner").status_code == 401
    response = client.get("/")
    assert response.status_code == 200 and "Tạo QR" in response.text
    assert response.headers["x-frame-options"] == "DENY"


def test_dashboard_keeps_authenticated_session_after_csrf_refresh(env):
    app, _, _ = env
    client = TestClient(app)
    login(client)
    refreshed_csrf = client.get("/api/csrf").json()["csrfToken"]
    session = client.get("/api/session")
    assert session.status_code == 200 and session.json()["role"] == "admin"
    assert client.get("/api/stats").status_code == 200
    assert client.get("/api/tickets").status_code == 200
    assert client.get("/api/check-ins").status_code == 200
