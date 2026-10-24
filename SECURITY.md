# Bảo mật Secure QR Attendance

## Threat model

Tài sản cần bảo vệ gồm quyền phát hành/thu hồi vé, trạng thái check-in, thông tin sự kiện, tài khoản staff/admin và nhật ký. Đối thủ có thể đoán token, chụp/sao chép QR, gửi lại request, gọi API trực tiếp, thử mật khẩu, hoặc khai thác thiết lập host/cookie sai. Staff nội bộ có thể chỉ xử lý event được gán; admin có quyền quản trị rộng.

## Các lớp bảo vệ

- **Sinh token:** `secrets.token_urlsafe(32)` tạo token ngẫu nhiên cho vé EVENT_CHECKIN cá nhân; DB chỉ lưu digest. QR chung điểm danh sự kiện dùng public ID riêng trong URL `/e/{id}`; người tham dự nhập mã sinh viên rồi xác nhận, không đưa token vé vào URL.
- **Hashing:** SHA-256 lưu digest token để lookup; entropy cao của token làm phù hợp hash nhanh. Mật khẩu người dùng dùng Argon2id, không SHA-256.
- **Mã hóa:** QR mặc định chứa opaque token, không bọc AES thừa. Codec tùy chọn QRA1 dùng AES-256-GCM, nonce ngẫu nhiên 12 byte, tag 16 byte, AAD gắn version, mục đích và event, `kid` hỗ trợ chọn key khi xoay vòng. AES-GCM xác thực nội dung nhưng không ngăn sao chép/replay QR; trạng thái DB dùng một lần mới là lớp replay control.
- **Xác thực/phân quyền:** session cookie ký, HttpOnly, SameSite=Lax; Secure bật qua `SESSION_HTTPS_ONLY`. Admin quản lý vé/event/audit; staff check-in event được gán. Mutation yêu cầu CSRF token.
- **Check-in:** conditional UPDATE trong DB transaction chỉ đổi `ISSUED` thành `USED` nếu vé chưa dùng/thu hồi và chưa hết hạn; checkin log + audit ghi cùng transaction.
- **Loại QR:** PRODUCT lookup không yêu cầu tài khoản và không tiêu thụ QR. DIGITAL_TICKET cho người sở hữu vé gửi email/số điện thoại một lần; trang công khai không trả lại dữ liệu liên hệ, admin có quyền xem. Chỉ staff được phân quyền mới xác nhận sử dụng. QR sự kiện tự điểm danh kiểm tra thời gian và, nếu cấu hình, khoảng cách GPS.
- **Transport:** TLS phải được reverse proxy/hosting cấu hình. HSTS production; HTTP localhost chỉ là môi trường phát triển.
- **Hardening:** TrustedHost, body limit, security headers, login/issuance/check-in throttling; production không bật docs mặc định và bắt buộc session secret.

## Giới hạn QR tĩnh

QR tĩnh không chứng minh danh tính người cầm vé và không chống chụp màn hình/copy. Public ID cho trang sản phẩm/tình trạng vé có thể bị chia sẻ; endpoint chỉ trả dữ liệu cần thiết và không cấp quyền check-in. Mỗi vé chỉ được staff xác nhận sử dụng một lần; hai request đồng thời chỉ có một request thắng. Token check-in không nằm trong URL, browser history, application logs, analytics hay DB plaintext; scanner gửi QR trong request body và TLS bảo vệ khi truyền qua mạng.

OTP qua email chưa được triển khai vì dự án chưa cấu hình nhà cung cấp email hoặc quy trình xác minh địa chỉ. Không có khẳng định xác minh danh tính chủ vé.

Email và số điện thoại do người quét tự nhập, chưa được xác minh qua OTP. Dữ liệu liên hệ được lưu cùng vé trong cơ sở dữ liệu và chỉ API admin trả về; cần áp dụng chính sách lưu giữ/xóa dữ liệu của đơn vị tổ chức.

## Quản trị khóa và phiên

- Tạo `SESSION_SECRET` bằng bộ sinh ngẫu nhiên bí mật; không commit hoặc chia sẻ. Xoay khóa session sẽ đăng xuất session hiện tại.
- Nếu bật `QR_AES_KEYS`, lưu key 32 byte trong secret manager, key ID ổn định. Khi xoay, phát hành key ID mới, giữ key cũ chỉ trong giai đoạn cần giải mã payload cũ rồi thu hồi. Mặc định token opaque không cần AES key.
- Argon2id hash mật khẩu bằng salt riêng; đổi/reset mật khẩu nên thực hiện ngoài band bởi quản trị viên.

## Backup và incident response

- Backup PostgreSQL định kỳ, mã hóa backup tại nơi lưu, giới hạn quyền truy cập và diễn tập restore. Giữ backup theo chính sách dữ liệu tổ chức.
- Khi lộ vé: revoke vé chưa dùng, theo dõi audit, phát hành vé mới và thông báo chủ thể liên quan. Vé đã check-in không thể reset qua API.
- Khi lộ session secret: thay secret, dừng/khởi động lại dịch vụ và yêu cầu mọi người đăng nhập lại.
- Khi nghi lộ tài khoản: vô hiệu hóa account/thu hồi quyền event, đặt lại mật khẩu, rà audit log và kiểm tra log reverse proxy để loại bỏ query/path chứa token.
- Khi lộ AES key tùy chọn: dừng phát hành payload với key đó, chuyển key ID mới, đánh giá vé bị ảnh hưởng và thay vé. AES-GCM không thu hồi QR đã bị chụp.

## Rủi ro còn lại

Geofence GPS có thể sai lệch hoặc bị giả lập; mã sinh viên nhập tay chưa được xác minh với roster của trường. Dùng đăng nhập/roster trường nếu cần xác thực danh tính chính thức.

Rate limiter hiện giữ bộ đếm trong process; dùng nhiều worker cần gateway hoặc Redis. HTTPS, backup encryption, secrets manager, giám sát cảnh báo và chính sách account recovery là trách nhiệm triển khai. CDN html5-qrcode cần mạng để tải thư viện camera; cân nhắc self-host bản đã pin/hash trong production.
