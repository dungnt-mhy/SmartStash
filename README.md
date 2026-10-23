# Bot Telegram Giao Việc Viết Bài

Bot Telegram phục vụ quy trình quản trị nội dung website:

**Gửi ảnh/slide + gợi ý → Muse viết bài → tạo bài nháp trên website →
gửi link duyệt → được duyệt mới đăng public lên website, Facebook, Instagram.**

Người dùng giao việc, duyệt bài và phân quyền **ngay trong Telegram**,
bằng lệnh và nút bấm. Mọi thao tác đều được bot kiểm tra quyền lại ở phía
máy chủ và ghi nhật ký (audit log) trong SQLite.

---

## 1. Tạo bot qua BotFather

1. Mở Telegram, tìm **@BotFather** và bấm Start.
2. Gửi lệnh `/newbot`.
3. Đặt **tên hiển thị** cho bot, ví dụ: `Bot Giao Việc Viết Bài`.
4. Đặt **username** cho bot — bắt buộc kết thúc bằng `bot`,
   ví dụ: `giaoviec_vietbai_bot`. Nếu bị trùng, BotFather sẽ yêu cầu đổi.
5. BotFather sẽ trả về một **token** dạng `123456789:AA...`.

> ⚠️ **Token là bí mật.** Không gửi token trong chat, không ghi vào file,
> không chia sẻ cho người khác.

## 2. Nhập token qua biểu mẫu bảo mật của Muse

Token được nhập qua **biểu mẫu bảo mật (secure form) của Muse** để lưu
vào kết nối an toàn tên `custom.telegram` — Muse chỉ dùng một *surrogate*
khi gọi `api.telegram.org`, token thật không hề xuất hiện trong code,
file cấu hình hay nhật ký.

Nói với Muse: *"nhập token Telegram bot"* và làm theo biểu mẫu hiện ra.
**Tuyệt đối không dán token vào tin nhắn chat.**

Khi chạy mô phỏng nội bộ (bot test riêng), có thể đặt biến môi trường
`TELEGRAM_BOT_TOKEN` — cách này chỉ dùng cho thử nghiệm, không dùng cho
bot thật.

Thứ tự bot lấy token khi chạy:

1. Kết nối bảo mật `custom.telegram` của Muse (surrogate).
2. Biến môi trường `TELEGRAM_BOT_TOKEN` (chỉ mô phỏng nội bộ).

## 3. Khởi động bot và nhận quyền Admin

Chạy bot (trên máy của Muse):

```bash
cd ~/workspace/telegram-bot-giao-viec
python3 bot.py
```

Sau đó, **chủ website** mở Telegram:

1. Tìm bot theo username đã tạo, bấm Start hoặc gửi `/start`.
2. Vì chưa có Admin nào, bot hiện nút **👑 Nhận quyền Admin**.
3. Bấm nút đó, rồi bấm **✅ Xác nhận nhận quyền Admin**.
4. Bạn trở thành **Admin gốc** — thao tác này được ghi vào nhật ký.
   Từ lúc này không ai khác nhận quyền Admin theo cách này được nữa.

Có sẵn mẫu systemd unit `telegram-bot-giao-viec.service` để chạy bot
thường trực (Restart=on-failure) — hiện **chưa cài/chưa bật**, sẽ cài
khi chính thức vận hành.

## 4. Danh sách lệnh

Bot có 3 lớp giao diện (theo bản thiết kế trong `docs/`): **menu lệnh**
ở nút Menu cạnh khung chat — danh sách lệnh theo vai trò, người duyệt
và Admin thấy thêm lệnh quản trị; **bàn phím chính** ghim dưới khung
chat hiện ngay sau `/start`, mỗi vai trò một bộ nút; và **nút inline**
trên thẻ việc/thẻ kênh. Khi bấm một nút thao tác trên thẻ, chính thẻ đó
được sửa tại chỗ cho khớp trạng thái mới (thông báo cho người khác vẫn
là tin nhắn mới như trước).

| Lệnh | Ai dùng | Tác dụng |
|---|---|---|
| `/start` | Mọi người | Đăng ký (trạng thái chờ cấp quyền); người đầu tiên có thể nhận quyền Admin gốc; hiện bàn phím chính theo vai trò |
| `/giaoviec` | Người đã được cấp quyền tạo việc | Giao việc mới theo 3 bước hướng dẫn: gửi file → nhập gợi ý → chọn nơi đăng → bấm Tạo việc |
| `/vaitro` | Mọi người | Xem danh sách vai trò và quyền của từng vai trò |
| `/danhsach` | Admin, Phó Admin | Xem danh sách người dùng + vai trò |
| `/capquyen` | Admin, Phó Admin | Cấp quyền: chọn người → chọn vai trò → bấm Xác nhận mới áp dụng |
| `/thuhoi` | Admin, Phó Admin | Thu hồi quyền: chọn người → bấm Xác nhận |
| `/viec` | Người đã được cấp quyền | Xem việc: Admin/Phó Admin/Biên tập viên xem tất cả, người viết/cộng tác viên xem việc của mình |
| `/choduyet` | Admin, Phó Admin, Biên tập viên | Xem các việc đang ở trạng thái chờ duyệt (PENDING) |
| `/thongke` | Admin, Phó Admin, Biên tập viên | Thống kê nhanh: số việc theo trạng thái, số đầu ra theo kênh/trạng thái |

### Giao việc (tạo bài)

Cách mới (khuyên dùng): bấm **📝 Giao việc mới** trên bàn phím chính
(hoặc lệnh `/giaoviec`) rồi làm theo 3 bước bot hướng dẫn. Trong lúc
giao việc, bàn phím tạm đổi thành các nút **✅ Xong** / **❌ Huỷ** luôn
ghim ở phía dưới (khỏi phải kéo lên tìm nút trên tin nhắn cũ); ở bước
gửi file, bot chỉ giữ **một tin đếm số file và sửa số tại chỗ** khi có
file mới — không gửi thêm tin xác nhận hay nút mới cho mỗi ảnh; gõ chữ
`xong` cũng chuyển bước được; tạo việc xong bàn phím chính tự quay
lại. Cách cũ vẫn dùng được:

1. Gửi **ảnh hoặc tài liệu/slide** cho bot (chú thích/caption của ảnh
   được lưu làm gợi ý).
2. Gửi thêm ảnh/tài liệu nếu cần.
3. Nhắn **một tin nhắn văn bản** (gợi ý, ví dụ:
   *"hội thảo nông nghiệp thông minh xã Chân Mộng, Phú Thọ"*) để chốt —
   bot tạo việc với mã `CV-0001`, `CV-0002`, … và gửi lại **thẻ việc**.
4. Thẻ việc ghi rõ **"✍️ Bản nháp: chưa có — đang chờ Muse viết bài"**.
   Muse viết bản nháp cho từng kênh rồi gắn vào **thẻ kênh** của kênh
   đó; bot gửi thẻ kênh kèm bản nháp cho **người tạo và người duyệt**
   ngay trong Telegram. Người duyệt **không** còn nhận thẻ việc rỗng
   lúc mới tạo việc.

Nhân viên viết bài có thể gửi thẳng bản thảo dạng văn bản (không cần ảnh)
để tạo việc. Cộng tác viên **bắt buộc** gửi ảnh/tư liệu trước.

### Duyệt và đăng bài (trên thẻ kênh)

Từ ngày 2026-10-23, việc duyệt/đăng diễn ra **trên thẻ kênh** — nơi
có bản nháp thật. Thẻ việc không còn nút Gửi duyệt/Duyệt/Đăng (bấm vào
nút cũ trên thẻ đã gửi từ trước, bot sẽ hướng dẫn mở thẻ kênh).

Thẻ việc luôn hiện: **Người tạo**, **Thời gian tạo** (giờ Việt Nam),
nội dung/gợi ý, số file đính kèm, **Nơi đăng**, dòng tiến trình
**✍️ Bản nháp: đã có X/Y kênh** và tóm tắt trạng thái từng kênh.
Trạng thái của thẻ việc được **tự suy ra từ các thẻ kênh** (có kênh
chờ duyệt → việc chờ duyệt; mọi kênh đã đăng → việc đã đăng; …).

Trên **thẻ kênh** của từng kênh (Website / Facebook / Instagram):

- **📤 Gửi duyệt**: người tạo việc bấm trên thẻ kênh. **Chưa có bản
  nháp thì không gửi duyệt được** — bot báo "kênh này chưa có bản
  nháp" thay vì cho duyệt một cái vỏ rỗng. Có nháp → kênh chuyển sang
  **Chờ duyệt (PENDING)** và bot gửi thẻ kênh cho người duyệt.
- **✅ Duyệt**: Biên tập viên trở lên → kênh chuyển sang
  **Đã duyệt, chờ đăng (APPROVED_WAITING)**.
- **↩️ Trả lại**: Biên tập viên trở lên bấm nút, rồi nhắn **lý do trả
  lại** trong tin nhắn tiếp theo → thẻ kênh quay về người tạo kèm lý do.
- **🚀 Đăng**: chỉ **Admin**, hoặc Phó Admin/Biên tập viên đã được
  Admin bật **quyền đăng (can_publish)**. Người không có quyền bấm vào
  sẽ bị từ chối và lần bấm đó được ghi vào nhật ký.
- **↩️ Thu hồi**: trên thẻ kênh đã đăng, chỉ người **có quyền đăng**
  mới xác nhận thu hồi được.
- **📋 Thẻ các kênh** (trên thẻ việc): mở lại các thẻ kênh để thao tác.

### Chỉnh sửa, chọn nơi đăng, thu hồi (trên thẻ việc)

Việc chỉnh sửa thực hiện **ngay trên thẻ việc trong Telegram**, không
dùng link ngoài:

- **✏️ Chỉnh sửa**: chỉ **người tạo việc**, khi việc đang là Nháp hoặc
  Bị trả lại. Bấm nút → nhắn **gợi ý mới** trong tin nhắn tiếp theo,
  gợi ý mới thay toàn bộ gợi ý cũ. Người khác bấm vào sẽ bị từ chối và
  được ghi nhật ký (`denied_edit_attempt`).
- **Chọn nơi đăng**: ba nút **✅/⬜ Website • Facebook • Instagram**
  trên thẻ việc (mặc định chọn cả ba). Bấm để bật/tắt từng nơi.
  *Người tạo việc* đổi được khi việc đang Nháp/Bị trả lại; *người duyệt
  (Biên tập viên trở lên)* đổi được khi việc Chờ duyệt/Đã duyệt chờ
  đăng. **Không thể bỏ chọn nơi đăng cuối cùng** — việc phải đăng ở ít
  nhất một nơi.
- **🗑 Thu hồi việc**: khi việc **chưa đăng** (Nháp/Chờ duyệt/Bị trả
  lại), người tạo việc — hoặc Admin — bấm nút rồi bấm **Xác nhận thu
  hồi**. Việc chuyển sang **Đã hủy (CANCELLED)**, người duyệt được bot
  thông báo, và việc đã hủy không gửi duyệt lại được, thẻ việc không
  còn nút thao tác. Thu hồi **bài đã đăng** làm trên **thẻ kênh** của
  kênh đó (nút ↩️ Thu hồi).

Admin bật/tắt quyền đăng cho Phó Admin/Biên tập viên ngay trong luồng
`/capquyen`, bằng nút **🔀 Bật/tắt quyền đăng bài** sau khi cấp quyền.

## 5. Mô hình việc gốc và thẻ đầu ra theo kênh (Phương án A)

Từ ngày 2026-10-22, bot áp dụng **Phương án A — mỗi kênh một thẻ
duyệt/đăng riêng**:

- **Việc gốc (thẻ việc)** chỉ là **hồ sơ tư liệu**: ảnh/slide, gợi ý,
  người tạo, thời gian tạo và danh sách **nơi đăng** đã chọn. Việc gốc
  **không còn vòng đời duyệt/đăng riêng** (từ 2026-10-23): trạng thái
  của nó là bản tóm tắt tự suy ra từ các đầu ra, thẻ việc chỉ còn các
  thao tác quản trị hồ sơ (chỉnh sửa gợi ý, chọn nơi đăng, thu hồi
  việc).
- **Mỗi kênh đăng là một đầu ra (output) riêng**, có bản ghi riêng
  trong bảng `outputs`, có **thẻ kênh riêng** trong Telegram và **vòng
  đời riêng**:

```
DRAFT (nháp) → PENDING (chờ duyệt) → APPROVED_WAITING (đã duyệt, chờ đăng)
             → PUBLISHED (đã đăng)
PENDING → REJECTED (trả lại kèm lý do) → gửi duyệt lại
PUBLISHED → RETRACTED (người có quyền đăng thu hồi)
DRAFT / PENDING → CANCELLED (bỏ chọn kênh, hoặc việc gốc bị thu hồi)
```

- Đầu ra được **tự tạo khi tạo việc** cho từng nơi đăng đã chọn. Khi
  **bật/tắt nơi đăng** trên thẻ việc, đầu ra được đồng bộ theo: tắt một
  kênh thì đầu ra của kênh đó đang ở Nháp/Chờ duyệt bị **hủy
  (CANCELLED)**; bật lại thì đầu ra được **tạo lại ở trạng thái Nháp**.
  Đầu ra đã sang Đã duyệt chờ đăng/Đã đăng/Đã thu hồi thì **không bị
  đụng vào** khi đổi nơi đăng.
- Trên thẻ việc có mục **"Đầu ra theo kênh"** tóm tắt trạng thái từng
  kênh và nút **📋 Thẻ các kênh**: bấm vào, bot gửi **mỗi kênh một thẻ
  riêng** kèm nút theo quyền của người xem — **📤 Gửi duyệt** (người
  tạo), **✅ Duyệt / ↩️ Trả lại** kèm lý do (người duyệt), **🚀 Đăng**
  và **↩️ Thu hồi** (người có quyền đăng). Nhờ vậy có thể **duyệt và
  đăng Facebook trước, Website sau** (hoặc ngược lại) mà không ảnh
  hưởng nhau; cộng tác viên bấm Đăng trên thẻ kênh vẫn bị từ chối và
  ghi nhật ký.
- Khi **thu hồi việc gốc**, các đầu ra đang ở Nháp/Chờ duyệt của việc
  đó cũng bị hủy theo. Thu hồi bài đã đăng chỉ làm **từng kênh** trên
  thẻ kênh của kênh đó.

Các kênh được định nghĩa sẵn trong `CHANNELS` (ở `storage.py`) theo
kiểu dữ liệu danh sách: `website` (bài báo), `facebook` (post),
`instagram` (carousel). Từ Giai đoạn 2 (2026-10-23), hai kênh
**`video_vertical` (🎬 Video dọc 9:16)** và **`video_horizontal`
(🎞 Video ngang 16:9)** đã được **kích hoạt**: chọn thêm trong wizard
Bước 3 hoặc trên thẻ việc (không nằm trong mặc định 3 kênh chữ), mỗi
kênh có thẻ kênh riêng; Muse dựng video nháp từ tư liệu gốc rồi gắn
bằng `botctl attach-output-video` — thẻ kênh được gửi **kèm video**
để duyệt ngay trong Telegram. Đăng thật lên YouTube/TikTok là
Giai đoạn 3; hiện nút Đăng chỉ ghi lệnh đăng như các kênh khác.

`/thongke` (Giai đoạn 2) có chuyển kỳ Hôm nay / Tuần này / Tháng này /
Tất cả bằng nút inline, đếm việc và đầu ra theo trạng thái + theo
kênh (gồm cả kênh video) + theo người tạo, kèm nút **🖼 Biểu đồ**
(PNG), **📥 Xuất CSV** và **📗 Xuất Excel** theo đúng kỳ đang xem.
Mỗi ngày lúc **20:00** bot tự gửi tin tổng kết ngày cho các Admin
(số việc tạo hôm nay, số đầu ra đã đăng, đang chờ duyệt/chờ đăng) —
mỗi ngày chỉ gửi một lần, kể cả khi bot khởi động lại.

## 6. Bảng vai trò

| Vai trò | Quyền |
|---|---|
| **Admin** (chủ website) | Toàn quyền: cấp/thu hồi quyền trong Telegram, duyệt cuối, đăng public, xem nhật ký |
| **Phó Admin** | Cấp/thu hồi quyền cho Nhân viên viết bài & Cộng tác viên; duyệt nội dung; chỉ đăng bài khi được bật `can_publish` |
| **Biên tập viên** | Sửa/duyệt nội dung, trả lại kèm lý do; chỉ đăng bài khi được bật `can_publish` |
| **Nhân viên viết bài** | Gửi ảnh/slide/gợi ý hoặc bản thảo của mình, tạo bài nháp, sửa bài của mình, gửi duyệt. Không duyệt bài người khác, không đăng |
| **Cộng tác viên** | **Chỉ** gửi ảnh/tư liệu kèm gợi ý ngắn để Muse làm bài. Không duyệt, **không public** |

Trạng thái của từng đầu ra theo kênh (trạng thái thẻ việc là bản
tóm tắt suy ra từ các đầu ra này):

```
DRAFT (nháp) → PENDING (chờ duyệt) → APPROVED_WAITING (đã duyệt, chờ đăng)
             → PUBLISHED (đã đăng)
PENDING → REJECTED (trả lại kèm lý do) → gửi duyệt lại
DRAFT / PENDING → CANCELLED (bỏ chọn kênh, hoặc việc gốc bị thu hồi)
PUBLISHED → RETRACTED (người có quyền đăng thu hồi trên thẻ kênh)
```

## 7. Công cụ nội bộ cho Muse — `botctl.py`

```bash
# Liệt kê các việc Muse cần làm bài (DRAFT/PENDING) dạng JSON
# (mỗi việc có thêm trường destinations — danh sách nơi đăng đã chọn)
python3 botctl.py list-needs-article

# Gắn link bài nháp trên website vào việc, đồng thời báo cho
# người tạo + người duyệt trong Telegram
python3 botctl.py attach-draft CV-0001 https://website/vi-du/nhap/1

# Đánh dấu đã đăng và gắn link public, báo cho người tạo + Admin
python3 botctl.py set-published CV-0001 https://website/vi-du/bai-1

# In JSON các đầu ra theo kênh của một việc
python3 botctl.py list-outputs CV-0001

# Gắn bản nháp cho riêng đầu ra Facebook (trạng thái đầu ra giữ nguyên
# DRAFT) và gửi thẻ kênh Facebook cho người tạo + người duyệt
python3 botctl.py attach-output-draft CV-0001 facebook https://website/vi-du/nhap/fb

# Kèm --file <file nháp .md>: bản nháp được đăng thành một trang
# telegra.ph và link trang trở thành tham chiếu nháp trên thẻ kênh —
# trong Telegram chỉ có thẻ kênh gọn gàng kèm 1 link, nhấn là mở xem
# toàn bộ bản nháp đã định dạng (Instant View). Gắn lại bản nháp đã
# sửa sẽ cập nhật đúng trang cũ, link không đổi.
python3 botctl.py attach-output-draft CV-0001 website CV-0001-website.md --file ./CV-0001-website.md

# Đánh dấu riêng đầu ra Facebook đã đăng, gắn link public và gửi
# thẻ kênh cho người tạo + Admin
python3 botctl.py set-output-published CV-0001 facebook https://facebook.com/post/1
```

Dữ liệu nằm ở `data/bot.db` (SQLite: người dùng, việc, nhật ký). File
ảnh/tài liệu của mỗi việc, khi bot chạy với API thật, được tải về
`data/files/<mã việc>/` qua `getFile`; mã việc vẫn lưu `file_id` gốc.

## 8. Hạn chế hiện tại

- **Website chưa được kết nối** (chưa có địa chỉ website): thao tác
  **Đăng bài** hiện mới đánh dấu trạng thái `PUBLISHED` và gửi thông báo
  trong Telegram. Việc đăng thật lên website (và chuyển thể lên Facebook,
  Instagram) sẽ được nối khi có địa chỉ website và tài khoản quản trị.
- Tương tự, **Thu hồi bài đã đăng** hiện cũng chỉ đổi trạng thái trong
  bot (`RETRACTED`) và thông báo cho người liên quan — **chưa gỡ bài
  thật** trên website/Facebook/Instagram. Việc gỡ bài thật sẽ được nối
  cùng lúc với khâu đăng bài, khi website được kết nối.
- Hai kênh video (`video_vertical`, `video_horizontal`) đã **kích
  hoạt từ Giai đoạn 2**: có đầu ra và thẻ kênh riêng, Muse dựng video
  nháp và gắn bằng `botctl attach-output-video` để duyệt ngay trong
  Telegram. **Chưa có khâu đăng thật lên YouTube/TikTok** — đó là
  Giai đoạn 3; nút Đăng hiện chỉ ghi lệnh đăng.
- Bot hiện chạy bằng long polling trên máy của Muse, chưa cài service
  thường trực.
- Chạy kiểm thử: `python3 -m unittest discover -s tests -v` — toàn bộ
  test dùng transport giả lập, không gọi Telegram thật, không cần token.

## 9. Cấu trúc thư mục

```
telegram-bot-giao-viec/
├── bot.py                    # Điểm chạy chính (long polling)
├── botctl.py                 # CLI nội bộ cho Muse
├── handlers.py               # Lệnh, nút bấm, tiếp nhận ảnh/tài liệu
├── roles.py                  # Định nghĩa vai trò + kiểm tra quyền
├── storage.py                # SQLite: users, tasks, outputs, audit_log, ...
├── telegram_client.py        # Client Telegram API (token qua kết nối bảo mật)
├── telegram-bot-giao-viec.service  # Mẫu systemd unit (chưa cài)
├── tests/test_bot.py         # Unit test (transport giả lập)
└── data/                     # bot.db và files/<mã việc>/ (tự tạo khi chạy)
```
