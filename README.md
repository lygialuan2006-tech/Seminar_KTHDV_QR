# QR Code Generation & Scanning — Seminar và demo

Bộ tài liệu này giúp bạn trình bày QR Code Generation & Scanning với ba tình huống: **Product QR**, **Event check-in** và **Digital tickets**. Demo chạy bằng Python 3.10+ với FastAPI và thư viện `qrcode`.

## Nội dung bộ seminar

- `docs/SEMINAR.md` — nội dung trình bày theo cấu trúc yêu cầu, câu trả lời bốn câu hỏi thực tế, kịch bản demo và Q&A.
- `docs/ARCHITECTURE.md` — kiến trúc, luồng nghiệp vụ, API và lưu ý bảo mật.
- `demo/app.py` — REST API FastAPI tạo QR PNG, tra cứu token và xác nhận check-in.
- `demo/requirements.txt`, `demo/tests/` — dependencies và kiểm thử tự động.

## Chạy demo nhanh

Yêu cầu: Python 3.10 trở lên. Từ thư mục gốc dự án, tạo môi trường ảo và cài dependencies:

```powershell
cd demo
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Ứng dụng chạy tại `http://localhost:8080`. Khi khởi động, app tự mở thêm tunnel ngrok và in ra `Public URL`; URL này được dùng làm `QR_PUBLIC_BASE_URL` để điện thoại quét được. Đặt `USE_NGROK=0` nếu không muốn dùng ngrok.

Mở terminal PowerShell thứ hai, tạo QR sản phẩm và xem response JSON (bao gồm token/ảnh Base64 và URL ảnh PNG):

```powershell
$product = Invoke-RestMethod -Method Post -Uri http://localhost:8080/api/qr -ContentType 'application/json' -Body '{"type":"PRODUCT","referenceId":"SKU-1001","label":"Bình giữ nhiệt"}'
$product | ConvertTo-Json -Depth 5
```

Có thể mở `imageUrl` trên trình duyệt để hiển thị/tải ảnh QR hoặc quét ảnh từ màn hình. Dùng token trả về để resolve. Ứng dụng camera trên điện thoại chỉ truy cập được server nếu máy tính/điện thoại cùng mạng và `QR_PUBLIC_BASE_URL` được cấu hình thành địa chỉ mà điện thoại truy cập được.

```powershell
$token = $product.token
Invoke-RestMethod "http://localhost:8080/api/qr/resolve/$token"
```

Tạo vé sự kiện rồi check-in hai lần để minh họa quét trùng:

```powershell
$ticket = Invoke-RestMethod -Method Post -Uri http://localhost:8080/api/qr -ContentType 'application/json' -Body '{"type":"EVENT_TICKET","referenceId":"TICKET-2026-0001","label":"Seminar cong nghe"}'
$ticketToken = $ticket.token
$body = @{token=$ticketToken} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8080/api/check-ins -ContentType 'application/json' -Body $body
Invoke-RestMethod -Method Post -Uri http://localhost:8080/api/check-ins -ContentType 'application/json' -Body $body
```

Có thể tạo `DIGITAL_TICKET` theo cách tương tự. Chạy kiểm thử từ thư mục `demo` bằng `python -m pytest`. Demo hiện dùng bộ nhớ trong nên dữ liệu mất khi restart; không dùng cho vé/sản phẩm production.

## API demo

| Phương thức | Đường dẫn                 | Mô tả                                           |
| ----------- | ------------------------- | ----------------------------------------------- |
| `POST`      | `/api/qr`                 | Tạo token và ảnh QR PNG (Base64); trả HTTP 201  |
| `GET`       | `/api/qr/resolve/{token}` | Tra cứu loại và mã tham chiếu                   |
| `GET`       | `/api/qr/image/{token}`   | Tải ảnh QR PNG                                  |
| `POST`      | `/api/check-ins`          | Check-in vé, từ chối token không hợp lệ/đã dùng |
| `GET`       | `/health`                 | Kiểm tra trạng thái ứng dụng                    |

Body tạo QR mẫu:

```json
{
  "type": "EVENT_TICKET",
  "referenceId": "TICKET-2026-0001",
  "label": "Seminar công nghệ"
}
```

## Tài liệu seminar

- `docs/SEMINAR.md`: dàn ý slide, nội dung trình bày, demo, tích hợp microservice (Spring Boot như ví dụ kiến trúc), bốn câu hỏi thực tế và Q&A.
- `docs/ARCHITECTURE.md`: architecture/workflow, API, mô hình dữ liệu và checklist triển khai.

## Cấu hình demo

Có thể đặt biến môi trường trước khi chạy:

- `QR_PUBLIC_BASE_URL`: địa chỉ gốc được nhúng trong QR (mặc định `http://localhost:8080`; đổi sang IP LAN nếu quét bằng điện thoại).
- `QR_VALIDITY_SECONDS`: thời gian hiệu lực theo giây (mặc định 86400).
- `PORT`: cổng server (mặc định 8080).
- `USE_NGROK`: `1` để tự mở tunnel ngrok, `0` để tắt (mặc định `1`).

## Lưu ý

Demo minh họa luồng ứng dụng, **không phải hệ thống bán vé production**. Trạng thái đang được lưu trong memory nên mất khi restart và không chia sẻ giữa nhiều worker. Dùng HTTPS, DB có unique constraint/transaction, token ngẫu nhiên khó đoán và có hạn dùng, phân quyền scanner, rate limit, audit log, xử lý thu hồi vé và kiểm thử đồng thời trước khi triển khai thật. Không nhúng dữ liệu cá nhân hoặc bí mật trực tiếp vào QR.
