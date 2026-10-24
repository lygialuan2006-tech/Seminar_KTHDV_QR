# Kế hoạch nâng cấp Secure QR Attendance

## Hiện trạng đã khảo sát

- Ứng dụng nằm trong `demo/app.py`, dùng FastAPI/Jinja2 và QR PNG.
- `POST /api/qr`, resolve, ảnh QR, `/t/{token}`, check-in, stats và reset đều đang tồn tại.
- Credential, token plaintext, PNG và used_at nằm trong dictionary của process; restart sẽ mất dữ liệu và nhiều worker không chia sẻ trạng thái.
- API phát hành, check-in, stats và reset chưa xác thực. Reset có thể mở lại vé đã sử dụng.
- QR hiện mã hóa URL có token; URL, access log và lịch sử trình duyệt có thể làm lộ token. Trang vé tự check-in không qua scanner đăng nhập.
- PRODUCT, EVENT_CHECKIN và DIGITAL_TICKET đang dùng chung registry; check-in PRODUCT bị chặn.
- Không có schema migration hay cấu hình triển khai an toàn.

## Rủi ro và kế hoạch migration

1. Chuyển dữ liệu runtime sang SQLAlchemy 2.x, PostgreSQL khi triển khai và SQLite chỉ cho test/local. Tạo schema mới bằng Alembic; không tự động xóa/di trú dữ liệu cũ vì dữ liệu hiện tại chỉ sống trong bộ nhớ.
2. Tạo bảng staff_users, events, event_staff, tickets, checkin_logs và audit_logs với khóa ngoại, unique/index và UTC timestamps. Chỉ lưu SHA-256 của token ngẫu nhiên 256-bit; token chỉ trả một lần lúc phát hành.
3. Bắt buộc admin/staff đăng nhập bằng Argon2id và session cookie HttpOnly/SameSite. Thêm CSRF token cho mutation, RBAC/event scope và throttle đăng nhập. Tài khoản admin được tạo qua CLI.
4. Check-in thực hiện conditional UPDATE trong transaction để một vé chỉ dùng một lần qua nhiều request/worker. Ghi nhật ký không chứa token.
5. Tách ba use case. PRODUCT dùng public ID ngẫu nhiên trong URL `/p/{id}`, cho phép tra cứu lặp lại, không hết hạn/check-in. DIGITAL_TICKET dùng `/v/{id}` để tra cứu trạng thái chỉ đọc; staff mới xác nhận sử dụng. EVENT_CHECKIN giữ token opaque và check-in một lần. Scanner gửi QR trong HTTPS request body; `/t/{token}` và API legacy chứa token trong path trả 410.
6. Thêm hardening headers, host/CORS allowlist, request body limit, giới hạn tần suất, cấu hình env mẫu, Compose, tài liệu threat model và demo.
7. AES-GCM không bật mặc định vì opaque random token đủ dùng và mã hóa token lần hai không tạo lợi ích rõ. Có codec QRA1 tùy chọn cho payload nhạy cảm; nó không chống sao chép/replay. QR tĩnh chỉ được dùng một lần nhờ trạng thái DB.

## Giới hạn

- Thay đổi API có chủ đích: issuance cần admin session + CSRF + eventId; check-in cần staff session + CSRF; stats/reset cần admin. Reset không còn mở lại vé. QR phát hành mới là raw token, không phải URL.
- Vé cũ không thể tự migrate an toàn: chúng chỉ tồn tại trong RAM, không có dữ liệu lưu trữ đáng tin cậy để chuyển.
- Rate limit demo nằm trong process; triển khai nhiều worker cần reverse proxy/API gateway hoặc Redis để giới hạn dùng chung.
- HTTPS phải được cấu hình tại reverse proxy/host; HTTP localhost không được xem là mã hóa.
- PRODUCT/DIGITAL_TICKET dùng `public_id` mới và migration 0002. QR PRODUCT/Vé đã in theo định dạng cũ cần phát hành/in lại; token cũ không thể khôi phục từ hash để tạo lại QR công khai.
