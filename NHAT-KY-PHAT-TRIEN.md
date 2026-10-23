# Nhật ký phát triển — Bot giao việc Telegram (@iaict_media_bot)

File này ghi lại **mọi thay đổi trong quá trình xây dựng dự án**: đổi yêu cầu, đổi thiết kế, quyết định kỹ thuật, sự cố và cách xử lý. Cuối mỗi giai đoạn, file thiết kế (`docs/ban-thiet-ke-bot-chuyen-nghiep.html`) sẽ được hoàn thiện lại cho khớp với thực tế đã xây, dựa trên nhật ký này.

## Quy ước ghi

Mỗi mục ghi theo mẫu:

```
### YYYY-MM-DD — <tiêu đề ngắn>
- **Hạng mục:** Thiết kế / Tính năng / Kỹ thuật / Sự cố / Vận hành
- **Thay đổi:** đã làm gì, khác gì so với trước
- **Lý do:** vì sao đổi
- **Trạng thái:** Đang làm / Đã xong / Tạm gác (kèm điều kiện mở lại)
```

Người ghi: Muse (theo yêu cầu/duyệt của chủ dự án) hoặc chủ dự án tự ghi trực tiếp.

---

## Nhật ký

### 2026-10-23 — Đưa dự án lên GitHub, thiết kế được duyệt làm chuẩn
- **Hạng mục:** Thiết kế / Vận hành
- **Thay đổi:** Bản thiết kế bot chuyên nghiệp (menu trái theo vai trò, bàn phím chính theo vai trò, wizard giao việc từng bước, thống kê trong Telegram, thẻ sửa tại chỗ) được chủ dự án duyệt ("Bản thiết kế ok"). Xuất file thiết kế dạng HTML tĩnh vào `docs/ban-thiet-ke-bot-chuyen-nghiep.html`, tạo file nhật ký này và đưa toàn bộ mã nguồn bot lên GitHub.
- **Lý do:** Có một nguồn chuẩn duy nhất để theo dõi thiết kế và tiến độ xây dựng.
- **Trạng thái:** Đã xong. Lộ trình: GĐ1 menu + bàn phím + wizard + thẻ sửa tại chỗ → GĐ2 thống kê + kênh Video → GĐ3 đăng YouTube/TikTok → GĐ4 Mini App (tuỳ chọn).

### 2026-10-23 — Bổ sung mảng Video vào thiết kế
- **Hạng mục:** Thiết kế
- **Thay đổi:** Thêm 2 đầu ra Video có thẻ duyệt/đăng riêng: Video dọc 9:16 (TikTok, YouTube Shorts, Reels) và Video ngang 16:9 (YouTube). Một việc cho ra cả bài viết lẫn video từ cùng tư liệu gốc; Muse dựng video nháp, gửi duyệt trong Telegram trước khi đăng. Thống kê thêm chỉ số video theo nền tảng.
- **Lý do:** Yêu cầu của chủ dự án; về sau Muse quản lý cả YouTube và TikTok như các kênh đăng.
- **Trạng thái:** Đã vào thiết kế. Điều kiện cần kiểm chứng khi triển khai: quyền tải lên YouTube qua tài khoản chủ kênh; chính sách duyệt ứng dụng đăng bài của TikTok.

### 2026-10-22 — Đường đăng Facebook chuyển sang trình duyệt của Muse
- **Hạng mục:** Kỹ thuật / Quyết định
- **Thay đổi:** Đường đăng chính: Muse đăng trực tiếp bằng trình duyệt (phiên đăng nhập Facebook của chủ dự án), đăng được cả trang cá nhân và Fanpage kèm ảnh; duyệt trong Telegram là chốt cuối. Đường Graph API (System User token) tạm gác.
- **Lý do:** Connector bắt duyệt từng lần đăng; Graph API bị chặn ở bước đăng ký Meta developer (SMS xác minh không về, sau đó bị Meta giới hạn tần suất gửi). Trình duyệt đã kiểm chứng đăng thành công cả 2 đích kèm ảnh.
- **Trạng thái:** Đang dùng đường trình duyệt. Graph API mở lại khi xác minh developer thành công.

### 2026-10-22 — Reset sạch dữ liệu bot, làm lại từ đầu
- **Hạng mục:** Vận hành
- **Thay đổi:** Xoá toàn bộ người dùng/phân quyền và các việc thử nghiệm (CV-0001, CV-0002). Sao lưu giữ tại `data/backup-20261022-1827/` (chỉ lưu trên máy, **không** đưa lên GitHub).
- **Lý do:** Yêu cầu của chủ dự án để bắt đầu vận hành thật với dữ liệu sạch.
- **Trạng thái:** Đã xong. Sau reset, người dùng đầu tiên /start sẽ nhận quyền Admin gốc.

### 2026-10-22 — Chốt kiến trúc đầu ra theo kênh (Phương án A)
- **Hạng mục:** Thiết kế / Quyết định
- **Thay đổi:** Mỗi kênh đăng (Website, Facebook, Instagram, Video để dành sẵn) có thẻ duyệt/đăng riêng trong Telegram với vòng đời độc lập (Nháp → Chờ duyệt → Đã duyệt chờ đăng → Đã đăng). Việc gốc chỉ là hồ sơ tư liệu + mục lục tổng hợp trạng thái các thẻ kênh. Nội dung mạng xã hội viết tách riêng từng kênh, không rút gọn từ bài website.
- **Lý do:** Chủ dự án duyệt/duyệt-đăng độc lập từng kênh; tránh một kênh lỗi làm kẹt cả việc.
- **Trạng thái:** Đã xây xong phần lõi (bảng đầu ra theo kênh), tự kiểm thử 31/31 đạt.

### 2026-10-22 — Khởi tạo bot giao việc
- **Hạng mục:** Tính năng
- **Thay đổi:** Dựng bot @iaict_media_bot chạy nền liên tục (systemd + watchdog tự khôi phục khi máy chủ thay mới): 5 vai trò (Admin, Phó Admin, Biên tập viên, Nhân viên viết bài, Cộng tác viên), cấp/thu hồi quyền ngay trong Telegram, giao việc bằng ảnh + gợi ý ngắn, thẻ việc hiện rõ Người tạo và Thời gian tạo (giờ Việt Nam).
- **Lý do:** Nhu cầu gốc của dự án: nhân viên giao việc cho Muse viết bài, duyệt và đăng đa kênh.
- **Trạng thái:** Đã xong, đang vận hành.

---

## Mẫu ghi mục mới (copy xuống dưới)

### YYYY-MM-DD — <tiêu đề ngắn>
- **Hạng mục:**
- **Thay đổi:**
- **Lý do:**
- **Trạng thái:**
