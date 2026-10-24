# Secure QR Attendance

Ứng dụng FastAPI/Jinja2 có ba loại QR riêng: PRODUCT mở trang tra cứu công khai, DIGITAL_TICKET mở trang kiểm tra trạng thái chỉ đọc và được staff xác nhận sử dụng tại cổng, EVENT_CHECKIN check-in một lần. Dữ liệu lưu bằng SQLAlchemy 2.x; PostgreSQL dùng cho triển khai, SQLite tiện cho phát triển/test.

## Chạy local trên Windows

```powershell
cd demo
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:SESSION_SECRET = (python -c "import secrets; print(secrets.token_urlsafe(48))")
$env:APP_ENV = "development"
alembic upgrade head
python app.py --create-user --username admin --role admin
python app.py
```

Mật khẩu được nhập ẩn bằng prompt và phải dài ít nhất 12 ký tự. Mở `http://localhost:8000/login`. Tạo event trong dashboard, ghi lại ID trả về từ API response/DB, rồi tạo staff được gán event:

```powershell
python app.py --create-user --username gate1 --role staff --event-id 1
```

Scanner camera chạy trên localhost hoặc origin HTTPS. Trên điện thoại, triển khai phía sau reverse proxy có TLS và đặt `SESSION_HTTPS_ONLY=1`; HTTP local không mã hóa lưu lượng.

## PostgreSQL bằng Docker Compose

Sao chép `demo/.env.example` thành `demo/.env`, thay tất cả giá trị mẫu bằng secrets riêng, sau đó:

```powershell
cd demo
docker compose up --build -d
docker compose exec web python app.py --create-user --username admin --role admin
```

Compose chỉ bind ứng dụng vào localhost. Đặt reverse proxy TLS trước nó, rồi cấu hình `TRUSTED_HOSTS` và giữ `SESSION_HTTPS_ONLY=1`. Không commit `.env`.

## Luồng sử dụng

1. Admin đăng nhập, tạo event và phát hành các loại QR. Chỉ admin được phát hành.
2. PRODUCT QR mở `/p/{public_id}`; ai cũng tra cứu thông tin sản phẩm nhiều lần. DIGITAL_TICKET QR mở `/v/{public_id}` để người tham dự nhập email/số điện thoại; thông tin được lưu cùng vé và admin xem trong danh sách. Trang công khai chỉ hiện trạng thái, không lộ thông tin liên hệ.
3. Admin cấu hình tâm GPS, bán kính 20–2000 m và khung giờ điểm danh cho event. QR chung `/e/{public_id}` cho phép mỗi người nhập mã sinh viên, xác nhận và bật GPS; server kiểm tra thời gian, khoảng cách và chỉ nhận mỗi mã một lần. Tọa độ người dùng không được lưu.
4. Mỗi sự kiện dùng một QR chung cho nhiều người tự điểm danh. DIGITAL_TICKET riêng là tùy chọn cho sự kiện cần phát hành vé để nhân viên quét tại cổng.
5. Staff đăng nhập `/login`, mở `/scanner`, chọn event và quét DIGITAL_TICKET hoặc vé EVENT_CHECKIN để ghi nhận tại cổng; check-in chỉ thành công một lần.
6. Admin có thể xóa QR khỏi danh sách; QR chưa dùng sẽ bị vô hiệu hóa, còn lịch sử check-in được giữ lại.
7. Admin có thể dùng nút **Xóa lịch sử** để xóa toàn bộ lượt check-in vé và tự điểm danh khỏi thống kê/lịch sử; thao tác này có xác nhận và không thể hoàn tác.
6. Admin có thể thu hồi vé chưa sử dụng, xem danh sách, thống kê, lịch sử check-in và audit.

Không có reset check-in: reset sẽ vô hiệu hóa bảo đảm dùng một lần. API `/t/{token}` cũ và API resolve/image mang token trên URL trả 410. PRODUCT lookup chuyển sang `POST /api/products/lookup`.

## Migration API

- `POST /api/qr` cần admin session + CSRF; ticket cần `eventId`. Response có `publicUrl` cho PRODUCT/DIGITAL_TICKET; token EVENT_CHECKIN cá nhân chỉ trả cho admin một lần để đưa vào QR scanner.
- `GET /e/{public_id}` mở trang xác nhận điểm danh chung cho event. `POST /api/event-self-check-ins` nhận event ID công khai, mã sinh viên và GPS trong JSON body; không lưu tọa độ người quét.
- `POST /api/events` và `PATCH /api/events/{id}/settings` cấu hình vị trí/bán kính, giờ bắt đầu và kết thúc; cần admin session + CSRF.
- `GET /p/{public_id}` và `GET /api/products/{public_id}` cho phép khách tra cứu PRODUCT công khai, lặp lại.
- `GET /v/{public_id}` cho người tham dự xem trạng thái và nhập thông tin liên hệ; `POST /api/tickets/public/{public_id}/contact` lưu email/số điện thoại một lần. `GET /api/tickets/public/{public_id}` chỉ trả trạng thái; không check-in.
- `POST /api/check-ins` cần staff/admin session + CSRF. Body `{ "token": "..." }` nhận token EVENT_CHECKIN cá nhân hoặc URL `/v/{public_id}` để staff xác nhận vé điện tử.
- `DELETE /api/check-ins/history` chỉ admin; xóa lượt check-in và bản ghi tự điểm danh, giữ audit log về thao tác xóa.
- `/api/stats`, `/api/tickets`, `/api/check-ins` (GET), `/api/audit`, tạo event và thu hồi vé cần admin.
- `GET /api/qr/resolve/{token}` và `/api/qr/image/{token}` trả 410 vì token trong URL có thể rò qua access log/history. Dùng `POST /api/products/lookup` cho PRODUCT.
- `POST /api/reset` trả 410. `/t/{token}` trả 410.

Schema mới được tạo bằng `alembic upgrade head`; migration không tự nhập credential từ app RAM cũ. Downgrade phá hủy bị vô hiệu hóa. Không có dữ liệu cũ bền vững để migrate.

## Cấu hình chính

| Biến | Mục đích |
|---|---|
| `DATABASE_URL` | PostgreSQL `postgresql+psycopg://...` hoặc SQLite local |
| `SESSION_SECRET` | Khóa ký session; production cần ít nhất 32 ký tự ngẫu nhiên |
| `SESSION_HTTPS_ONLY` | Đặt `1` khi dùng HTTPS |
| `APP_ENV` | `production` bật kiểm tra secret, HSTS và tắt docs |
| `TRUSTED_HOSTS` | Host allowlist phân cách bằng dấu phẩy |
| `QR_VALIDITY_SECONDS` | Thời hạn vé |
| `QR_PUBLIC_BASE_URL` | Origin gắn vào PRODUCT/DIGITAL_TICKET và QR điểm danh event; production phải là HTTPS |
| `QR_AES_KEYS` | Tùy chọn JSON map `kid` tới base64 của key AES 32 byte; không cần khi dùng opaque token mặc định |

Test:

```powershell
cd demo
python -m pytest -q
```

Tham khảo [SECURITY.md](../SECURITY.md), [SECURITY_PLAN.md](../SECURITY_PLAN.md) và [DEMO_SCRIPT.md](../DEMO_SCRIPT.md).
