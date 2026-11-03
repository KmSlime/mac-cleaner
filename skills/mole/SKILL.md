---
name: mole
description: Dọn dẹp/phân tích ổ đĩa macOS bằng Mole (`mo`, https://github.com/tw93/mole) — cache, log, file rác, build artifacts, installer, simulator. Dùng khi người dùng gọi /mole, nhờ "dọn máy", "giải phóng dung lượng", "quét file rác".
argument-hint: "[clean|purge|installer|analyze|status|optimize|uninstall] [ghi chú]"
---

Dọn máy macOS bằng Mole. Đối số: `$ARGUMENTS` (trống → `clean`).

`mo` phải có trong `PATH` (`./install.sh` của mac-cleaner cài sẵn: Homebrew nếu có, không thì bản phát hành đã kiểm sha256 vào `~/.local/bin`). Không thấy `mo` → nói người dùng chạy `./install.sh`, đừng tự cài bằng `curl | bash`.

## Quy trình
1. `df -h /System/Volumes/Data` ghi dung lượng trước.
2. **Luôn xem trước:** `mo <lệnh> --dry-run </dev/null 2>&1 | sed 's/\x1b\[[0-9;?]*[a-zA-Z]//g' | grep -v '^\s*$'`
   (`</dev/null` để không kẹt menu tương tác; chạy song song nhiều lệnh dry-run được.)
3. Báo bảng: nhóm · dung lượng · mức an toàn (🟢 an toàn / 🟡 cân nhắc) · ghi chú. Cộng tổng cẩn thận — tổng `mo clean` đã gồm simulator, đừng cộng trùng.
4. **Chỉ xoá khi người dùng ra lệnh**, rồi chạy thật `mo <lệnh> </dev/null`, đo lại `df`, báo số thật so với ước lượng, liệt kê mục bị bỏ qua.

## Lệnh
| Việc | Lệnh |
|---|---|
| Cache/log/rác | `mo clean` |
| node_modules, .build, Pods, .venv… | `mo purge` (xem `mo purge --paths`) |
| File .dmg/.pkg/.xip | `mo installer` (menu chọn — không chạy non-interactive được; xoá thủ công bằng `trash <file>`) |
| Phân tích dung lượng | `mo analyze`, hoặc `du -xhd1 <dir> \| sort -rh \| head` |
| Gỡ app sạch | `mo uninstall --dry-run` |
| Sức khoẻ máy | `mo status` |
| Lịch sử dọn | `mo history --json` |

Kiểm kê toàn máy có giao diện (ứng dụng, plugin, extension, git repo, file sót lại của app đã gỡ): dùng skill `mac-cleaner`.

## Loại trừ mục khỏi lần dọn
File `~/.config/mole/whitelist` (mỗi dòng 1 glob, `~` được) **thay thế toàn bộ** whitelist mặc định. Cần loại thêm mục: chép các pattern mặc định (dòng `↳` trong output dry-run) + pattern mới; nếu file trước đó không tồn tại thì xoá nó sau khi dọn xong. Xác nhận bằng dry-run có dòng `skipped (whitelist)`.

## Luôn giữ
- Dữ liệu ứng dụng và thư mục đồng bộ trong `~/Library` (ngoài cache/log), dữ liệu và model của các tool trong thư mục ẩn ở home, Keychain, `~/.ssh`.
- Thùng rác: đổ là xoá vĩnh viễn — chỉ khi người dùng nói rõ.
- Không `sudo` khi người dùng chưa cấp; Thùng rác cần Full Disk Access cho terminal.
- Chrome/app đang mở có thể khiến cache chính bị bỏ qua — nói rõ trong báo cáo.
