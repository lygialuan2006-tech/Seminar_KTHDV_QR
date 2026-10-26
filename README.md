# Secure QR Attendance — Seminar KTHDV

Ứng dụng điểm danh bằng QR: **FastAPI + SQLAlchemy 2.x + Jinja2**, 3 loại QR, mặc định SQLite (đổi `DATABASE_URL` sang PostgreSQL khi triển khai).

## Chạy

```powershell
.\run.ps1                  # port 8003 + ngrok
.\run.ps1 -NoNgrok         # chỉ local
.\run.ps1 -Port 8004       # đổi port
```

Tự tạo `demo/.venv`, cài requirements, chạy `alembic upgrade head` rồi khởi động app.
Mặc định: `http://localhost:8003` · **admin / lygialuan12345**

Chạy tay:

```powershell
cd demo
python -m venv .venv; .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
alembic upgrade head
python app.py --create-user --username admin --role admin   # mật khẩu ≥ 12 ký tự
python app.py                                                # thêm --ngrok nếu quét bằng điện thoại
```

Staff cho một event: `python app.py --create-user --username gate1 --role staff --event-id 1` (hoặc `--all-events`).

Test: `cd demo; python -m pytest -q` → 18 passed.

## Ba loại QR

| Loại             | Trang                     | Hành vi                                       |
| ---------------- | ------------------------- | --------------------------------------------- |
| `PRODUCT`        | `/p/{public_id}`          | Tra cứu công khai, quét lặp lại vô hạn        |
| `DIGITAL_TICKET` | `/v/{public_id}?s={hmac}` | Nhập email + SĐT, staff xác nhận đúng một lần |
| `EVENT_CHECKIN`  | `/e/{public_id}`          | Tự điểm danh bằng mã sinh viên + kiểm tra GPS |

QR được gắn thiết bị bằng `HMAC-SHA256(secret, token|deviceId)` (16 hex); sai thiết bị trả `403`.

## Cấu trúc

```
demo/
  app.py            app factory, config, middleware, CLI, ngrok
  models.py         7 bảng SQLAlchemy
  security.py       Argon2id, session, CSRF, rate limit, audit, HMAC
  utils.py          validate input, thời gian, GPS, render QR PNG
  routes.py         endpoint — public / staff / admin
  migrations/       6 migration Alembic
  templates/ static/ tests/
docs/presentation.pptx   slide seminar
run.ps1                  chạy một lệnh
```

## Endpoint chính

| Method   | Endpoint                                          | Quyền                   |
| -------- | ------------------------------------------------- | ----------------------- |
| `POST`   | `/api/login` · `/api/logout`                      | công khai               |
| `POST`   | `/api/qr`                                         | admin + CSRF            |
| `POST`   | `/api/events` · `PATCH /api/events/{id}/settings` | admin + CSRF            |
| `POST`   | `/api/check-ins`                                  | staff/admin + CSRF      |
| `POST`   | `/api/event-self-check-ins`                       | công khai (mã SV + GPS) |
| `POST`   | `/api/tickets/public/{id}/contact`                | công khai, lưu một lần  |
| `GET`    | `/api/stats` · `/api/tickets` · `/api/audit`      | admin                   |
| `DELETE` | `/api/check-ins/history`                          | admin                   |

## Cấu hình

| Biến                  | Ý nghĩa                                          |
| --------------------- | ------------------------------------------------ |
| `SESSION_SECRET`      | Khóa ký session, production cần ≥ 32 ký tự       |
| `DATABASE_URL`        | `postgresql+psycopg://...` hoặc SQLite           |
| `APP_ENV`             | `production` bật kiểm tra secret, HSTS, tắt docs |
| `SESSION_HTTPS_ONLY`  | Đặt `1` khi chạy sau HTTPS                       |
| `TRUSTED_HOSTS`       | Danh sách host, phân cách bằng dấu phẩy          |
| `QR_PUBLIC_BASE_URL`  | Origin gắn vào QR; production phải là HTTPS      |
| `QR_VALIDITY_SECONDS` | Thời hạn vé                                      |

Bảo mật chi tiết: [SECURITY.md](../SECURITY.md).
