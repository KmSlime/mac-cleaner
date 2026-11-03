---
name: mac-cleaner
description: Dọn dẹp và kiểm kê macOS an toàn — ứng dụng, plugin, extension, git repo, cache, log, installer, build artifact, file backup cũ, dữ liệu của app đã gỡ — bằng lệnh `mac-cleaner` (xoá = chuyển vào Thùng rác, có log) và Mole (`mo`) cho phần dọn sâu. Dùng khi người dùng gọi /mac-cleaner hoặc /mole, nhờ "dọn máy", "giải phóng dung lượng", "quét file rác", "file leftover", "app đã gỡ còn sót", "gỡ app sạch", hay muốn dashboard dung lượng.
argument-hint: "[scan|clean|deep|purge|uninstall|installer|analyze|status] [ghi chú]"
---

Đối số: `$ARGUMENTS` (trống → `scan`). Hai công cụ, **một quy trình**: luôn xem trước, báo bảng, chỉ xoá khi người dùng ra lệnh.

## Chọn công cụ

| Việc | Công cụ | Xoá thế nào |
|---|---|---|
| Kiểm kê toàn máy, dashboard, xoá **đích danh** từng mục | `mac-cleaner scan` / `clean --ids` | **Thùng rác** (khôi phục được), log `logs/clean-*.jsonl` |
| Dọn sâu cache/log/temp của macOS + app (15 nhóm) | `mo clean` | **Xoá hẳn** (`rm`), không qua Thùng rác |
| node_modules, .build, Pods, .venv… cũ | `mo purge` (hoặc mục `build-artifact` của mac-cleaner) | `mo`: xoá hẳn · mac-cleaner: Thùng rác |
| Gỡ app kèm file sót | `mo uninstall <tên>` | **Thùng rác** (mặc định của Mole cho uninstall) |
| .dmg/.pkg/.xip/.iso | `mo installer` (menu) hoặc mục `installer` của mac-cleaner | |
| Xem dung lượng / sức khoẻ máy | `mo analyze`, `mo status`, `du -xhd1 <dir> \| sort -rh \| head` | — |

`mac-cleaner` và `mo` do `./install.sh` của repo cài (`~/.local/bin`; Mole qua Homebrew hoặc bản phát hành đã kiểm sha256). Thiếu lệnh → bảo người dùng chạy `./install.sh`, **không** tự cài bằng `curl | bash`.
Muốn hoàn tác được thì ưu tiên mac-cleaner; `mo clean` chỉ dùng cho cache thật sự tự tạo lại.

## Quy trình

1. `df -h /System/Volumes/Data` — ghi dung lượng trước.
2. **Xem trước, không bao giờ bỏ bước này:**
   - `mac-cleaner scan` → đọc report `.md`/`.json` (đường dẫn in ra).
   - `mo <lệnh> --dry-run </dev/null 2>&1 | sed 's/\x1b\[[0-9;?]*[a-zA-Z]//g' | grep -v '^\s*$'` (`</dev/null` để không kẹt menu; nhiều lệnh dry-run chạy song song được).
3. Báo **bảng**: nhóm · dung lượng · mức (🟢 an toàn / 🟡 cân nhắc / 🔵 bảo vệ) · cách xoá (Thùng rác / xoá hẳn) · ghi chú. Tổng `mo clean` đã gồm simulator — đừng cộng trùng với mac-cleaner.
4. **Chỉ xoá khi người dùng ra lệnh**, rồi:
   - `mac-cleaner clean --ids <id,…> [--include-review] --yes`;
   - mục kiểm kê/bảo vệ (app, repo, plugin, backup mới nhất, dữ liệu trong `Application Support`, LaunchAgent…) chỉ khi người dùng **chỉ đích danh**: thêm `--force`;
   - hoặc `mo <lệnh> </dev/null` (`mo purge --yes` để chạy không tương tác).
5. Đo lại `df`, báo số thật so với ước lượng, liệt kê mục `skip`/`error` và lý do.

Plugin có hook bắt hỏi duyệt trước mọi lệnh xoá thật (`--yes`, `mo` không `--dry-run`, đụng Thùng rác) — đó là chốt, không phải lỗi.

## Hiểu đúng trước khi đề xuất xoá

- **"Sót lại" của app đã gỡ** (bundle id không còn app): chỉ tin khi *cả* các điều sau đúng — không còn app nào có bundle id đó ở chỗ đã quét, **Spotlight cũng không thấy** (`mdfind "kMDItemCFBundleIdentifier == '<id>'"` — bắt được app cài qua Steam, ở `~/Downloads`…), thư mục **không được ghi trong 30 ngày**, không phải của Apple (`com.apple.*`, kể cả `group(s).com.apple.*`). Spotlight lỗi/quá giờ → **giữ**, không đoán.
- **Dùng chung**: Office group container, autoupdate/oneauth của Microsoft, Sparkle, Keystone — còn app nào của hãng đó thì giữ.
- **Cache đang là dữ liệu sống** (xoá là hỏng thứ khác, không chỉ phải tải lại): `~/Library/Caches/pypoetry` (virtualenv của project Poetry), `org.R-project.R` (renv), `com.apple.FontRegistry`, `com.apple.Spotlight`.
- **Cache tải lại rất lâu**: `ms-playwright*`, `JetBrains*` → cân nhắc, hỏi trước.
- **Cache của app đang chạy**: đóng app rồi mới dọn (mac-cleaner tự bỏ qua và báo `skip`).
- **Build artifact vừa dùng trong 7 ngày**: đang làm dự án đó → không đề xuất.
- `mo clean` có 15 nhóm: System (cần sudo) · User essentials · App caches · Browsers · Cloud & Office · Developer tools · Apps & utilities · Virtualization · Application Support · App leftovers · Apple Silicon · Device backups & firmware · Time Machine · Large files · Project artifacts. Nhóm "Large files" và "Project artifacts" chỉ là gợi ý, không tự xoá.

## Không bao giờ

- Dữ liệu trình quản lý mật khẩu, keychain, `~/.ssh`, `~/.gnupg`, ví tiền mã hoá, ứng dụng xác thực.
- Bất cứ thứ gì của **phần mềm bảo mật / quản lý thiết bị** (EDR, MDM: CrowdStrike, SentinelOne, Jamf, Kandji, Defender…) — xoá có thể bị coi là can thiệp và báo động phía bảo mật công ty. Gỡ bằng trình gỡ của hãng.
- Phần hệ thống macOS khoá bằng SIP, thư mục gốc (`/Applications`, `/Library`, `/Users/*`, `/opt/homebrew`…) — mac-cleaner chặn cứng, kể cả `--force`.
- Đổ Thùng rác, dùng `sudo`, hay `mo clean` nhóm System khi người dùng chưa nói rõ. Thùng rác cần Full Disk Access cho terminal.
- Dán đường dẫn / danh sách app của người dùng ra ngoài máy (report là dữ liệu cá nhân).

## Loại trừ mục khỏi `mo clean`

`~/.config/mole/whitelist` (mỗi dòng một glob, `~` được) **thay thế** whitelist mặc định của Mole; các mục an toàn bắt buộc của Mole vẫn luôn được gộp vào. Muốn loại thêm: chép các dòng mặc định (dòng `↳` trong output dry-run) + dòng mới; nếu trước đó chưa có file thì xoá file sau khi dọn xong. Xác nhận bằng dry-run có `skipped (whitelist)`.

## Dashboard

`open ~/Applications/MacCleaner.app` (đọc report mới nhất; quét lại, xoá từng mục, Xoá toàn bộ, terminal log). Report CLI và app dùng chung `~/Library/Application Support/MacCleaner`.
