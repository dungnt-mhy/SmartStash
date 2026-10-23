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

### 2026-10-23 — Thêm nút ✏️ Sửa lại cho người tạo trên thẻ kênh
- **Hạng mục:** Trải nghiệm người dùng
- **Thay đổi:** Chủ dự án xem thẻ CV-0007 và yêu cầu "phải có nút sửa lại + gợi ý sửa lại". Trước đó ↩️ Trả lại chỉ dành cho người duyệt; người tạo (kể cả Admin, sau đợt phân vai) không có cách nào bảo Muse viết lại bản nháp ngay trên thẻ. Đã thêm nút **✏️ Sửa lại** cho NGƯỜI TẠO trên mọi thẻ kênh đã có nháp (trạng thái Nháp/Bị trả): bấm → gõ nội dung cần sửa → đầu ra chuyển sang "cần viết lại" kèm nguyên văn yêu cầu (audit `revise_output`), bot chỉ gửi đúng 1 tin xác nhận; bộ theo dõi nền bắt sự kiện này như một lượt trả bài và Muse viết lại, gắn lại cùng trang telegra.ph. Bố cục thẻ của Admin: [🚀 Đăng] / [✏️ Sửa lại · 🗑 Hủy bài]; thẻ người tạo thường: [📤 Gửi duyệt] / [✏️ Sửa lại].
- **Trạng thái:** Đã xong. Kiểm thử 106/106 đạt, bot đã khởi động lại, thẻ CV-0007 đã được gửi lại với nút mới.

### 2026-10-23 — Tăng tốc gắn nháp: tải ảnh song song + quét việc mới mỗi 30 giây
- **Hạng mục:** Hiệu năng
- **Thay đổi:** Chủ dự án nhận xét tạo bài qua telegra.ph "mất khá nhiều time". Đo thực tế: bản thân telegra.ph rất nhanh (gắn lại 1 đầu ra gồm sửa trang + nhúng ảnh đã lưu cache chỉ ~1 giây). Phần chậm nằm ở (1) chờ bộ quét phát hiện việc mới (chu kỳ 60 giây) và (2) tải ảnh MỚI lên GitHub nối tiếp từng ảnh. Đã sửa cả hai: chu kỳ quét giảm còn 30 giây; botctl tải các ảnh của một việc song song (tối đa 4 luồng, giữ nguyên thứ tự ảnh; ImageHost thêm khóa an toàn cho cache).
- **Trạng thái:** Đã xong. Kiểm thử 102/102 đạt.

### 2026-10-23 — Nút bấm phân vai rõ ràng: thẻ của Admin không còn Gửi duyệt / Duyệt / Trả lại
- **Hạng mục:** Phân quyền / trải nghiệm người dùng
- **Thay đổi:** Chủ dự án chốt lại: "Admin tự tạo bài thì không cần gửi duyệt, duyệt, và trả lại — cần phân vai và nút rõ ràng". Trước đó thẻ việc của chính Admin vẫn còn nút ↩️ Trả lại (do hàng nút của người duyệt). Nay: Admin xem thẻ kênh của việc do mình tạo (Nháp / Bị trả / Chờ duyệt cũ) chỉ còn 2 nút [🚀 Đăng] (khi đã có nháp) và [🗑 Hủy bài] — không Gửi duyệt, không Duyệt, không Trả lại. Các vai khác giữ nguyên: người tạo thường có Gửi duyệt; người duyệt xem việc của người khác vẫn đủ Duyệt / Trả lại / Hủy bài. Bấm Đăng từ trạng thái Chờ duyệt cũ của việc Admin cũng đi đường tắt như Nháp.
- **Trạng thái:** Đã xong. Kiểm thử 102/102 đạt, bot đã khởi động lại.

### 2026-10-23 — Thẻ kênh mới tự đóng băng thẻ cũ: hết nút Đăng "sống" trên thẻ đã đăng
- **Hạng mục:** Trải nghiệm người dùng / tính nhất quán thẻ
- **Thay đổi:** Chủ dự án gửi ảnh chụp thẻ CV-0004: bài đã đăng xong mà thẻ cũ trong khung chat vẫn hiện nút 🚀 Đăng. Nguyên nhân: bot chỉ sửa tại chỗ thẻ bị bấm, còn các thẻ gửi sau (gắn nháp, hoàn tất đăng) là tin mới — thẻ cũ giữ nguyên nút bấm cũ. Nay bot ghi nhớ mọi thẻ kênh đã gửi (bảng mới `output_cards`); mỗi khi gửi thẻ mới cho một đầu ra, các thẻ cũ của chính đầu ra đó trong cùng cuộc trò chuyện được sửa tại chỗ về trạng thái hiện tại và **gỡ toàn bộ nút** (đóng băng như thẻ việc đã chốt). Chỉ thẻ mới nhất còn nút thao tác.
- **Trạng thái:** Đã xong. Kiểm thử 100/100 đạt, bot đã khởi động lại.

### 2026-10-23 — Admin bỏ qua duyệt với việc của mình · chốt nơi đăng Facebook ngay trong Telegram · hết trùng nút Đăng
- **Hạng mục:** Quy trình duyệt/đăng
- **Thay đổi:** Ba phản ánh liên tiếp của chủ dự án qua ảnh chụp CV-0006: (1) "Admin thì cần gì gửi duyệt, duyệt" — thẻ kênh của việc do chính Admin tạo, khi đã có nháp, hiện thẳng nút 🚀 Đăng thay cho 📤 Gửi duyệt; bấm Đăng là tự nhảy qua khâu duyệt (audit approve_output auto=admin_own) rồi đi tiếp. (2) "Phải chốt ở Telegram chứ" — bấm Đăng ở kênh Facebook nay hiện câu hỏi ngay trong Telegram: [👤 Trang cá nhân] / [📄 Fanpage Minhy]; chọn xong mới ghi lệnh đăng (audit publish_requested kèm target=profile/page) và tin chọn nơi đăng tự sửa thành tin nhận lệnh duy nhất. Bộ theo dõi nền cũng chuyển target này cho Muse khi đăng. (3) "Đã duyệt rồi, xong lại hiện đăng, bên dưới cũng có đăng" — nguyên nhân: thẻ gốc tự sửa tại chỗ thành chờ đăng (có nút Đăng) VÀ bot gửi thêm một thẻ thông báo duyệt nữa (cũng có nút Đăng). Đã bỏ thẻ thông báo khi người duyệt chính là người tạo (thẻ đã bấm tự cập nhật là đủ); duyệt việc của người khác vẫn báo cho người tạo như cũ.
- **Trạng thái:** Đã xong. Kiểm thử 98/98 đạt (thêm 3 test cho cả ba hành vi), bot đã khởi động lại.

### 2026-10-23 — Luồng Đăng bài: một nút Đăng, một thông báo thành công kèm link
- **Hạng mục:** Quy trình đăng bài / trải nghiệm người dùng
- **Thay đổi:** Chủ dự án phản ánh bấm Đăng thì hiện 2 lần "đăng" và yêu cầu "thông báo đã đăng thành công và gửi link bài". Kiểm tra tận gốc: nút 🚀 Đăng cũ tự lật trạng thái sang PUBLISHED và báo "đã đăng" dù CHƯA có bài thật và không có link (CV-0005 Facebook bị lật oan lúc 13:35, public_url trống — đã đưa về APPROVED_WAITING kèm audit `revert_hollow_publish`). Nay: nút Đăng chỉ ghi nhận lệnh đăng (audit `publish_requested`) + đúng 1 tin nhận lệnh; Muse đăng thật rồi hoàn tất bằng `botctl set-output-published <link>` — lệnh này nay gửi đúng 1 thông báo "✅ Đã đăng thành công" kèm thẻ kênh chứa link bài cho người tạo + quản trị (đã khử trùng người nhận).
- **Trạng thái:** Đã xong. Kiểm thử 92/92 đạt, bot đã khởi động lại.

### 2026-10-23 — Thông báo "Phiên giao việc đã kết thúc": bỏ hộp thoại, chuyển thành toast nhẹ
- **Hạng mục:** Trải nghiệm người dùng
- **Thay đổi:** Chủ dự án chê hộp thoại cảnh báo (modal alert) khi bấm nút của phiên giao việc đã đóng. Cả 5 chỗ trả lời "Phiên giao việc đã kết thúc" chuyển từ alert modal sang **toast nhỏ** kèm câu chữ thân thiện: "⌛ Phiên giao việc này đã kết thúc. 📝 Bấm Giao việc mới để bắt đầu việc khác." Các cảnh báo quan trọng (quyền, trạng thái) vẫn giữ dạng hộp thoại để gây chú ý.
- **Trạng thái:** Đã xong. Kiểm thử 92/92 đạt, bot đã khởi động lại.

### 2026-10-23 — Nút chọn kênh: bỏ tích xanh, icon đổi thành ❌ khi bỏ chọn; bỏ nút "Chọn tất cả"
- **Hạng mục:** Trải nghiệm người dùng
- **Thay đổi:** Theo chỉ đạo của chủ dự án: nút kênh đang chọn chỉ hiện **icon + tên kênh** (bỏ hẳn dấu ✅ phía trước); chạm để bỏ chọn thì **icon của kênh đổi thành ❌** (tên kênh giữ nguyên), chạm lại thì icon trở lại. **Bỏ nút "Chọn tất cả"** ở cả wizard Bước 3 lẫn thẻ việc — chỉ có 3 kênh, không cần nút này. (Chủ dự án cũng xác nhận emoji không có icon Facebook/Instagram chính thức; bộ icon 🌐/📘/📸 giữ nguyên làm biểu tượng đại diện.)
- **Trạng thái:** Đã xong. Kiểm thử 91/91 đạt, bot đã khởi động lại.

### 2026-10-23 — Icon cho từng kênh + nút hủy về thùng rác
- **Hạng mục:** Trải nghiệm người dùng
- **Thay đổi:** Theo yêu cầu của chủ dự án: các nút chọn kênh có icon riêng — **🌐 Website, 📘 Facebook, 📸 Instagram** (áp dụng ở Bước 3 wizard, khối chọn kênh trên thẻ việc và dòng 🎯 nơi đăng); nút hủy ở Bước 3 quay về icon **thùng rác** ("🗑 Hủy việc") theo đúng chỉ đạo "hủy việc vẫn là icon thùng rác đỏ" (trên điện thoại của chủ dự án emoji thùng rác hiển thị tông đỏ).
- **Trạng thái:** Đã xong. Kiểm thử 91/91 đạt, bot đã khởi động lại.

### 2026-10-23 — Nút hủy ở Bước 3: "❌ Hủy việc"
- **Hạng mục:** Trải nghiệm người dùng
- **Thay đổi:** Theo yêu cầu của chủ dự án, nút hủy ở Bước 3 wizard đổi từ "🗑 Huỷ" thành **"❌ Hủy việc"** (icon đỏ). Hàng cuối Bước 3 nay là [🚀 Chốt! Gửi Muse] [❌ Hủy việc].
- **Trạng thái:** Đã xong. Kiểm thử 91/91 đạt, bot đã khởi động lại.

### 2026-10-23 — Rút gọn tên nút chốt: "Chốt! Gửi Muse"
- **Hạng mục:** Trải nghiệm người dùng
- **Thay đổi:** Theo yêu cầu của chủ dự án, nút chốt bỏ chữ "cho": **"🚀 Chốt! Gửi Muse"** — áp dụng ở Bước 3 wizard, thẻ việc và mọi câu chữ dẫn chiếu tới nút.
- **Trạng thái:** Đã xong. Kiểm thử 91/91 đạt, bot đã khởi động lại.

### 2026-10-23 — Bước 3 wizard: thêm ghi chú mặc định + đổi icon nút chốt thành 🚀
- **Hạng mục:** Trải nghiệm người dùng
- **Thay đổi:** Theo góp ý của chủ dự án khi test: tin Bước 3/3 thêm dòng ghi chú "📌 Mặc định đang chọn cả 3 nơi đăng — chạm vào từng kênh để bỏ chọn nếu không cần."; nút chốt đổi icon ✅ → **🚀** ("🚀 Chốt! Gửi Muse") ở cả Bước 3 wizard lẫn thẻ việc, để phân biệt với dấu ✅ "đã chọn" trên các nút kênh.
- **Trạng thái:** Đã xong. Kiểm thử 91/91 đạt, bot đã khởi động lại. (Cùng buổi: máy chủ bị thay mới làm mất đăng ký dịch vụ systemd — đã cài lại unit + EnvironmentFile từ bản mẫu trong repo, dữ liệu bot nguyên vẹn, bot chạy lại từ 11:42.)

### 2026-10-23 — Sửa lỗi nút "Huỷ giao việc" bấm không ăn (bàn phím cũ sau khi đổi tên nút)
- **Hạng mục:** Sửa lỗi
- **Thay đổi:** Chủ dự án phản ánh nút "❌ Huỷ giao việc" cứ hiện mãi, bấm không ăn. Nguyên nhân: nút đã được đổi tên thành "🗑 Huỷ giao việc" (icon thùng rác) nhưng Telegram giữ bàn phím cũ trên máy người dùng — nhãn cũ bấm vào không khớp nhãn mới nên bot không nhận. Đã thêm **nhãn tương thích ngược** cho nút Huỷ wizard (nhãn ❌ cũ vẫn hủy được, đồng thời khôi phục bàn phím chính); các nút bị gỡ khỏi bàn phím chính (Người dùng/Thống kê/Tất cả việc) vốn vẫn còn đường xử lý nên không lỗi tương tự. Bàn phím Bước 1 của wizard gộp [Xong | Huỷ] vào 1 hàng cho gọn.
- **Trạng thái:** Đã xong. Kiểm thử 91/91 đạt, bot đã khởi động lại.

### 2026-10-23 — Hàng nút duyệt trên thẻ kênh: Duyệt · Trả lại · Hủy bài
- **Hạng mục:** Trải nghiệm người dùng
- **Thay đổi:** Theo chỉ đạo của chủ dự án: nút "↩️ Trả lại kèm yêu cầu sửa" rút gọn tên thành **"↩️ Trả lại"** (lời nhắc nhập nội dung cần sửa giữ nguyên khi bấm); thêm nút mới **"🗑 Hủy bài"** — hủy hẳn đầu ra của kênh đó (trạng thái CANCELLED, thẻ mất hết nút), chỉ người có quyền duyệt mới bấm được, bấm xong chỉ 1 tin báo cho người bấm. Trên thẻ đang chờ duyệt, ba nút **Duyệt · Trả lại · Hủy bài nằm cùng 1 hàng**; trên thẻ kênh đang Nháp đã có bản nháp, hàng tương ứng là [Trả lại · Hủy bài].
- **Trạng thái:** Đã xong. Kiểm thử 90/90 đạt, bot đã khởi động lại.

### 2026-10-23 — Trả bài chỉ còn đúng 1 thông báo
- **Hạng mục:** Trải nghiệm người dùng
- **Thay đổi:** Theo chỉ đạo của chủ dự án: khi người duyệt bấm trả lại thẻ kênh kèm yêu cầu sửa, bot **chỉ gửi đúng 1 tin** "↩️ Đã trả lại thẻ kênh … ✏️ Yêu cầu chỉnh sửa: …" cho người trả — **bỏ hẳn** tin báo + thẻ gửi lại cho người tạo. Người tạo chỉ nhận lại thẻ khi Muse gắn bản sửa mới (đúng nhịp "khi nào có kết quả mới gửi lại"). Đã áp dụng ngay cho lượt trả thật đầu tiên (CV-0002 · Instagram, yêu cầu "Viết hay hơn"): bản Instagram viết lại hay hơn (carousel 4 slide, caption mới) và thẻ đã quay lại Telegram.
- **Trạng thái:** Đã xong. Kiểm thử 88/88 đạt, bot đã khởi động lại.

### 2026-10-23 — Chuẩn thẻ mới (mỗi dòng 1 icon), Bước 3 wizard thành nút chốt, trang nháp có ảnh mặc định
- **Hạng mục:** Trải nghiệm người dùng + đường giao bản nháp
- **Thay đổi:** Ba điều chỉnh theo chỉ đạo trực tiếp của chủ dự án trong buổi test sau reset:
  1. **Định dạng thẻ:** thẻ việc và thẻ kênh viết lại theo quy tắc "mỗi dòng đúng 1 icon, gộp trường liên quan vào cùng dòng" — bỏ các dòng nhãn trơ ("Loại đầu ra:", "Việc gốc:", "Người tạo:", "Thời gian tạo:"), thay bằng các dòng 🗂 mã việc · loại, 👤 người tạo · thời gian, 💡 gợi ý, 📄 bản nháp.
  2. **Wizard Bước 3 = bản chốt hoàn chỉnh:** hiện dòng gợi ý hiện tại kèm nút **"✏️ Sửa nội dung gợi ý"** (gõ lại gợi ý ngay tại Bước 3); nút kết thúc đổi tên thành **"✅ Chốt! Gửi Muse"** và hoạt động đúng như nút chốt trên thẻ việc (tạo việc + ghi nhận đã chốt + thẻ việc đóng băng + đúng 1 tin thông báo); nút Huỷ dùng icon thùng rác 🗑 ở cả nút inline lẫn bàn phím wizard. Sửa thêm lỗi định tuyến: tin nhắn chữ ở trạng thái wizard_channels trước đây không tới được bộ xử lý wizard.
  3. **Trang nháp telegra.ph:** sửa lỗi tiêu đề trang Instagram lấy nhầm dòng phụ đề ("Phụ đề nhỏ…") — nay luôn lấy câu chữ trên ảnh bìa của slide 1; **nhúng ảnh tư liệu vào trang thành mặc định** (chủ dự án chỉ đạo rõ "nên thêm ảnh vào" sau đợt test CV-0004; còn cờ `--no-images` để ngoại lệ). Đã gắn lại cả 6 đầu ra của CV-0001/CV-0002 kèm ảnh và kiểm chứng trực tiếp trên telegra.ph (có figure ảnh + tiêu đề đúng).
- **Trạng thái:** Đã xong. Kiểm thử 88/88 đạt, bot đã khởi động lại.

### 2026-10-23 — Bàn phím chính gọn tiếp: bỏ nút Thống kê và Tất cả việc
- **Hạng mục:** Trải nghiệm người dùng
- **Thay đổi:** Theo yêu cầu của chủ dự án, bàn phím vai trò người duyệt/Admin bỏ luôn hàng "📋 Tất cả việc / 📊 Thống kê" — chỉ còn [Giao việc mới | Chờ duyệt] và hàng cuối [Muse đang làm | Ẩn thao tác]. Các lệnh /viec và /thongke vẫn dùng bình thường.
- **Trạng thái:** Đã xong. Kiểm thử 85/85 đạt, bot đã khởi động lại.

### 2026-10-23 — Bố cục bàn phím chính: bỏ nút Người dùng, hàng cuối là [Muse đang làm | Ẩn thao tác]
- **Hạng mục:** Trải nghiệm người dùng
- **Thay đổi:** Theo yêu cầu trực tiếp của chủ dự án: bàn phím chính **bỏ hẳn nút "👥 Người dùng"** (quản lý người dùng vẫn còn qua lệnh /danhsach và các luồng cấp quyền); hàng cuối gộp thành một hàng duy nhất: **"🤖 Muse đang làm" bên trái, "🙈 Ẩn thao tác" bên phải** (nút ẩn đổi tên từ "Ẩn bàn phím" thành "Ẩn thao tác"). Áp dụng cho mọi vai trò.
- **Trạng thái:** Đã xong. Kiểm thử 85/85 đạt, bot đã khởi động lại.

### 2026-10-23 — Thông báo "Muse đang làm": gom theo từng việc, có vạch ngăn
- **Hạng mục:** Trải nghiệm người dùng
- **Thay đổi:** Chủ dự án chê danh sách trạng thái nối dài bằng dấu phẩy khó đọc ("CV-0002 · Website, CV-0002 · Facebook, …"). Nay `_cmd_claude_status` trình bày **theo từng việc**: mỗi việc một khối "▸ MÃ VIỆC — gợi ý" và dòng kênh nằm dưới ("Website • Facebook • Instagram"); các mục (chờ viết nháp / đã có nháp / chờ duyệt) cách nhau bằng vạch "────────────". Đã kiểm chứng trên dữ liệu test thật của chủ dự án (CV-0001, CV-0002 sau reset).
- **Trạng thái:** Đã xong. Kiểm thử 85/85 đạt, bot đã khởi động lại.

### 2026-10-23 — Tinh chỉnh nút chốt: hoán vị trí, đổi tên "Chốt! Gửi Muse", chỉ 1 tin thông báo sau khi chốt
- **Hạng mục:** Trải nghiệm người dùng
- **Thay đổi:** Theo yêu cầu trực tiếp của chủ dự án: hàng nút dưới cùng của thẻ việc đổi thứ tự thành **[✅ Chốt! Gửi Muse] [🗑 Thu hồi việc]** (nút chốt đứng trước, tên đúng nguyên văn chủ dự án đặt). Sau khi bấm chốt, bot **chỉ gửi đúng 1 tin thông báo** ("Đã chốt kênh đăng bài… Đã gửi đến Muse. Đợi Muse làm xong…") rồi im lặng chờ — **không gửi kèm loạt thẻ kênh** như bản trước nữa; thẻ kênh sẽ đến sau, cùng bản nháp khi Muse làm xong. Thẻ việc vẫn đóng băng tại chỗ như đã chốt ở mục trước.
- **Trạng thái:** Đã xong. Kiểm thử 85/85 đạt (bổ sung kiểm chứng thứ tự 2 nút ở hàng cuối và việc không có thẻ kênh nào được gửi kèm sau khi chốt). Bot đã khởi động lại.

### 2026-10-23 — Thẻ việc đổi sang mô hình chốt bằng OK: chọn kênh theo giai đoạn, đóng băng thẻ, nút trạng thái "Muse đang làm"
- **Hạng mục:** Tính năng / Trải nghiệm người dùng
- **Thay đổi:** Theo mô tả trực tiếp của chủ dự án khi test bản reset (kèm ảnh chụp khối nút trên thẻ việc):
  - "✏️ Chỉnh sửa" → **"✏️ Chỉnh sửa gợi ý"** cho rõ nghĩa.
  - Mục "📋 Thẻ các kênh" → **"🎯 Chọn kênh đăng bài"** (nút tiêu đề của khối chọn kênh; chạm vào hiện hướng dẫn). Người không có quyền đổi kênh vẫn thấy nút Thẻ các kênh như cũ.
  - Ba nút kênh đổi cơ chế: mặc định chọn tất cả (✅); chạm để bỏ chọn → hiện **❌** (trước là ⬜). Các chạm chỉ là **chọn tạm trên thẻ** (lưu ở trạng thái chờ của người dùng, thẻ tự sửa tại chỗ) — **chưa áp dụng ngay** như trước, nên không còn nguy cơ chạm nhầm làm huỷ đầu ra/bản nháp.
  - Thêm nút **"✅ OK" ngay cạnh "🗑 Thu hồi việc"**: bấm OK mới chốt — đồng bộ nơi đăng + các đầu ra kênh, gửi lại thẻ kênh, và bot báo: *"🎯 Đã chốt kênh đăng bài cho việc… ✅ Đã gửi đến Muse. Đợi Muse làm xong — khi xong, Muse sẽ thông báo và gửi kết quả công việc (thẻ kênh kèm bản nháp) ngay tại đây."* (đúng nội dung chủ dự án yêu cầu). Việc sau khi chốt nằm trong hàng đợi Muse theo dõi để viết nháp.
  - Sau khi OK, **thẻ việc bị đóng băng** (như thanh hành động của Facebook): toàn bộ nút trên chính thẻ đó bị gỡ, thẻ thêm dòng "🔒 Đã chốt kênh đăng — Muse đang làm bài cho các kênh đã chọn…". Luồng tiếp tục trên các thẻ kênh mới gửi phía dưới; thẻ việc gửi mới về sau vẫn thao tác bình thường.
  - Bàn phím dưới khung chat: nút **"❓ Trợ giúp" được thay bằng "🤖 Muse đang làm"** — chạm vào bot báo tình hình hàng đợi thật từ dữ liệu: kênh nào đang chờ Muse viết nháp, kênh nào đã có nháp chờ gửi duyệt, thẻ nào đang chờ duyệt. Lệnh /help vẫn dùng như cũ.
- **Trạng thái:** Đã xong. Kiểm thử 85/85 đạt (viết lại 3 test chọn kênh theo mô hình chọn-tạm/OK, thêm kiểm chứng đóng băng thẻ sau OK và nút trạng thái Muse). Bot đã khởi động lại bản mới.

### 2026-10-23 — Ba chỉnh sửa trải nghiệm Telegram: tắt hộp xem trước link, nút Trả lại kèm yêu cầu sửa, bàn phím chính ẩn/hiện được
- **Hạng mục:** Tính năng / Trải nghiệm người dùng
- **Thay đổi:** Theo 3 yêu cầu trực tiếp của chủ dự án (kèm ảnh chụp thực tế):
  1. **Tắt hộp xem trước link**: Telegram tự dựng hộp preview to (kèm ảnh bìa trang) dưới link bản nháp trên thẻ kênh — nay `telegram_client.send_message` luôn gửi kèm `link_preview_options.is_disabled`, mọi tin nhắn của bot hết hộp preview; link vẫn bấm mở trang bình thường (trang bên trong vẫn có ảnh).
  2. **Nút "↩️ Trả lại kèm yêu cầu sửa"**: đổi nhãn từ "Trả lại" cho rõ nghĩa; nay người duyệt bấm được **ngay trên thẻ kênh đang Nháp** (miễn đã có bản nháp), không phải đợi người tạo bấm Gửi duyệt trước. Bot hỏi "NỘI DUNG CẦN SỬA", người tạo nhận lại thẻ kênh với dòng **"✏️ Yêu cầu chỉnh sửa: <nguyên văn>"** và thông báo riêng — trước đây luồng trả-lại-kèm-lý-do mới chỉ có ở trạng thái Chờ duyệt và ghi nhãn "Lý do trả lại".
  3. **Bàn phím chính ẩn/hiện theo ý muốn**: thêm nút **"🙈 Ẩn bàn phím"** ở hàng cuối bàn phím mọi vai trò — bấm là bàn phím biến mất (ReplyKeyboardRemove); gõ **/menu** (đã thêm vào danh sách lệnh Menu của mọi vai trò) hoặc /start để hiện lại.
- **Trạng thái:** Đã xong. Kiểm thử 84/84 đạt (thêm test: người duyệt trả thẻ kênh đang Nháp kèm yêu cầu sửa và người tạo thấy đúng nguyên văn; ẩn/hiện bàn phím + lệnh /menu có trong danh sách lệnh). Bot đã khởi động lại, danh sách lệnh tự đồng bộ theo vai trò.

### 2026-10-23 — Ảnh trong trang telegra.ph (TEST trên CV-0004) + sửa độ ổn định đăng trang
- **Hạng mục:** Tính năng (thử nghiệm, chưa áp dụng diện rộng)
- **Thay đổi:** telegra.ph chặn tải ảnh trực tiếp và catbox bị cắt kết nối từ mạng này, nên dùng đường vòng qua repo GitHub công khai của dự án: module mới `image_host.py` đưa ảnh tư liệu lên `draft-media/<mã việc>/` qua Contents API (cùng cơ chế bảo mật với github-cli, có bộ nhớ đệm theo hash để không tải lại), botctl nhúng link ảnh thô vào trang telegra.ph (Website: sau phần mở đầu; Facebook: trước dòng hashtag; Instagram: mỗi slide một ảnh). Đồng thời sửa `telegraph.py`: tự thử lại khi rớt kết nối, và chọn tiêu đề trang tốt hơn (bỏ qua các nhãn mục như "Tiêu đề"/"Cấu trúc carousel"). Mới áp dụng cho CV-0004 theo yêu cầu test 1 việc; chủ dự án đã xem và chốt "cứ để test vậy đã" — các việc khác vẫn trang chữ. Lưu ý riêng tư: ảnh lên repo công khai thì ai có link cũng xem được — cân nhắc trước khi áp dụng cho việc có ảnh gia đình.

### 2026-10-23 — Bản nháp qua MỘT link telegra.ph trên thẻ kênh (bỏ hẳn gửi nội dung vào Telegram)
- **Hạng mục:** Tính năng / Trải nghiệm người dùng
- **Thay đổi:** Chủ dự án chốt lại yêu cầu lần cuối, rất rõ: **không gửi nội dung bản nháp vào Telegram dưới bất kỳ dạng nào** (không toàn văn, không ảnh mô phỏng) — chỉ cần **một link trên thẻ kênh, nhấn là xem được, cho gọn**; link `.md` bị chê vì trình duyệt không hiển thị được. Giải pháp: `botctl attach-output-draft --file <nháp.md>` nay **đăng bản nháp thành một trang telegra.ph** (dịch vụ bài viết của chính Telegram, mở bằng Instant View ngay trong ứng dụng, rất nhẹ) và dùng **link trang làm tham chiếu nháp** — thẻ kênh trong Telegram chỉ còn: thông tin kênh + dòng link + nút duyệt/đăng. Module mới `telegraph.py`: tạo một tài khoản telegra.ph cho bot (token + danh mục trang lưu ở `data/telegraph.json`, đã đưa vào .gitignore), đăng mới hoặc **sửa tại chỗ cùng một trang** khi gắn lại bản nháp đã sửa (link cũ vẫn dùng được). Nội dung trang: Website/Facebook = toàn văn định dạng (tiêu đề, đoạn, in đậm, danh sách; đã lọc phần SEO/ghi chú biên tập và dòng gợi ý ảnh nội bộ); Instagram = chữ từng slide + caption + hashtag. Đã gỡ toàn bộ cơ chế gửi nội dung vào Telegram của hai lần sửa trước (toàn văn, file HTML, ảnh mô phỏng — gồm cả `send_photo` không còn được botctl dùng). Đã đăng đủ 10/10 đầu ra của CV-0001 → CV-0004 lên telegra.ph và gắn link vào thẻ kênh tương ứng.
- **Lý do:** Yêu cầu trực tiếp, kèm ảnh chụp link `http://CV-0004-facebook.md` bị Telegram tự biến thành link chết: "không gửi draft dạng content trong telegram mà cần nhấn vào link là xem được cho nó gọn".
- **Giới hạn đã biết:** telegra.ph chặn tải ảnh lên từ mạng này (trả "Unknown error" cả với ảnh nén lại) nên trang nháp chỉ có chữ; ảnh tư liệu vẫn nằm trong cuộc trò chuyện Telegram của việc. Trang telegra.ph mở được cho bất kỳ ai có link (không công khai liệt kê ở đâu) — đánh đổi lấy việc mở xem không cần đăng nhập.
- **Trạng thái:** Đã xong. Kiểm thử 80/80 đạt (3 test --file viết lại theo cơ chế đăng link: kiểm chứng link được lưu làm draft_ref, nội dung trang chứa đúng bài/slide/caption, và khung chat chỉ nhận đúng 1 thẻ kênh chứa link). Đã mở kiểm chứng trang Facebook CV-0004: hiển thị đẹp, đủ bài, emoji nguyên vẹn.

### 2026-10-23 — Xem trước Facebook/Instagram ngay trong Telegram bằng ảnh mô phỏng
- **Hạng mục:** Tính năng / Trải nghiệm người dùng
- **Thay đổi:** Chủ dự án nói rõ lại ý: muốn xem trước bài Facebook/Instagram **ngay trong Telegram, trước khi nhấn vào bất cứ link nào, theo cách nhẹ nhất** (lần sửa trước làm ở trang Phòng đọc bản nháp trong Muse là hiểu nhầm chỗ — trang đó vẫn giữ, coi như thêm một nơi đọc). Nay `botctl attach-output-draft --file` còn tự dựng và gửi kèm **ảnh xem trước**: kênh Facebook = 1 ảnh mô phỏng bài đăng (thẻ trắng, chữ của post, ảnh tư liệu); kênh Instagram = mỗi slide carousel 1 ảnh vuông (ảnh tư liệu phủ đầy, chữ của slide ở dưới, huy hiệu 1/N). Ảnh hiện inline ngay trong khung chat Telegram, liếc qua là duyệt nhanh được. `telegram_client` thêm `send_photo` (dùng chung bộ upload multipart với `send_document`); ảnh dựng bằng Pillow, emoji được lược bỏ trong ảnh cho sạch chữ (tin nhắn toàn văn vẫn giữ emoji). Đã gửi bổ sung cho cả 6 đầu ra Facebook/Instagram của CV-0001 → CV-0004 (2 ảnh FB + 13 ảnh slide IG).
- **Lý do:** Yêu cầu trực tiếp của chủ dự án: "ở telegram bạn ơi, không phải ở đây".
- **Trạng thái:** Đã xong. Kiểm thử 80/80 đạt (thêm 2 test: gắn nháp Facebook kèm --file có ảnh xem trước, Instagram có đủ ảnh từng slide; bộ tách slide đã kiểm chứng trên cả 4 việc thật, gồm các biến thể định dạng Chữ trên ảnh/Dòng 1–2/dấu ngoặc kép, và chặn rò chữ từ phần caption vào slide cuối). Bot đã khởi động lại để nạp bản client mới.

### 2026-10-23 — Bản nháp đọc được ngay: toàn văn trong Telegram + bản xem trước HTML cho Website
- **Hạng mục:** Tính năng / Trải nghiệm người dùng
- **Thay đổi:** Chủ dự án phản ánh bản nháp gửi dạng file/tham chiếu `.md` không mở xem được, gợi ý "gửi prototype hay kiểu draft thật". Đã sửa cơ chế chuyển nháp: `botctl attach-output-draft` thêm tuỳ chọn `--file <file.md>` — ngoài thẻ kênh, người tạo + người duyệt nhận (1) **toàn văn bản nháp** dạng tin nhắn Telegram đã gọt bỏ ký hiệu markdown (tự chia đoạn nếu dài), và (2) riêng kênh **Website**: một **file HTML xem trước** trình bày như bài thật (tiêu đề, sapo, thân bài, ảnh tư liệu nhúng base64) gửi bằng sendDocument để chạm mở bằng trình duyệt. `telegram_client` được bổ sung `send_document` (multipart). Đã áp dụng ngay cho cả 3 việc đang chờ (CV-0001, CV-0002, CV-0003 — tổng 7 đầu ra).
- **Lý do:** Phản hồi trực tiếp của chủ dự án khi duyệt thử.
- **Trạng thái:** Đã xong. Kiểm thử 76/76 đạt (thêm test: gắn nháp kèm --file thì toàn văn + file xem trước HTML tới được người tạo), bot không cần khởi động lại (botctl là công cụ dòng lệnh chạy riêng).

### 2026-10-23 — Cách A: duyệt bài trên thẻ kênh + gọn Bước 1 wizard (hết rối)
- **Hạng mục:** Tính năng / Thiết kế luồng
- **Thay đổi:** (1) Chủ dự án chốt **Cách A**: bỏ hẳn nút Gửi duyệt/Duyệt/Đăng/Thu hồi bài trên **thẻ việc gốc** — trước đây bấm Gửi duyệt là duyệt một cái vỏ chưa có bài viết nào (lỗi do chủ dự án chỉ ra khi dùng thử CV-0001). Toàn bộ duyệt/đăng chuyển xuống **thẻ kênh**, nơi có bản nháp thật. (2) Trên thẻ kênh, nút 📤 Gửi duyệt bị **chặn khi kênh chưa có bản nháp** (bot báo "Muse đang viết"), nên không còn duyệt rỗng. (3) **Trạng thái thẻ việc được tự suy ra từ các thẻ kênh** (`sync_task_status_from_outputs`): có kênh chờ duyệt → việc Chờ duyệt; tất cả đã đăng → việc Đã đăng; … (4) Lúc tạo việc, bot **không gửi thẻ việc cho người duyệt nữa** (chưa có gì để duyệt); người duyệt nhận thẻ kênh khi Muse gắn nháp (botctl attach-output-draft) và khi kênh được gửi duyệt. (5) Thẻ việc có thêm dòng **"✍️ Bản nháp: chưa có — đang chờ Muse viết bài"** / "đã có X/Y kênh". (6) Nút cũ trên các thẻ đã gửi từ trước vẫn bấm được nhưng bot trả lời hướng dẫn "duyệt trên thẻ kênh", không đổi trạng thái. (7) **Bước 1 wizard hết rối:** bỏ tin xác nhận + khối nút lặp lại dưới mỗi ảnh; thay bằng **một tin đếm file duy nhất được sửa tại chỗ** ("📎 Đã nhận N file…"), nút ✅ Xong/❌ Huỷ chỉ còn trên bàn phím ghim bên dưới.
- **Lý do:** Phản hồi trực tiếp của chủ dự án: "nếu tôi bấm gửi duyệt thì tôi đã nhận được bài viết đâu?" và "Rối quá, mỗi ảnh lại thêm 1 bước xác nhận".
- **Trạng thái:** Đã xong. Kiểm thử 74/74 đạt (viết lại 12 test theo luồng mới: duyệt/đăng/thu hồi ở thẻ kênh, chặn gửi duyệt khi chưa có nháp, thẻ cũ bị chuyển hướng, tin đếm file sửa tại chỗ), bot đã khởi động lại bản mới, README mục 4–6 đã cập nhật.

### 2026-10-23 — Thêm nút "Chọn tất cả" khi chọn nơi đăng
- **Hạng mục:** Tính năng
- **Thay đổi:** Ở cả hai chỗ chọn kênh (Bước 3 của wizard và hàng nút chọn nơi đăng trên thẻ việc) thêm nút ✅ Chọn tất cả — chỉ hiện khi đang có ít nhất một kênh chưa chọn; bấm một cái là chọn đủ Website + Facebook + Instagram và các thẻ kênh được đồng bộ lại.
- **Lý do:** Yêu cầu trực tiếp của chủ dự án khi dùng thử.
- **Trạng thái:** Đã xong, kiểm thử 74/74 đạt, bot đã chạy bản mới. (Cùng lúc, chủ dự án chỉ ra lỗi luồng "bấm Gửi duyệt ở thẻ gốc khi chưa có bài viết" — chủ dự án đã chốt Cách A và lỗi này đã được sửa ở mục ngay phía trên.)

### 2026-10-23 — Sửa trải nghiệm wizard Bước 1: nút Xong luôn ở dưới tay
- **Hạng mục:** Tính năng / Trải nghiệm người dùng
- **Thay đổi:** Người dùng phản ánh ở Bước 1 (gửi ảnh/slide) phải kéo lên tìm nút ✅ Xong trên tin nhắn hướng dẫn cũ, rất phiền. Đã sửa 3 lớp: (1) trong lúc chạy wizard, bàn phím dưới khung chat tạm đổi thành nút ✅ Xong / ❌ Huỷ luôn ghim ở dưới, tạo việc xong hoặc huỷ thì bàn phím chính tự quay lại; (2) mỗi tin nhắn xác nhận "Đã nhận file" đều gắn kèm nút ✅ Xong và ❌ Huỷ ngay dưới tin mới nhất; (3) gõ chữ "xong" cũng chuyển sang Bước 2 được.
- **Lý do:** Phản hồi trực tiếp của chủ dự án khi dùng thử wizard.
- **Trạng thái:** Đã xong. Kiểm thử 73/73 đạt (thêm 3 test cho bàn phím ghim của wizard, gõ "xong", khôi phục bàn phím chính), bot đã khởi động lại bản mới.

### 2026-10-23 — Xây xong Giai đoạn 1: menu, bàn phím chính, wizard, thẻ sửa tại chỗ
- **Hạng mục:** Tính năng
- **Thay đổi:** (1) Menu lệnh ở nút Menu theo vai trò: danh sách cơ bản cho mọi người (/start, /giaoviec, /viec, /vaitro, /help), người duyệt có thêm /choduyet và /thongke, Admin/Phó Admin thêm /danhsach, /capquyen, /thuhoi — bot tự đăng ký lại menu cho từng người khi khởi động, khi /start và khi được cấp/thu hồi quyền. (2) Bàn phím chính ghim theo vai trò hiện ngay sau /start (nút Giao việc mới, Việc của tôi/Tất cả việc, Chờ duyệt, Thống kê, Người dùng, Trợ giúp). (3) Lệnh /giaoviec và nút Giao việc mới mở luồng tạo việc 3 bước: gửi file → nhập gợi ý → chọn nơi đăng bằng nút bật/tắt → bấm Tạo việc; cách gửi thẳng ảnh + gợi ý như cũ vẫn chạy song song. (4) Thẻ việc/thẻ kênh được sửa tại chỗ khi bấm nút thao tác thay vì gửi thẻ mới. (5) Thêm lệnh /choduyet (hàng chờ duyệt) và /thongke (bản thống kê nhanh dạng chữ; biểu đồ và xuất file vẫn thuộc Giai đoạn 2).
- **Lý do:** Thực hiện Giai đoạn 1 của bản thiết kế đã duyệt.
- **Trạng thái:** Đã xong. Kiểm thử tự động 70/70 đạt (31 cũ + 8 mới, lớp mới kế thừa nên tổng số chạy là 70), bot đã khởi động lại và kiểm chứng menu lệnh đã đăng ký thật trên Telegram. Đã cập nhật README mục Danh sách lệnh.

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
