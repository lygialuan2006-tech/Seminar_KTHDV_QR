# QR Code Generation & Scanning

Demo seminar: tạo QR, tra cứu token và check-in vé một lần.
Ba loại QR: `PRODUCT`, `EVENT_CHECKIN`, `DIGITAL_TICKET`.

Stack: Python 3.10+, FastAPI + Jinja2, `qrcode[pil]`, `pyngrok`, ngrok.

## Chạy

```powershell
cd demo
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Server: `http://localhost:8000` · Swagger: `http://localhost:8000/docs`.

Hoặc chạy nhanh từ thư mục gốc:

```powershell
.\run.ps1
```

Nếu PowerShell chặn script: `powershell -ExecutionPolicy Bypass -File .\run.ps1`.

Mở `http://localhost:8000` để dùng giao diện web: tạo QR, check-in và xem bảng check-in ngay trên trang chủ.

- `/` — tạo QR `PRODUCT`, `EVENT_CHECKIN`, `DIGITAL_TICKET` + bảng check-in trực tiếp (người mới nhất lên đầu); nhấn vào ảnh QR để tải
- `/t/{token}` — trang thông tin sản phẩm/vé, có nút Check-in với vé
- `/static/styles.css` — CSS dùng chung cho mọi trang

`PRODUCT` có thêm `batch`, `info`; `DIGITAL_TICKET` có thêm `holder`, `seat`.

Reset lịch sử check-in:

- Đổi "Loại QR" → ẩn QR cũ và gọi `/api/reset?type=<loại trước đó>` (chỉ xoá lịch sử của loại đó).
- Mở tab mới → reset toàn bộ (lưu cờ trong `sessionStorage`).
- F5 trên cùng tab → **không** reset, giữ nguyên số người đã check-in.

App tự mở ngrok và in `Public URL`. URL này được nhúng vào QR để điện thoại quét được. Tắt ngrok bằng `$env:USE_NGROK="0"`.

## Thử nhanh

Tạo vé sự kiện:

```powershell
$ticket = Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/qr `
  -ContentType 'application/json' `
  -Body '{"type":"EVENT_CHECKIN","referenceId":"TICKET-2026-0001","label":"Seminar cong nghe"}'
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

| Method | Path                      | Mô tả                                                                |
| ------ | ------------------------- | -------------------------------------------------------------------- |
| `POST` | `/api/qr`                 | Tạo token + ảnh QR PNG (Base64)                                      |
| `GET`  | `/api/qr/resolve/{token}` | Tra cứu token                                                        |
| `GET`  | `/api/qr/image/{token}`   | Tải ảnh QR                                                           |
| `POST` | `/api/check-ins`          | Check-in vé một lần                                                  |
| `GET`  | `/api/stats`              | Số người đã check-in + danh sách                                     |
| `POST` | `/api/reset`              | Xoá lịch sử check-in; thêm `?type=EVENT_CHECKIN` để chỉ xoá một loại |
| `GET`  | `/health`                 | Kiểm tra server                                                      |

Body tạo QR:

```json
{
  "type": "EVENT_CHECKIN",
  "referenceId": "TICKET-2026-0001",
  "label": "Seminar công nghệ"
}
```

## Cấu hình

| Biến                  | Mặc định                | Ý nghĩa                  |
| --------------------- | ----------------------- | ------------------------ |
| `QR_PUBLIC_BASE_URL`  | `http://localhost:8000` | URL gốc nhúng trong QR   |
| `QR_VALIDITY_SECONDS` | `86400`                 | Hạn dùng token           |
| `QR_SIZE`             | `8`                     | Kích thước mỗi ô QR      |
| `QR_BORDER`           | `4`                     | Số ô viền trắng quanh QR |
| `PORT`                | `8000`                  | Cổng server              |
| `USE_NGROK`           | `1`                     | `0` để tắt ngrok         |

## Test

```powershell
cd demo
python -m pytest
```
