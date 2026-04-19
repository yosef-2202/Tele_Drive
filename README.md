***

```markdown
# ☁️ Telegram Drive - Unlimited Cloud Storage Solution

![Telegram Drive Banner](https://img.shields.io/badge/Telegram-Drive-blue?style=for-the-badge&logo=telegram)
![Python](https://img.shields.io/badge/Python-3.8%2B-blue?style=flat-square&logo=python)
![Flask](https://img.shields.io/badge/Flask-Web%20Framework-black?style=flat-square&logo=flask)
![Security](https://img.shields.io/badge/Encryption-AES--256-green?style=flat-square)

**Telegram Drive** là một hệ thống lưu trữ đám mây tự lưu trữ (self-hosted), sử dụng API của Telegram làm nơi lưu trữ dữ liệu không giới hạn. Hệ thống kết hợp giao diện Web hiện đại mang phong cách Google Drive và một Telegram Bot thông minh để quản lý từ xa.

---

## ✨ Tính năng nổi bật

### 🎨 Giao diện & Trải nghiệm người dùng
* **Thiết kế chuẩn Material Design:** Giao diện sạch sẽ, chuyên nghiệp, hỗ trợ Responsive hoàn hảo trên cả máy tính và điện thoại.
* **Menu 3 chấm thông minh:** Tối ưu không gian hiển thị, không bị tràn màn hình trên thiết bị di động.
* **Chế độ hiển thị:** Chuyển đổi linh hoạt giữa Dạng lưới (Grid View) và Dạng danh sách (List View).
* **Quản lý hàng loạt:** Hỗ trợ tích chọn nhiều tệp/thư mục để xóa hoặc thao tác cùng lúc.

### 🚀 Công nghệ Tải lên & Tải xuống
* **Mã hóa AES-256:** Tất cả tệp tin đều được mã hóa nội bộ trước khi gửi lên Telegram. Chỉ hệ thống của bạn mới có chìa khóa để giải mã.
* **Hàng đợi thông minh (Upload Queue):** Cho phép tải lên hàng chục tệp cùng lúc. Hệ thống sẽ tự động xếp hàng và tải lần lượt từng tệp để đảm bảo ổn định.
* **Chống Rate-Limit:** Cơ chế Auto-Retry tự động "ngủ" và thử lại khi bị Telegram chặn do gửi dữ liệu quá nhanh (Lỗi 429), giúp tải thành công các tệp siêu lớn (>1GB).
* **Điều khiển trực tiếp:** Nút **Tạm dừng / Tiếp tục / Hủy** quá trình tải lên ngay trên giao diện Web.

### 🎥 Trình xem trực tiếp (Preview)
* Hỗ trợ xem trực tiếp không cần tải về đối với: **Video (MP4, MKV), Audio (MP3), Hình ảnh, PDF, Văn bản (TXT)**.
* Tích hợp Google Docs Viewer để xem các tệp văn phòng: **Word, Excel, PowerPoint**.
* **Dark Mode Viewer:** Giao diện xem phim/ảnh chuẩn rạp chiếu phim.

### 🤖 Telegram Bot & Quản trị
* **Chạy song song (Multiprocessing):** Khởi động Web và Bot chỉ với một câu lệnh duy nhất.
* **Admin Control qua Telegram:**
    * Nhận thông báo tin nhắn từ khách trên Web và trả lời trực tiếp qua Bot (`/reply`).
    * Kiểm tra dung lượng hệ thống (`/info`).
    * Khóa tài khoản vi phạm ngay lập tức (`/ban`).
* **Admin Panel (Web):** Quản lý người dùng, cấu hình SMTP, logo, tên website và sao lưu dữ liệu.

---

## 🛠️ Hướng dẫn cài đặt

### 1. Yêu cầu hệ thống
* Python 3.8 trở lên.
* Một Bot Token từ [@BotFather](https://t.me/BotFather).
* Một Chat ID (Group hoặc Channel) để chứa file dữ liệu mã hóa.

### 2. Cài đặt thư viện
Mở Terminal/CMD và chạy lệnh:
```bash
pip install Flask pycryptodomex pyTelegramBotAPI Flask-Login Werkzeug requests
```

### 3. Cấu hình
Mở file `main.py` và `bot.py`, cập nhật các thông số sau:
* `TELEGRAM_BOT_TOKEN`: Token con bot của bạn.
* `TELEGRAM_CHAT_ID`: ID nhóm/kênh lưu trữ dữ liệu.

### 4. Khởi chạy
```bash
python main.py
```
*Hệ thống sẽ tự động khởi động Web Server tại cổng `9006` và kích hoạt Telegram Bot chạy ngầm.*

---

## 🔑 Tài khoản mặc định (Default Login)

Sau khi khởi chạy lần đầu, bạn truy cập `http://localhost:9006` và sử dụng thông tin sau để đăng nhập quyền Quản trị:

* **Tên đăng nhập:** `admin`
* **Mật khẩu:** `admin`

*(Lưu ý: Bạn nên đổi mật khẩu ngay sau khi đăng nhập lần đầu tại trang Quản trị).*

---

## 💬 Lệnh Telegram Bot

Sau khi dùng lệnh `/login <username> <password>` trên Telegram để liên kết tài khoản, bạn có thể sử dụng:

| Lệnh | Chức năng | Đối tượng |
| :--- | :--- | :--- |
| `/help` | Xem danh sách lệnh hỗ trợ | Tất cả |
| `/info` | Xem dung lượng đã dùng, số tệp/thư mục | Người dùng |
| `/chats` | Xem danh sách khách đang nhắn tin hỗ trợ | Admin |
| `/reply <ID> <nội dung>` | Trả lời khách chat từ Web qua Telegram | Admin |
| `/ban <username> <lý do>` | Khóa tài khoản người dùng vi phạm | Admin |
| `/logout` | Hủy liên kết Bot với tài khoản Web | Tất cả |

---

## ⚠️ Lưu ý quan trọng
* **Tệp Database:** File `data/file_data.db` cực kỳ quan trọng. Nó chứa toàn bộ Khóa giải mã AES. Nếu mất file này, bạn sẽ không thể mở lại các tệp đã tải lên Telegram. Hãy thường xuyên bấm nút **Backup** trong Admin Panel.
* **Bảo mật:** Không chia sẻ file dữ liệu cho người lạ vì nó chứa các thông tin cấu hình nhạy cảm.

---

## 📄 Giấy phép
Dự án được phát hành dưới giấy phép MIT. Tự do sử dụng và phát triển thêm.
