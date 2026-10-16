# QR Demo — Architecture & API
## Mục đích và phạm vi
Demo minh họa tạo QR, resolve token và check-in vé sự kiện bằng Python 3.10+, Flask và `qrcode[pil]`. Đây là skeleton học tập, không phải hệ thống bán vé production; dữ liệu in-memory sẽ mất khi ứng dụng restart.

## Architecture
```text
Client / Admin
    | POST /api/qr
    v
Python Flask API -> QR service / token registry -> in-memory store (demo)
    |                                      ^
    +-- PNG Base64                           | resolve / check-in
                                             |
Scanner / PowerShell -> POST /api/check-ins -+
```

Ở production, thay store bằng database và thêm auth, transaction, audit log, rate limiting, observability, migrations và chính sách lưu trữ.

## Workflow
### Phát hành
1. Client gửi `type`, `referenceId`, `label`.
2. API kiểm tra request và cấp token.
3. Service mã hóa URL resolve thành PNG, trả token cùng ảnh Base64.
4. Client hiển thị hoặc gửi ảnh QR cho người dùng.

### Resolve
Scanner đọc token/URL và gọi `GET /api/qr/resolve/{token}`. Service tra cứu loại QR, mã tham chiếu và nhãn (chỉ trả trường được phép công khai).

### Check-in
Scanner gọi `POST /api/check-ins` với token vé. Token phải là vé sự kiện hợp lệ và chưa sử dụng. Lần check-in kế tiếp phải bị từ chối. Demo minh họa trạng thái; triển khai production cần đảm bảo cập nhật nguyên tử ở DB để hai scanner đồng thời không cùng chấp nhận.

## API demo
### `POST /api/qr`
Request:
```json
{"type":"PRODUCT","referenceId":"SKU-1001","label":"Bình giữ nhiệt"}
```
Kiểu hợp lệ trong seminar: `PRODUCT`, `EVENT_TICKET`, `DIGITAL_TICKET`. Response gồm token và ảnh QR PNG dạng Base64 (cấu trúc JSON chính xác theo source code demo).

### `GET /api/qr/resolve/{token}`
Trả thông tin tra cứu gắn với token nếu token tồn tại.

### `POST /api/check-ins`
Request:
```json
{"token":"TOKEN_NHAN_DUOC"}
```
Chấp nhận vé sự kiện hợp lệ chưa được dùng; từ chối token sai hoặc lần sử dụng trùng.

## Chạy thử PowerShell
Yêu cầu Python 3.10+. Từ thư mục gốc dự án:
```powershell
cd demo
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```
Từ terminal khác:
```powershell
$qr = Invoke-RestMethod -Method Post -Uri http://localhost:8080/api/qr -ContentType 'application/json' -Body '{"type":"EVENT_TICKET","referenceId":"TICKET-2026-0001","label":"Seminar cong nghe"}'
$qr | ConvertTo-Json -Depth 5
```
Dùng giá trị `token` trả về:
```powershell
$token = $qr.token
Invoke-RestMethod -Uri "http://localhost:8080/api/qr/resolve/$token"
Invoke-RestMethod -Method Post -Uri http://localhost:8080/api/check-ins -ContentType 'application/json' -Body (@{token=$token} | ConvertTo-Json)
Invoke-RestMethod -Uri $qr.imageUrl -OutFile ticket.png
```
Gọi check-in lần nữa để quan sát từ chối quét trùng. Mở `ticket.png` hoặc quét ảnh trên màn hình bằng camera.

## Mô hình dữ liệu production gợi ý
`qr_credential(id, token_hash UNIQUE, type, reference_id, status, expires_at, used_at, created_at)`.
- Token được tạo bằng CSPRNG, ít nhất 128 bit entropy; lưu hash nếu cần giảm rủi ro khi DB bị lộ.
- Check-in: `UPDATE ... SET status='USED', used_at=... WHERE token_hash=? AND status='ISSUED' AND expires_at > now()`; chỉ coi là thành công nếu đúng một dòng được cập nhật.
- Tạo index cho token hash, sự kiện và trạng thái; không dùng `reference_id` tuần tự làm bí mật.
- Lưu audit riêng: scanner/user, thời điểm, kết quả và request id; không ghi token nguyên văn vào log.

## Tích hợp dịch vụ
- **Event/Ticket service:** sở hữu quy tắc sự kiện, phát hành/thu hồi vé.
- **QR service:** encode/decode hình ảnh và ánh xạ token tới đối tượng; có thể là module trong cùng service ở dự án nhỏ.
- **Identity service:** OAuth2/JWT, role issuer/scanner/admin.
- **Notification service:** gửi vé qua email/SMS/push; không gửi thông tin nhạy cảm trong URL.
- **Analytics:** tiêu thụ event `TicketCheckedIn` sau khi DB xác nhận thành công.

## Giới hạn & checklist production
- HTTPS; authentication/authorization và kiểm tra quyền truy cập sự kiện.
- Token ngẫu nhiên, hạn dùng, thu hồi, chống replay và cập nhật atomic/transactional.
- Idempotency, rate limiting, input validation, audit, metrics, tracing, alert.
- Chọn payload URL ngắn; không nhúng PII hoặc bí mật. Hiển thị domain đáng tin cậy.
- Kế hoạch offline, đồng bộ xung đột, bảo vệ scanner/device và quy trình hỗ trợ thủ công.
- Kiểm thử race condition, tải cao, camera/thiết bị thật, in ấn, ánh sáng và QR lỗi.
- Demo Python in-memory chỉ phù hợp trình bày; dữ liệu mất khi restart và không chia sẻ giữa nhiều worker. Cần database/transaction để đảm bảo an toàn production.
