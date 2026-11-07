---
name: mac-cleaner
description: Dọn dẹp, kiểm kê và theo dõi macOS an toàn — ứng dụng (gỡ sạch kèm file liên quan, bản cập nhật), plugin, extension, git repo, cache, log, rác hệ thống/trình duyệt, installer, build artifact, file backup cũ, dữ liệu app đã gỡ, file lớn/trùng, bản đồ dung lượng, cloud, mục khởi động, phần mềm bám trụ, quyền riêng tư, bảo trì — bằng một lệnh `mac-cleaner` (xoá = chuyển vào Thùng rác, có log; không phụ thuộc công cụ ngoài). Dùng khi người dùng gọi /mac-cleaner, nhờ "dọn máy", "giải phóng dung lượng", "quét file rác", "file leftover", "app đã gỡ còn sót", "gỡ app sạch", "file trùng", "file lớn", "mục khởi động", "máy chậm", hay muốn dashboard dung lượng.
argument-hint: "[scan|clean|junk|uninstall|updates|analyze|large|dupes|cloud|startup|audit|privacy|maintain|status] [ghi chú]"
---

Đối số: `$ARGUMENTS` (trống → `scan`). Một công cụ, **một quy trình**: luôn xem trước, báo bảng, chỉ xoá/đổi khi người dùng ra lệnh.

## Chọn lệnh

Mọi lệnh dưới đây **mặc định chỉ đọc / xem trước**; chỉ `--yes` mới làm thật (và hook bắt hỏi duyệt).

| Việc | Lệnh | Làm thật thế nào |
|---|---|---|
| Kiểm kê toàn máy + mọi tính năng, dashboard | `mac-cleaner scan` | — (ghi report `.json/.md/.html`) |
| Xoá **đích danh** mục trong report (cả mục tính năng) | `clean --ids <id,…>` · `clean --category <nhóm>` hoặc `<tính năng>:<kind>` | `--yes` → **Thùng rác**, log `logs/clean-*.jsonl`; mục `permanent` cần thêm `--permanent` |
| Rác hệ thống mở rộng (cache trình duyệt/app, log container, plist hỏng, firmware…) | `junk` | qua `clean --category junk:<kind> --yes` |
| Gỡ app kèm file liên quan | `uninstall <tên\|bundle id\|.app>` · `uninstall --list` (app lâu không mở) | `--yes` → app rồi file liên quan vào Thùng rác |
| Bản cập nhật app (Sparkle, Homebrew cask) + macOS | `updates [--offline]` | chỉ liệt kê, không cài |
| Bản đồ dung lượng / file lớn và cũ | `analyze [path] [--depth N]` · `large [path…] [--min 500M] [--older 180d]` | chỉ đọc — chưa nối vào `clean` (không có id trong report): người dùng tự chuyển vào Thùng rác |
| File trùng (giữ một bản mỗi nhóm) | `dupes [path…] [--min-size 5M] [--prefer DIR]` | chỉ đọc — chưa nối vào `clean` (không có id trong report): người dùng tự chuyển vào Thùng rác |
| Thư mục đồng bộ cloud: bản local vs chỉ trên cloud | `cloud` | chỉ liệt kê |
| Mục khởi động (LaunchAgent, login item) | `startup list` · `startup disable\|enable <label>` | `--yes` (ghi log `logs/startup-*.jsonl`) |
| Phần mềm bám trụ + chữ ký mã | `audit [--gatekeeper]` | chỉ liệt kê (`--gatekeeper` gọi `spctl`, có thể hỏi mạng Apple) |
| Dấu vết riêng tư (lịch sử/cookie/phiên trình duyệt, quyền TCC) | `privacy list` · `privacy reset <dịch vụ> <bundle id>` | `--yes` (`tccutil reset`, log `logs/privacy-*.jsonl`) |
| Bảo trì (QuickLook, LaunchServices, snapshot TM…) | `maintain list` · `maintain run <tác vụ>` | `--yes`; tác vụ cần `sudo` **chỉ in lệnh** để người dùng tự chạy |
| Sức khoẻ máy (ổ, RAM/swap, CPU, pin, mạng, cảnh báo) | `status` | chỉ đọc |

`mac-cleaner` do `./install.sh` của repo cài (`~/.local/bin`, gọi `python3 maccleaner.py`). Thiếu lệnh → dùng `python3 <repo>/maccleaner.py …` hoặc bảo người dùng chạy `./install.sh`; **không** tự cài công cụ dọn dẹp khác bằng `curl | bash`.
Mole (`mo`) không còn là dependency. Nếu người dùng tự cài và muốn dùng, coi như chương trình riêng: luôn `--dry-run` trước, `mo clean` **xoá hẳn** (không qua Thùng rác).

## Quy trình

1. `df -h /System/Volumes/Data` — ghi dung lượng trước.
2. **Xem trước, không bao giờ bỏ bước này:**
   - `mac-cleaner scan` → đọc report `.md`/`.json` (đường dẫn in ra) **và** bảng phân loại do hook `post-scan/10-classify` in ngay sau đó (`reports/classify-<ts>.md`).
   - Người dùng muốn quét cả hệ thống file: `python3 scripts/classify.py --report latest --fs [--root <boundary>…] --out reports` (toàn bộ từ `/` nếu không có `--root`).
   - Cần chi tiết hơn một mảng: chạy lệnh tính năng tương ứng ở bảng trên (`junk`, `large`, `dupes`, `uninstall <app>`…), tất cả đều chỉ xem trước.
3. Báo **bảng** theo 3 nhóm của classify (NÊN XOÁ / CÂN NHẮC kèm "xoá thì sao" / GIỮ): nhóm · dung lượng · mức (🟢 an toàn / 🟡 cân nhắc / 🔵 bảo vệ) · cách xoá (Thùng rác / xoá hẳn) · ghi chú. Mục tính năng đã bỏ những gì scan gốc có — vẫn kiểm trùng đường dẫn trước khi cộng tổng.
4. **Chỉ xoá khi người dùng ra lệnh**, rồi:
   - `mac-cleaner clean --ids <id,…> [--include-review] --yes`;
   - mục kiểm kê/bảo vệ (app, repo, plugin, backup mới nhất, dữ liệu trong `Application Support`, LaunchAgent…) chỉ khi người dùng **chỉ đích danh**: thêm `--force`;
   - gỡ app: `uninstall <app> --yes`; tắt mục khởi động: `startup disable <label> --yes`; reset quyền: `privacy reset <dịch vụ> <bundle id> --yes`; bảo trì: `maintain run <tác vụ> --yes` — mỗi lệnh một mục người dùng đã chỉ đích danh.
5. Đo lại `df`, báo số thật so với ước lượng, liệt kê mục `skip`/`error` và lý do.

Plugin có hook bắt hỏi duyệt trước mọi lệnh làm thật (`clean`/`uninstall`/`startup disable|enable`/`privacy reset`/`maintain run` kèm `--yes`, `mo` không `--dry-run`, đụng Thùng rác) — đó là chốt, không phải lỗi.

## Hiểu đúng trước khi đề xuất xoá

- **"Sót lại" của app đã gỡ** (bundle id không còn app): chỉ tin khi *cả* các điều sau đúng — không còn app nào có bundle id đó ở chỗ đã quét, **Spotlight cũng không thấy** (`mdfind "kMDItemCFBundleIdentifier == '<id>'"` — bắt được app cài qua Steam, ở `~/Downloads`…), thư mục **không được ghi trong 30 ngày**, không phải của Apple (`com.apple.*`, kể cả `group(s).com.apple.*`). Spotlight lỗi/quá giờ → **giữ**, không đoán.
- **Dùng chung**: Office group container, autoupdate/oneauth của Microsoft, Sparkle, Keystone — còn app nào của hãng đó thì giữ.
- **Cache đang là dữ liệu sống** (xoá là hỏng thứ khác, không chỉ phải tải lại): `~/Library/Caches/pypoetry` (virtualenv của project Poetry), `org.R-project.R` (renv), `com.apple.FontRegistry`, `com.apple.Spotlight`.
- **Cache tải lại rất lâu**: `ms-playwright*`, `JetBrains*` → cân nhắc, hỏi trước.
- **Cache của app đang chạy**: đóng app rồi mới dọn (mac-cleaner tự bỏ qua và báo `skip`).
- **Build artifact vừa dùng trong 7 ngày**: đang làm dự án đó → không đề xuất.
- **Model AI đã tải** (ollama, LM Studio, `~/.cache/huggingface`…), **bản sao lưu iOS**, **snapshot Time Machine**: chỉ liệt kê — tải lại/khôi phục rất lâu, người dùng tự quyết.
- **Mục khởi động / quyền TCC**: tắt hay reset có thể làm app ngừng hoạt động đúng; chỉ đề xuất mục hỏng/không ký/không còn app, và nói rõ hậu quả.

## Không bao giờ

- Dữ liệu trình quản lý mật khẩu, keychain, `~/.ssh`, `~/.gnupg`, ví tiền mã hoá, ứng dụng xác thực.
- Bất cứ thứ gì của **phần mềm bảo mật / quản lý thiết bị** (EDR, MDM: CrowdStrike, SentinelOne, Jamf, Kandji, Defender…) — xoá có thể bị coi là can thiệp và báo động phía bảo mật công ty. Gỡ bằng trình gỡ của hãng.
- Phần hệ thống macOS khoá bằng SIP, thư mục gốc (`/Applications`, `/Library`, `/Users/*`, `/opt/homebrew`…) — mac-cleaner chặn cứng, kể cả `--force`.
- Đổ Thùng rác hay dùng `sudo` khi người dùng chưa nói rõ (tác vụ `maintain` cần sudo chỉ in lệnh cho người dùng tự chạy). Thùng rác cần Full Disk Access cho terminal.
- Dán đường dẫn / danh sách app của người dùng ra ngoài máy (report là dữ liệu cá nhân).

## Dashboard

`open ~/Applications/MacCleaner.app` (đọc report mới nhất; quét lại, xoá từng mục, Xoá toàn bộ, khu Tính năng, terminal log + nút Mở Terminal để xem log trực tiếp trong Terminal.app). Report CLI và app dùng chung `~/Library/Application Support/MacCleaner`.
