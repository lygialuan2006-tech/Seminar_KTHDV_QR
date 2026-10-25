# Kịch bản demo Secure QR Attendance

## Chuẩn bị

1. Khởi động PostgreSQL/app, chạy migration, tạo admin CLI.
2. Admin đăng nhập, tạo sự kiện Seminar, đặt tâm GPS, bán kính và khung giờ. Chiếu QR chung sự kiện cho lớp; phát hành thêm QR PRODUCT, DIGITAL_TICKET và vé EVENT_CHECKIN cá nhân nếu cần.
3. Người tham dự quét QR chung, nhập mã sinh viên, bấm xác nhận và cho phép vị trí. Mỗi mã chỉ điểm danh một lần trong thời gian và bán kính đã đặt.
4. Staff có thể mở `/scanner` qua HTTPS, đăng nhập và quét vé EVENT_CHECKIN cá nhân tại cổng.

## Trình bày

1. **Phát hành:** Admin tạo QR. Server tạo token ngẫu nhiên bằng `secrets.token_urlsafe(32)`, lưu SHA-256 digest trong PostgreSQL, chỉ trả token một lần để tạo QR. Đây là hashing, không phải mã hóa token.
2. **PRODUCT:** Quét QR mở trang sản phẩm công khai. Quét lặp lại vẫn xem được và không tạo check-in.
3. **DIGITAL_TICKET:** Quét QR mở trang trạng thái công khai chỉ đọc. Quét trang không tiêu thụ vé; staff xác nhận vé tại cổng bằng scanner.
4. **Xác thực và phân quyền:** Staff đăng nhập; mật khẩu xác minh bằng Argon2id. Staff chỉ check-in event được gán. Admin phát hành/thu hồi vé và xem audit.
5. **Check-in hợp lệ:** Scanner gửi QR trong request body HTTPS. Vé hợp lệ trả 200. DB đổi `ISSUED` thành `USED` và ghi log trong transaction.
6. **Replay:** Quét lần nữa trả 409. Hai request đồng thời chỉ có một request thành công.
7. **Gắn thiết bị:** Copy QR sang thiết bị khác (đổi `deviceId`) trả 403 dù token đúng.
8. **Giả mạo/hết hạn/thu hồi:** QR không tồn tại trả 404; vé hết hạn hoặc bị thu hồi trả 410.
9. **Chưa đăng nhập:** Gửi check-in không session nhận 401. PRODUCT tra cứu vẫn công khai nhưng không thể check-in.
10. **Lưu bền:** Restart app; vé vẫn còn trong DB.

## Giải thích chính xác

- **Mã hóa:** TLS mã hóa truyền tải khi triển khai HTTPS. HTTP local không có TLS. Payload QR không bọc AES; token đã là 256-bit ngẫu nhiên.
- **Hashing:** SHA-256 cho token random entropy cao; Argon2id cho password.
- **Gắn thiết bị:** payload QR chứa `HMAC-SHA256(SESSION_SECRET, token|deviceId)`; server so khớp lại khi quét. Copy QR sang máy khác trả 403.
- **Xác thực:** kiểm tra session staff/admin.
- **Phân quyền:** role + event assignment phía server.
- **Chống replay:** cập nhật trạng thái nguyên tử một lần trong DB.

Không khẳng định QR tĩnh chống chụp màn hình. Người có bản sao có thể dùng trước; cần quy trình kiểm tra phù hợp và thu hồi/phát hành lại khi lộ vé.
