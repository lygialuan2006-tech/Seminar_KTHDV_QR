# QR Code Generation & Scanning

Demo seminar: tạo QR, tra cứu token và check-in vé một lần.
Ba loại QR: `PRODUCT`, `EVENT_TICKET`, `DIGITAL_TICKET`.

Stack: Python 3.10+, FastAPI, `qrcode[pil]`, ngrok.

## Chạy

```powershell
cd demo
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Server: `http://localhost:8000` · Swagger: `http://localhost:8000/docs`.

App tự mở ngrok và in `Public URL`. URL này được nhúng vào QR để điện thoại quét được. Tắt ngrok bằng `$env:USE_NGROK="0"`.

## Thử nhanh

Tạo vé sự kiện:

```powershell
$ticket = Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/qr `
  -ContentType 'application/json' `
  -Body '{"type":"EVENT_TICKET","referenceId":"TICKET-2026-0001","label":"Seminar cong nghe"}'
$ticket | ConvertTo-Json -Depth 5
```

Check-in hai lần:

```powershell
$body = @{token=$ticket.token} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/check-ins -ContentType 'application/json' -Body $body
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/check-ins -ContentType 'application/json' -Body $body
```

Lần đầu trả `200`, lần hai trả `409`.

## API

| Method | Path                      | Mô tả                           |
| ------ | ------------------------- | ------------------------------- |
| `POST` | `/api/qr`                 | Tạo token + ảnh QR PNG (Base64) |
| `GET`  | `/api/qr/resolve/{token}` | Tra cứu token                   |
| `GET`  | `/api/qr/image/{token}`   | Tải ảnh QR                      |
| `POST` | `/api/check-ins`          | Check-in vé một lần             |
| `GET`  | `/health`                 | Kiểm tra server                 |

Body tạo QR:

```json
{
  "type": "EVENT_TICKET",
  "referenceId": "TICKET-2026-0001",
  "label": "Seminar công nghệ"
}
```

## Cấu hình

| Biến                  | Mặc định                | Ý nghĩa                |
| --------------------- | ----------------------- | ---------------------- |
| `QR_PUBLIC_BASE_URL`  | `http://localhost:8000` | URL gốc nhúng trong QR |
| `QR_VALIDITY_SECONDS` | `86400`                 | Hạn dùng token         |
| `PORT`                | `8000`                  | Cổng server            |
| `USE_NGROK`           | `1`                     | `0` để tắt ngrok       |

## Test

```powershell
cd demo
python -m pytest
```