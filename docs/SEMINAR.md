# Seminar: QR Code Generation & Scanning
> Chủ đề: **Product QR · Event check-in · Digital tickets**  
> Thời lượng gợi ý: 12–15 phút trình bày + 5 phút demo + Q&A.

## Mục tiêu và thông điệp chính
QR là cách mã hóa một định danh hoặc đường dẫn thành hình ảnh có thể đọc bằng camera. QR không tự xác thực danh tính, không phải cơ chế mã hóa, và cũng không đảm bảo vé là hợp lệ. Giá trị nằm ở hệ thống backend xác minh trạng thái và quyền sử dụng.

### Bố cục slide đề xuất
1. **Tiêu đề và mục tiêu** — QR trong ba luồng sản phẩm, sự kiện và vé số.
2. **Technology Overview** — QR là gì, giải quyết bước nhập liệu/chuyển giao thông tin chậm và dễ sai như thế nào?
3. **Business problem & use cases** — truy xuất sản phẩm, giảm thao tác tại cổng, phân phối vé số.
4. **Architecture** — client/app, QR service, database, scanner, API.
5. **Workflow** — phát hành token → tạo QR → quét → backend xác minh → trả kết quả.
6. **Hands-on demo** — tạo QR sản phẩm, tạo vé sự kiện và quét/giả lập quét, thử quét lặp.
7. **Microservice integration** — demo Python/Flask; kiến trúc production có thể dùng Spring Boot, API, persistence, transaction, bảo mật.
8. **Best practices & limitations** — token opaque, chống phát lại, vận hành offline, riêng tư.
9. **Kết luận và Q&A**.
10. **Tài liệu/source tái sử dụng** — README, mã demo, API và hướng dẫn chạy.

## 1. Technology Overview: giải quyết vấn đề gì?

QR Code là mã vạch hai chiều tiêu chuẩn hóa, lưu dữ liệu dưới dạng ma trận ô đen/trắng. Điện thoại phổ thông có thể đọc nhanh mà không cần máy quét chuyên dụng. So với nhập URL/mã thủ công, QR giảm thao tác và lỗi gõ; so với mã vạch một chiều, QR có thể lưu nhiều dữ liệu hơn và vẫn đọc được khi một phần bị hư hại tùy mức sửa lỗi.

QR chỉ là **vật mang dữ liệu**. Một ảnh QR có thể bị chụp màn hình, sao chép hoặc giả mạo. Với giao dịch cần tin cậy, QR nên chứa token ngẫu nhiên hoặc URL có token; backend mới là nơi quyết định token còn hiệu lực, thuộc đúng sự kiện/sản phẩm và đã sử dụng hay chưa.

### 2.1. Vấn đề kinh doanh
- **Product QR:** giảm nhập mã SKU, mở trang hướng dẫn/bảo hành/truy xuất nguồn gốc; giúp khách hàng và nhân viên truy cập đúng thông tin sản phẩm. Cần kiểm soát nội dung để QR giả không dẫn tới trang độc hại.
- **Event check-in:** giảm thời gian xếp hàng và thao tác tra danh sách; hỗ trợ thống kê số người vào. Backend phải từ chối vé giả, hết hạn hoặc đã check-in.
- **Digital tickets:** phát hành/phân phối vé điện tử thuận tiện, tránh in ấn và cho phép thu hồi/cập nhật trạng thái. QR tĩnh có thể bị chia sẻ; nên cấp token không đoán được và quản lý việc sử dụng tại server.

## 2. Architecture & Workflow
### Kiến trúc tham khảo
```text
Organizer / Product admin
          | REST: issue QR
          v
  Python/Flask QR API -----> Database
          |                       | token, type, reference,
          | QR PNG/SVG            | expiry, status, usedAt
          v                       |
  Email / Web / Mobile            |
                                  |
Scanner app -- REST: validate/check-in --> QR service
                   <-- valid / used / expired
```

### Luồng cấp mã
1. Người có quyền yêu cầu tạo QR cho `PRODUCT`, `EVENT_TICKET` hoặc `DIGITAL_TICKET`.
2. Backend kiểm tra quyền, đối tượng tham chiếu và trạng thái; tạo token ngẫu nhiên không mang thông tin nhạy cảm.
3. Lưu token (tốt hơn là lưu hash token), loại, đối tượng, hạn dùng và trạng thái vào DB.
4. Mã hóa URL/định danh thành QR PNG/SVG và trả về cho client gửi email, in hoặc hiển thị.

### Luồng quét và check-in
1. Scanner đọc URL/token; app gọi API backend qua HTTPS.
2. Backend xác thực scanner, tìm token, kiểm tra sự kiện, hạn dùng, trạng thái và quyền sử dụng.
3. Với check-in, cập nhật trạng thái nguyên tử trong transaction. Chỉ một yêu cầu đồng thời được thành công; lần sau trả `409 Already used`.
4. Ghi audit event và trả kết quả dễ hiểu cho nhân viên (hợp lệ/đã dùng/hết hạn/không tồn tại).

### Khác biệt theo tình huống
| Trường hợp | QR thường trỏ tới | Hành động sau khi quét |
|---|---|---|
| Product QR | URL sản phẩm hoặc token tra cứu | Hiển thị thông tin công khai, hướng dẫn, bảo hành |
| Event check-in | Token vé gắn với một sự kiện | Xác minh và tiêu thụ vé đúng một lần |
| Digital ticket | Token vé có hạn và trạng thái | Xác minh vé, cho phép vào cửa; hỗ trợ thu hồi |

## 3. Hands-on Demonstration
### Chuẩn bị
- Python 3.10+; từ PowerShell chạy `cd demo`, `py -m venv .venv`, `.\\.venv\\Scripts\\Activate.ps1`, `python -m pip install -r requirements.txt`, `python app.py`.
- Demo lắng nghe tại `http://localhost:8080`; API và lệnh gọi được mô tả trong README.

### Kịch bản demo (3–5 phút)

1. **Product QR:** gọi `POST /api/qr` với `type=PRODUCT`, `referenceId=SKU-1001`, `label=Bình giữ nhiệt`. Lưu ảnh PNG trả về; quét bằng camera/ứng dụng QR để thấy URL resolve. Gọi `GET /api/qr/resolve/{token}` để tra cứu.
2. **Digital ticket:** tạo QR với `type=DIGITAL_TICKET`, `referenceId=TICKET-2026-0002`, giải thích token không chứa dữ liệu cá nhân và có thể gửi trong vé điện tử.
3. **Event check-in:** tạo QR `EVENT_TICKET`, dùng token gọi `POST /api/check-ins`. Kết quả lần đầu thành công; gọi lại cùng token để minh họa từ chối dùng lặp.
4. **Thảo luận:** thử token sai/hết hạn (nếu demo hỗ trợ); chỉ ra phản hồi và trạng thái phía server.

### Lời dẫn ngắn
“Camera chỉ đọc được một chuỗi từ QR. Nó chưa biết vé thật hay giả. Ứng dụng gửi token về backend; backend đối chiếu DB và cập nhật check-in nguyên tử. Vì vậy chụp lại ảnh không tạo thêm quyền vào cửa: lượt dùng tiếp theo bị từ chối.”

### Dự phòng khi mạng/demo lỗi
Chuẩn bị trước ảnh QR, token và ảnh chụp phản hồi thành công/đã dùng. Có thể minh họa API bằng lệnh PowerShell trong README. Nêu rõ demo hiện lưu bộ nhớ trong nếu chưa kết nối DB nên restart sẽ xóa dữ liệu.

## 4. Integration: Python demo và Spring Boot microservice
### Thành phần
- Python demo dùng Flask route/controller để phát hành QR, resolve token, validate/check-in. Trong Spring Boot tương ứng là REST Controller; seminar demo thực tế dùng Python/Flask.
- Service layer: quy tắc nghiệp vụ và transaction, không đặt logic trong route/controller.
- Repository + database: PostgreSQL/MySQL; unique index cho token/vé và trạng thái.
- Python dùng `qrcode[pil]` để tạo PNG; Spring Boot có thể dùng ZXing. Scanner phía client có thể dùng camera API hoặc thư viện như ML Kit; có thể dùng ứng dụng camera phổ thông để demo.
- Các service liên quan: Event/Ticket service, Identity/Auth, Notification/email; giao tiếp REST hoặc message broker tùy nhu cầu.

### API gợi ý
```text
POST /api/qr                    cấp QR
GET  /api/qr/resolve/{token}    tra cứu thông tin công khai
POST /api/tickets/{id}/check-in xác minh và tiêu thụ vé
POST /api/tickets/{id}/revoke   thu hồi vé (có phân quyền)
```

Ví dụ request cấp QR:

```json
{
  "type": "EVENT_TICKET",
  "referenceId": "TICKET-2026-0001",
  "label": "Seminar công nghệ"
}
```

Response production nên trả `ticketId`, `expiresAt` và URL/ảnh QR; tránh trả dữ liệu nội bộ. Với API check-in, dùng mã HTTP nhất quán: `200` thành công, `404` không tồn tại, `410` hết hạn/đã thu hồi, `409` đã sử dụng.

### Gắn vào microservice
1. **Python demo:** cài `Flask` và `qrcode[pil]`; với Spring Boot thêm ZXing; cấu hình giới hạn kích thước/định dạng ảnh.
2. Xác thực request/DTO; giới hạn loại QR và quyền phát hành (Flask validation hoặc Spring validation).
3. Tạo token bằng CSPRNG (ví dụ 128 bit trở lên), không dùng ID tuần tự.
4. Lưu trạng thái trong DB; tạo migration và unique constraint.
5. Check-in trong transaction với cập nhật có điều kiện (`status=ISSUED` → `USED`) hoặc locking để xử lý quét đồng thời.
6. Bảo vệ API bằng OAuth2/JWT (Spring Security hoặc Flask middleware); phân quyền issuer/scanner; thêm rate limit, audit log, metrics và tracing.
7. Trả QR dưới dạng `image/png` hoặc URL ngắn; cân nhắc object storage/CDN cho lượng lớn.

> Demo hiện là bản khởi đầu minh họa, không đồng nghĩa đã triển khai đầy đủ các phần production trên. Tham khảo `README.md` và `docs/ARCHITECTURE.md` để chạy và xem luồng.

## 5. Best Practices & Limitations
### Nên làm
- QR chứa token opaque/URL ngắn; không nhúng email, số điện thoại, thông tin thanh toán hay bí mật.
- Token đủ ngẫu nhiên, có hạn dùng; lưu hash token nếu phù hợp; có thể thu hồi và xoay khóa.
- Dùng HTTPS, xác thực và phân quyền; kiểm tra đầu vào, rate limit và audit mọi lần sử dụng.
- Cập nhật check-in nguyên tử, có unique constraint/idempotency để tránh race condition và quét lặp.
- Kiểm thử trên nhiều camera, kích thước in, ánh sáng và khoảng cách; chọn error correction phù hợp, giữ vùng quiet zone, tương phản tốt.
- Hiển thị domain đích rõ ràng; hạn chế QR redirect tùy ý để giảm phishing.
- Thiết kế quy trình offline có kiểm soát (danh sách ký số/cache, đồng bộ và xử lý xung đột), không tin vào ảnh QR đơn lẻ.

### Giới hạn/rủi ro
- QR bị sao chép/chụp màn hình; QR tĩnh không chứng minh người cầm là chủ vé.
- Mạng/camera/ánh sáng kém làm quét chậm; lỗi DB/backend có thể gây ùn tắc.
- Với chế độ offline, danh sách thu hồi và chống dùng lặp có thể không đồng bộ.
- QR chỉ là biểu diễn dữ liệu: không mã hóa, không tự xác minh chữ ký và không bảo đảm nội dung đích an toàn.
- Chống chia sẻ tuyệt đối có thể cần vé động, đổi mã theo thời gian, xác minh tài khoản hoặc kiểm tra giấy tờ, tăng chi phí và độ phức tạp.

## 6. Bốn câu hỏi thực tế
### 2.1. Công nghệ giải quyết vấn đề kinh doanh nào?

Giảm nhập liệu thủ công và thời gian tra cứu/check-in; liên kết vật lý (sản phẩm, thẻ/vé) với thông tin/dịch vụ số; hỗ trợ phát hành, theo dõi và xác nhận quyền sử dụng. Lợi ích chỉ đạt được khi backend quản lý đúng trạng thái và quy trình.

### 2.2. Tích hợp vào Python microservice thế nào? (Spring Boot là phương án tương đương)

Demo seminar dùng Python/Flask (`demo/app.py`): route + service logic + encoder `qrcode[pil]`. Đưa nghiệp vụ vào service layer; dùng repository SQLAlchemy/DB-API để lưu token, đối tượng, hạn và trạng thái; bảo vệ API bằng Flask middleware/OAuth2; cập nhật check-in bằng transaction và conditional update để một vé chỉ dùng một lần. Spring Boot có thể dùng REST controller + service + repository + Spring Security với cùng hợp đồng API. Tách QR issuance/validation khỏi domain service khi cần. Kafka có thể phát `TicketCheckedIn` cho analytics/notification, còn quyết định check-in cần đồng bộ và nguyên tử.

### 2.3. Dự án học kỳ nào hưởng lợi?

- E-commerce/inventory: truy xuất sản phẩm, bảo hành, kiểm kho.
- Quản lý hội thảo/sự kiện: đăng ký, cấp vé, check-in, thống kê.
- Rạp chiếu phim, xe buýt, ký túc xá/thư viện: vé, lượt vào, mượn-trả.
- Quản lý tài sản/phòng lab: định danh thiết bị và lịch sử bảo trì.
- Nhà hàng/thực đơn số, thanh toán demo: truy cập tài nguyên qua QR; giao dịch thật cần xác thực bổ sung.

### 2.4. Demo/tài liệu tái sử dụng ở đâu?

Mã Python/Flask nằm trong `demo/app.py`; dependencies ở `demo/requirements.txt`; test ở `demo/tests/`; lệnh chạy và request mẫu ở `README.md`; kiến trúc ở `docs/ARCHITECTURE.md`. Bạn cùng lớp có thể cài Python, chạy `python -m pip install -r requirements.txt` rồi `python app.py`. Trước khi dùng thật cần thay lưu trữ in-memory bằng DB và hoàn thiện xác thực, xử lý đồng thời, test.

## 7. Q&A gợi ý
**QR có an toàn không?** Bản thân QR không an toàn hay nguy hiểm; nó chỉ chứa dữ liệu. Phải kiểm tra URL/token ở backend và không mở URL không rõ nguồn.

**QR bị chụp lại thì sao?** Token vé một lần phải được đánh dấu đã sử dụng nguyên tử; ảnh chụp sau đó bị từ chối. Với vé động, đổi token theo chu kỳ và kiểm tra qua backend.

**Nếu mất mạng ở cổng?** Cần quy trình offline được thiết kế trước, chẳng hạn danh sách vé ký số/cache có thời hạn và đồng bộ xung đột. Offline không thể bảo đảm chống dùng trùng toàn cục nếu nhiều cổng không liên lạc với nhau.

**Sao không nhúng toàn bộ vé vào QR?** Dữ liệu có thể bị lộ/sửa và khó thu hồi. Token tham chiếu cho phép server kiểm tra trạng thái và giới hạn thông tin tiết lộ.

**QR khác barcode thường?** QR là mã 2D, thường lưu được nhiều dữ liệu hơn và đọc từ nhiều hướng; barcode 1D đơn giản hơn, đôi lúc phù hợp hơn với dây chuyền scanner sẵn có.

## Kết luận
QR giúp nối thế giới vật lý và dịch vụ số với thao tác quét đơn giản. Với Product QR, ưu tiên nội dung chính xác và an toàn; với vé/check-in, ưu tiên token khó đoán, backend xác minh và tiêu thụ một lần. QR là giao diện tiện lợi—hệ thống phía sau mới là nguồn tin cậy.
