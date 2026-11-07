# Đối chiếu tính năng với CleanMyMac

Mục tiêu: mac-cleaner tự làm được **chức năng** tương đương một bộ dọn máy thương mại phổ biến (CleanMyMac của MacPaw,
bản hiện hành) và bỏ phụ thuộc vào Mole. Tài liệu này **chỉ mô tả chức năng bằng lời của dự án**. Không dùng tên module,
văn bản, giao diện, icon hay tài sản của MacPaw. Ràng buộc của dự án: Python stdlib + app SwiftUI, chạy local, không `sudo`.

Nghiên cứu ngày 2026-11-07. Ký hiệu: ✅ có · ◐ một phần · ✗ chưa. "Không sudo": ✅ làm được · ◐ làm được một phần hoặc
cần quyền TCC (Full Disk Access = FDA, Automation) · ✗ cần root. Rủi ro: T thấp · V vừa · C cao.
Dòng có ghi **(đã kiểm)** là hành vi lệnh đã chạy thử, không sudo, trên macOS 26. Các dòng còn lại là cơ chế khả dĩ, chưa kiểm.

## Nguồn

| Mã | Nguồn | Ghi chú |
|---|---|---|
| S1 | https://cleanmymac.macpaw.com/ | trang người dùng đưa; nội dung render bằng JS, công cụ fetch chỉ lấy được tiêu đề |
| S2 | https://macpaw.com/support/cleanmymac/knowledgebase/feature-plans | danh sách đầy đủ module, tính năng con, khác biệt giữa bản App Store và bản tải trực tiếp |
| S3 | https://macpaw.com/news/introducing-new-cleanmymac | công bố bản mới (2024-10, cập nhật 2025-08): 6 module chính, hơn 25 công cụ |
| S4 | https://macpaw.com/support/cleanmymac/knowledgebase/my-tools | 21 công cụ lẻ |
| S5 | https://macpaw.com/support/cleanmymac/knowledgebase/uninstaller | nhóm app: chưa mở 6 tháng, theo nguồn cài, theo hãng, plug-in |
| S6 | https://macpaw.com/cleanmymac | trang sản phẩm: dọn cloud (file lớn, bản local trùng) |
| S7 | https://macpaw.com/support/cleanmymac-3/knowledgebase/cleanmymac-3-application-overview | bản cũ: huỷ file, quản lý extension (bản hiện hành không còn liệt kê) |
| R1 | https://thesweetbits.com/tools/cleanmymac/ | review độc lập, 2026-04: dọn cloud có từ 2025-07, 3 mức quét malware, menu bar |
| R2 | https://allaboutcookies.org/cleanmymac-x-review | review độc lập, 2026-04, macOS Tahoe: quét malware không bắt được file thử EICAR, không có lịch quét |

## 1. Bảng tính năng

### 1a. Dọn rác hệ thống, Thùng rác, mail/ảnh/nhạc

| Chức năng | Cơ chế khả dĩ trên macOS | Không sudo | Rủi ro | mac-cleaner | Nguồn |
|---|---|---|---|---|---|
| Cache người dùng | `~/Library/Caches/*`, `~/Library/Containers/*/Data/Library/Caches` | ✅ | T | ✅ | S2 |
| Log người dùng | `~/Library/Logs/*`, `~/Library/Containers/*/Data/Library/Logs` | ✅ | T | ✅ (chưa có phần Containers) | S2 |
| Cache, log hệ thống | `/Library/Caches`, `/Library/Logs` (phần đọc được); `/private/var/log` | ◐ (phần của root thì ✗) | V | ◐ | S2 |
| Rác công cụ lập trình | DerivedData, DeviceSupport, Archives, `xcrun simctl delete unavailable`, cache npm/pip/gradle/cargo/go | ✅ | T–V | ✅ | S2 |
| Cache trình duyệt | thư mục `Cache`, `Code Cache`, `GPUCache` trong profile Chromium/Firefox; cache Safari cần FDA | ◐ | V (bỏ qua khi trình duyệt đang chạy) | ◐ (chỉ phần nằm trong `~/Library/Caches`) | S2 |
| Rác AI: cache, log, model local | `~/.cache/huggingface`, thư mục model của các runtime LLM local, log của chúng | ✅ | V (model tải lại rất lâu) | ◐ (`~/.cache/*` để cân nhắc, model đang được bảo vệ) | S2, S4 |
| Mục khởi động hỏng | LaunchAgent có `Program`/`ProgramArguments[0]` không tồn tại; `sfltool dumpbtm` | ✅ cho `~/Library/LaunchAgents`; `sfltool dumpbtm` chạy được không sudo **(đã kiểm)** | V | ◐ (LaunchAgent mồ côi) | S2 |
| File cấu hình hỏng | `~/Library/Preferences/*.plist` mà `plistlib` không đọc được | ✅ | T–V | ✗ (chỉ có cấu hình của app đã gỡ) | S2 |
| Bản sao lưu thiết bị iOS | `~/Library/Application Support/MobileSync/Backup/*`; đọc `Info.plist` của từng bản để lấy tên máy, ngày | ◐ (FDA) | C (mất bản sao lưu duy nhất) | ✗ | S2 |
| Phiên bản tài liệu | `/.DocumentRevisions-V100` | ✗ | C | ✗ | S2 |
| Tài khoản người dùng đã xoá | `/Users/Deleted Users` | ✗ | C | ✗ | S2 |
| File ngôn ngữ thừa | `*.lproj` trong bundle app | ◐ (ghi được nếu app thuộc user) | **C: làm hỏng chữ ký mã, app có thể không mở được** | ✗ | S2 |
| Gọt binary đa kiến trúc | `lipo -thin` trên Mach-O | ◐ | **C: hỏng chữ ký mã** | ✗ | S2 |
| Đổ mọi Thùng rác | `~/.Trash` (cần FDA để liệt kê), `/Volumes/*/.Trashes/<uid>`; đổ qua Finder (Apple Events) | ◐ (FDA + Automation) | C (vĩnh viễn) | ◐ (chỉ `~/.Trash`) | S2, S4 |
| File đính kèm mail lưu local | `~/Library/Containers/com.apple.mail/Data/Library/Mail Downloads`, phần đính kèm trong `~/Library/Mail` | ◐ (FDA) | C (sửa kho của Mail có thể làm hỏng hộp thư) | ✗ | S2, S4 |
| Rác thư viện ảnh, nhạc (bản cũ) | cache dựng lại của app ảnh/nhạc; **không** đụng bên trong `*.photoslibrary` | ◐ | C | ✗ | S7 (bản hiện hành không còn liệt kê) |

### 1b. Bảo vệ: malware và riêng tư

| Chức năng | Cơ chế khả dĩ trên macOS | Không sudo | Rủi ro | mac-cleaner | Nguồn |
|---|---|---|---|---|---|
| Quét và gỡ malware | cần CSDL chữ ký độc quyền. Thay thế: kiểm kê cơ chế bám trụ (LaunchAgents/Daemons, login items, profile cấu hình, extension trình duyệt), `codesign -dv`, `spctl --assess`, cờ quarantine | ◐ (kiểm kê được, phát hiện thật thì không) | C nếu tự xoá | ✗ | S2, S4, R2 |
| Giám sát thời gian thực | theo dõi file mới ở Downloads/LaunchAgents bằng FSEvents (Swift) | ◐ | V | ✗ | R1 |
| Xoá lịch sử, cookie trình duyệt | file SQLite trong profile Chromium/Firefox; Safari cần FDA. Chỉ khi trình duyệt đã đóng | ◐ | C (mất đăng nhập) | ✗ | S2 |
| Danh sách mục gần đây | `~/Library/Application Support/com.apple.sharedfilelist/*.sfl*` | ✅ | V | ✗ | S2 |
| Mạng Wi-Fi đã lưu | `networksetup -listpreferredwirelessnetworks`; xoá cần admin | ◐ (liệt kê) | V | ✗ | S2 |
| Quyền của ứng dụng (camera, mic, file…) | đọc `TCC.db` cần FDA; thu hồi bằng `tccutil reset <dịch vụ> <bundle id>`; mở thẳng pane Cài đặt bằng URL `x-apple.systempreferences:` | ◐ | V | ✗ | S2, S4 |

### 1c. Hiệu năng: tác vụ bảo trì, mục khởi động và chạy nền

| Chức năng | Cơ chế khả dĩ trên macOS | Không sudo | Rủi ro | mac-cleaner | Nguồn |
|---|---|---|---|---|---|
| Xoá cache DNS | `dscacheutil -flushcache` + gửi HUP cho `mDNSResponder` | ✗ | T | ✗ | S2 |
| Giải phóng dung lượng purgeable | phần lớn là snapshot local: `tmutil listlocalsnapshots /`, `tmutil thinlocalsnapshots / <bytes> <1-4>` | ◐ (liệt kê **(đã kiểm)**; thin không sudo: chưa kiểm) | V | ✗ | S2 |
| Giải phóng RAM | `purge` | ✗ | T (lợi ích ngắn hạn) | ✗ | S2 |
| Chạy script bảo trì định kỳ | `periodic daily weekly monthly` | ✗ | T | ✗ | S2 |
| Đánh chỉ mục lại Spotlight | `mdutil -E /` cần root; không sudo: `mdimport <thư mục>` | ◐ | T | ✗ | S2 |
| Sửa quyền ổ đĩa | lỗi thời từ khi có SIP | — | — | không làm | S2 |
| Tăng tốc Mail | `VACUUM` CSDL index của Mail khi Mail đã đóng | ◐ (FDA) | V | ✗ | S2 |
| Xoá snapshot Time Machine local | `tmutil deletelocalsnapshots <ngày>` | ◐ (chưa kiểm không sudo) | V | ✗ | S2, S4 |
| Mục khởi động, mục chạy nền | `sfltool dumpbtm` (đã kiểm), `~/Library/LaunchAgents`, `/Library/Launch{Agents,Daemons}` (đọc được), `launchctl print gui/$UID`; tắt agent của user: `launchctl bootout` + `launchctl disable gui/$UID/<label>` | ◐ (agent của user ✅, daemon hệ thống ✗) | V | ◐ | S2, S4 |

### 1d. Ứng dụng

| Chức năng | Cơ chế khả dĩ trên macOS | Không sudo | Rủi ro | mac-cleaner | Nguồn |
|---|---|---|---|---|---|
| Gỡ app kèm file liên quan | bundle id từ `Info.plist` → tìm trong `~/Library/{Application Support, Caches, Containers, Group Containers, Preferences, Saved Application State, HTTPStorages, WebKit, LaunchAgents, Logs}`; `pkgutil --pkgs`/`--files` cho app cài bằng `.pkg` | ◐ (app thuộc root cần admin) | V | ◐ (đưa app vào Thùng rác; tìm đồ sót riêng) | S2, S5 |
| App lâu không mở | `mdls -name kMDItemLastUsedDate`; nhóm theo nguồn cài (App Store có `_MASReceipt`) và theo hãng (tiền tố bundle id) | ✅ | T (chỉ liệt kê) | ✗ | S5 |
| Cập nhật app | Sparkle: `SUFeedURL` trong `Info.plist` → tải appcast, so phiên bản; Homebrew: `brew outdated --cask`; macOS: `softwareupdate -l` | ✅ (liệt kê) | V nếu tự cài | ✗ | S2, S3 |
| Plug-in, extension | `pluginkit`, thư mục plug-in, `systemextensionsctl list`, extension của editor và trình duyệt | ✅ | V | ✅ (kiểm kê) | S5, S7 |
| Đồ sót của app đã gỡ | so bundle id với app đang cài + Spotlight + thời gian ghi | ✅ | V | ✅ | S2, S4 |
| File cài đặt đã dùng | `.dmg/.pkg/.xip/.iso` ở Downloads/Desktop/Documents | ✅ | T | ✅ | S2 |

### 1e. File, cloud, theo dõi máy, quét tổng hợp

| Chức năng | Cơ chế khả dĩ trên macOS | Không sudo | Rủi ro | mac-cleaner | Nguồn |
|---|---|---|---|---|---|
| Bản đồ dung lượng ổ | duyệt `os.scandir` + `lstat` (`st_blocks`), không đi theo symlink, không vượt volume; xuất cây JSON cho treemap/sunburst | ✅ | T (chỉ đọc) | ◐ (`classify.py --fs`, mục "Lớn nhất") | S2, R1 |
| File lớn và cũ | `os.walk` hoặc `mdfind 'kMDItemFSSize > N'` + `kMDItemLastUsedDate` | ✅ | V | ◐ | S2, S4 |
| File trùng lặp | gom theo kích thước → băm 64 KB đầu → `hashlib.sha256` toàn file; bỏ qua hardlink (cùng inode) | ✅ | V (luôn giữ ≥1 bản) | ✗ | S2, S4 |
| Ảnh gần giống | `sips` thu nhỏ ảnh → tính dHash bằng stdlib; hoặc Vision `VNGenerateImageFeaturePrintRequest` trong app Swift | ✅ | V (người dùng chọn bản giữ) | ✗ | S2, S4 |
| File trong Downloads | tuổi file + nguồn tải (`xattr com.apple.metadata:kMDItemWhereFroms`) | ✅ | V | ◐ (chỉ file cài) | S2, S4 |
| Dọn cloud: bản local của file đã đồng bộ | iCloud: `~/Library/Mobile Documents`, giải phóng bản local bằng `FileManager.evictUbiquitousItem(at:)` (Swift). Nhà cung cấp khác: `~/Library/CloudStorage/*`, nhận diện file chỉ còn trên cloud qua cờ `SF_DATALESS` trong `st_flags`. `brctl` trên macOS 26 không còn lệnh evict **(đã kiểm)** | ◐ | **C: xoá file đồng bộ là xoá trên cloud** | ✗ | S2, S6, R1 |
| Huỷ file an toàn | ghi đè vô hiệu trên SSD/APFS (TRIM, copy-on-write) | — | — | không làm (khuyên bật FileVault) | S7 |
| Theo dõi máy (CPU, RAM, ổ, pin, mạng, thiết bị) | `top -l 1`, `vm_stat`, `sysctl`, `df`, `pmset -g batt`, `ioreg -rn AppleSmartBattery`, `netstat -ib`, `system_profiler SPUSBDataType SPBluetoothDataType` | ✅ | T | ✗ (đang dùng Mole) | S2, R1 |
| Cảnh báo (ổ sắp đầy, Thùng rác lớn, app treo, pin hao) | ngưỡng trên dữ liệu theo dõi, thông báo qua `UserNotifications` | ✅ | T | ✗ | S2 |
| Quét tổng hợp một lần | gọi tuần tự các module, mỗi module trả về danh sách mục + gợi ý | ✅ | T (chỉ đọc) | ◐ (`scan` + `classify`) | S2, S3, R1 |
| Ngoài phạm vi | dọn hộp thư/huỷ đăng ký bản tin, quản lý tab trình duyệt, chế độ khoá bàn phím để lau màn hình | — | — | không làm | S4 |

## 2. Lệnh Mole đang dùng và cách thay

Nguyên tắc clean-room: Mole là GPL-3.0. Không đọc hay chép mã, danh sách đường dẫn, whitelist của Mole. Chỉ viết từ man page,
tài liệu Apple và luật đã có của mac-cleaner. Mọi lệnh thay thế đều mặc định xem trước và xoá bằng cách chuyển vào Thùng rác.

| Lệnh Mole | Đang dùng ở | Thay bằng | Khác biệt cần giữ |
|---|---|---|---|
| `mo clean` (+ `--dry-run`, whitelist `~/.config/mole/whitelist`) | SKILL.md, README (phần Bổ sung), hook xin duyệt | `mac-cleaner scan` + `clean` với module `junk` mở rộng (cache Application Support có tên `Cache*`, cache trình duyệt, cache công cụ lập trình, log trong Containers, file cấu hình hỏng, rác AI). Phần Homebrew giao cho `brew cleanup --dry-run`/`brew cleanup`. Danh sách loại trừ dùng `--keep`/`MACCLEANER_KEEP_FILE` (đã có) | chuyển vào Thùng rác thay vì xoá hẳn; nhóm cần sudo chỉ in lệnh cho người dùng tự chạy |
| `mo purge` | SKILL.md | mục `build-artifact` (đã có) | đã giữ luật "dùng trong 7 ngày thì không đề xuất" |
| `mo uninstall <app>` | SKILL.md, ghi chú trong `scan_apps` (`maccleaner.py`), `Web/board.html` | `mac-cleaner uninstall <app\|bundle id>`: xem trước app + file liên quan, `launchctl bootout` agent, rồi chuyển vào Thùng rác | app thuộc root hay cài bằng `.pkg`: in đường dẫn và hướng dẫn, không tự leo quyền |
| `mo installer` | SKILL.md | mục `installer` (đã có) | — |
| `mo analyze` | SKILL.md | `mac-cleaner analyze [path] [--depth N] [--json]` (module `space`) | chỉ đọc |
| `mo status` | SKILL.md | `mac-cleaner status [--json]` (module `monitor`) | chỉ đọc |
| `mo optimize` | hook `confirm-cleanup.sh`, README | `mac-cleaner maintain list\|run <task>` (module `maintain`): chỉ chạy tác vụ cấp user; tác vụ cần sudo thì in lệnh | không bao giờ tự gọi `sudo` |

Khi bỏ Mole cần sửa thêm: `install.sh` (bước 2 và cờ `--no-mole`), regex trong `hooks/confirm-cleanup.sh` (thêm `uninstall`,
`maintain run` của mac-cleaner), mô tả trong `.claude-plugin/marketplace.json`, mục Mole trong SKILL.md và README.

## 3. Chia module để làm song song

Ràng buộc chung: mỗi module là `mclib/<tên>.py`, chỉ dùng stdlib, không import lẫn nhau ngoài `mclib/safety.py` và `mclib/core.py`.
Mỗi module có hàm `scan(ctx) -> list[item]` theo schema mục hiện có (`category, kind, name, path, owner, version, safety, action,
note, size, id`) và test riêng trong `tests/test_<tên>.py`. Dữ liệu dạng cây hoặc số đo không phải mục dọn thì ghi vào khoá riêng
của report JSON.

| Ưu tiên | Module | Lệnh CLI | Report JSON | Thay Mole | Phụ thuộc |
|---|---|---|---|---|---|
| P0 | `mclib/core.py` + `mclib/safety.py`: tách `run/du/plist/item` và `hard_deny/validate/to_trash/guard_reason` ra khỏi `maccleaner.py`, giữ nguyên hành vi và test | (không đổi) | (không đổi) | — | làm trước, một người |
| P1 | `mclib/junk.py`: rác hệ thống mở rộng (1a) | `scan --only junk`, `clean --category junk` | `items[]` với `kind` mới: `browser-cache`, `ai-cache`, `ai-model`, `broken-pref`, `ios-backup`, `container-log` | `mo clean` | P0 |
| P1 | `mclib/uninstall.py`: gỡ app sạch, app lâu không mở | `uninstall <app> [--yes]`, `scan --only apps` | `items[].related[]` (file liên quan của từng app), `items[].lastUsed` | `mo uninstall` | P0 |
| P1 | `mclib/space.py`: bản đồ dung lượng, file lớn và cũ | `analyze [path] [--depth N] [--json]` | `space: {path, size, children[]}` (có thể ghi file riêng `space-<ts>.json` vì cây lớn) | `mo analyze` | core |
| P2 | `mclib/startup.py`: mục khởi động và chạy nền, agent hỏng | `startup list`, `startup disable\|enable <label>` | `items[]` `category: startup`, `kind: agent\|daemon\|login-item\|btm` | một phần `mo optimize` | P0 |
| P2 | `mclib/dupes.py`: file trùng, ảnh gần giống | `dupes [path…] [--images]` | `duplicates: [{hash, size, files[], keep}]` | — | core |
| P2 | `mclib/monitor.py`: số đo máy, cảnh báo ngưỡng | `status [--json]` | `status: {cpu, mem, disk, battery, net, devices}` (không vào `items`) | `mo status` | core |
| P3 | `mclib/maintain.py`: tác vụ bảo trì cấp user, snapshot local | `maintain list\|run <task>` | `maintenance: [{task, needsRoot, command}]` | `mo optimize` | P0 |
| P3 | `mclib/updates.py`: liệt kê bản cập nhật (Sparkle, Homebrew cask, macOS) | `updates` | `updates: [{app, current, latest, source, url}]` | — | core (cần mạng; tắt được bằng `--offline`) |
| P3 | `mclib/privacy.py`: dữ liệu trình duyệt, mục gần đây, quyền TCC | `privacy list`, `privacy reset <dịch vụ> <bundle id>` | `items[]` `category: privacy` (mặc định `info`) | — | P0 |
| P3 | `mclib/persistence.py`: kiểm kê bám trụ + chữ ký mã (thay một phần quét malware) | `audit` | `audit: [{path, kind, signed, notarized, quarantine, flags[]}]` | — | startup |
| P3 | `mclib/cloud.py`: dung lượng bản local của thư mục đồng bộ, file chỉ còn trên cloud | `cloud` (giải phóng bản local làm trong app Swift) | `cloud: [{provider, root, localBytes, files[]}]` | — | core |

App SwiftUI: mỗi khoá report mới là một mục trên sidebar. Phần cần API Apple (evict iCloud, Vision, FSEvents, thông báo) làm ở Swift,
Python chỉ cung cấp dữ liệu.

### Danh sách bị chặn và phương án thay thế an toàn

| Bị chặn | Vì sao | Phương án |
|---|---|---|
| Xoá cache DNS, `purge` RAM, `periodic`, `mdutil -E /`, log `/private/var/log`, cache của root, `/.DocumentRevisions-V100`, `/Users/Deleted Users`, LaunchDaemons hệ thống, xoá mạng Wi-Fi đã lưu | cần root | `maintain list` in đúng lệnh `sudo …` để người dùng tự chạy; báo dung lượng nếu đọc được |
| Gỡ app thuộc root hoặc cài bằng `.pkg`, xoá file pkg ở `/Library` | cần quyền admin | liệt kê `pkgutil --files`, hướng dẫn dùng trình gỡ của hãng hoặc kéo vào Thùng rác qua Finder (Finder tự hỏi mật khẩu) |
| Liệt kê `~/.Trash`, `~/Library/Mail`, dữ liệu Safari, bản sao lưu iOS, `TCC.db` | cần Full Disk Access | thử đọc trước; lỗi quyền thì đánh dấu `needsFDA`, mở pane `x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles`, không đoán |
| Đổ Thùng rác qua Finder, đọc login item kiểu cũ | cần quyền Automation (Apple Events) | hỏi rõ trước khi gọi; dự phòng: `sfltool dumpbtm` (không sudo) |
| Phát hiện malware thật, CSDL luật an toàn theo từng app | CSDL chữ ký và luật độc quyền | `persistence.py` chỉ liệt kê và gắn cờ (không ký, ad-hoc, không notarize, nằm ở `/tmp`, file thực thi ẩn); dựa vào Gatekeeper/XProtect của Apple; khuyên dùng phần mềm diệt virus chuyên dụng; luật dọn là luật mở của dự án |
| Xoá file ngôn ngữ, gọt binary đa kiến trúc | phá chữ ký mã | chỉ báo dung lượng, không sửa bundle |
| Cài cập nhật App Store, macOS | không có API công khai; cài macOS cần admin | chỉ liệt kê; mở App Store hoặc Cài đặt |
| Giải phóng bản local của file cloud bằng CLI | `brctl` trên macOS 26 không còn lệnh evict (đã kiểm) | Swift `FileManager.evictUbiquitousItem(at:)` cho iCloud; nhà cung cấp khác thì hướng dẫn thao tác "Remove Download" trong Finder |
| Huỷ file an toàn, sửa quyền ổ đĩa | vô hiệu trên APFS/SSD hoặc lỗi thời | không làm; ghi rõ lý do trong UI |
| Dọn hộp thư online, tab trình duyệt | cần tài khoản hoặc dịch vụ ngoài | ngoài phạm vi |

## 4. Rào an toàn bắt buộc

**Không bao giờ xoá**, kể cả khi dùng `--force`:
- cây hệ thống và SIP, thư mục gốc (`DENY_TREES`, `DENY_EXACT` đã có); thư mục mac-cleaner đang chạy;
- khoá và bí mật: Keychains, `~/.ssh`, `~/.gnupg`, dữ liệu trình quản lý mật khẩu, ví, app xác thực;
- phần mềm bảo mật và quản lý thiết bị (EDR, MDM);
- bên trong thư viện ảnh (`*.photoslibrary`), kho thư `~/Library/Mail`, `~/Library/Messages`: chỉ app sở hữu được sửa;
- file trong thư mục đồng bộ cloud (`~/Library/Mobile Documents`, `~/Library/CloudStorage`): chỉ được giải phóng bản local, không xoá;
- nội dung bundle app đã ký (`.lproj`, Mach-O), bản sao lưu Time Machine (chỉ đụng snapshot local qua `tmutil`);
- cache đang là dữ liệu sống (`LIVE_DATA_CACHES`), dữ liệu của app đang chạy, trình duyệt đang mở;
- cây Homebrew: giao cho `brew`.

**Phải qua Thùng rác** (khôi phục được), kiểm lại từng mục ngay trước khi xoá, ghi `logs/clean-*.jsonl`:
- mọi file, thư mục do mac-cleaner xoá: cache, log, đồ sót, file cài, build artifact, app gỡ, file lớn/cũ, Downloads;
- file trùng: luôn giữ ít nhất một bản (bản cũ nhất hoặc bản trong thư mục người dùng chọn), không bao giờ chọn hết cả nhóm;
  lưu ý bản clone APFS dùng chung block nên xoá không giải phóng dung lượng, báo dung lượng thực tế bằng `df` trước và sau;
- LaunchAgent: `launchctl bootout` trước rồi mới chuyển plist vào Thùng rác.

**Không hoàn tác được**: tách khỏi xoá hàng loạt, phải tick xác nhận riêng, ghi log: đổ Thùng rác, `simctl delete`,
`tmutil thinlocalsnapshots`/`deletelocalsnapshots`, `tccutil reset`, xoá lịch sử trình duyệt.

**Chỉ liệt kê** (không có hành động tự động, người dùng tự chọn từng mục):
mục nghi malware hay bám trụ, bản cập nhật app, model AI local, ảnh gần giống, file cloud, file đính kèm mail, bản sao lưu iOS,
mục khởi động của hệ thống hay của người dùng khác, mọi tác vụ cần sudo (chỉ in lệnh), mọi mục cần FDA mà chưa có quyền.

**Quy tắc chung**: mặc định xem trước; dùng `lstat`, không đi theo symlink ra ngoài vùng được phép; không vượt sang volume khác
khi không được yêu cầu; nghi ngờ thì giữ; không gửi report (đường dẫn, danh sách app) ra khỏi máy.
