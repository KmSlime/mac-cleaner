---
name: mac-cleaner
description: Kiểm kê toàn bộ máy macOS (ứng dụng, plugin, extension, git repo, cache, log, installer, build artifact, file backup cũ, file sót lại của app đã gỡ) kèm mức an toàn, rồi dọn có kiểm soát (mặc định chỉ xem trước, xoá = chuyển vào Thùng rác, có log). Dùng khi người dùng nhờ "quét máy", "kiểm kê", "file leftover", "app đã gỡ còn sót", hoặc muốn dashboard dung lượng.
argument-hint: "[scan|clean|open] [ghi chú]"
---

Dùng CLI `mac-cleaner` (do `./install.sh` cài vào `~/.local/bin`; report dùng chung với app **Mac Cleaner**). Không thấy lệnh → nói người dùng chạy `./install.sh` trong thư mục repo.

## Quy trình
1. **Quét:** `mac-cleaner scan` → in đường dẫn report `.json/.html/.md`. Đọc report `.md` hoặc `.json`, báo bảng theo nhóm: số mục · dung lượng · an toàn / cân nhắc / kiểm kê.
2. **Rà trước khi đề xuất dọn**, nhất là nhóm `app-leftover` (nhận diện theo bundle id nên có thể nhầm):
   - app tương ứng còn cài không: `mdfind "kMDItemCFBundleIdentifier == '<bundle>*'"`, `ls /Applications`;
   - thuộc Apple/hệ thống (Podcasts, CUPS…) hoặc dùng chung (Office group container, Sparkle, Keystone) → giữ;
   - dữ liệu nhạy cảm (authenticator, ví, ngân hàng) → giữ và hỏi.
3. **Xem trước:** `mac-cleaner clean --ids <id,…> [--include-review]` (không `--yes` = chỉ preview).
4. **Chỉ dọn khi người dùng ra lệnh:** thêm `--yes`. Mục kiểm kê/bảo vệ (app, repo, plugin, backup mới nhất, dữ liệu trong `~/Library/Application Support`, LaunchAgent…) chỉ xoá khi người dùng **chỉ đích danh** → `--ids <id> --force --yes` (vẫn bị chặn cứng: SIP, thư mục gốc). Mục ngoài report (file người dùng chỉ ra) thì chạy `mac-cleaner scan` trước để có id, hoặc dùng `trash <đường dẫn>`. Mọi thứ vào Thùng rác; log ở `~/Library/Application Support/MacCleaner/logs/`. Báo dung lượng trước/sau (`df -h /System/Volumes/Data`) và mục bị `skip`.
5. **Dashboard:** `open ~/Applications/MacCleaner.app` (giao diện đọc report mới nhất; nút Quét lại / Dọn trong app).

## Không bao giờ
- Đổ Thùng rác (mục Thùng rác + `--force`), dọn mục `protected`/`info`, hay dùng `sudo` khi người dùng chưa nói rõ.
- Dán đường dẫn/danh sách app của người dùng ra ngoài máy (report là dữ liệu cá nhân).

Dọn cache/log sâu hơn ở cấp app: skill `mole`.
