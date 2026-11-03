# Mac Cleaner

Kiểm kê toàn bộ máy macOS và dọn dẹp an toàn: **ứng dụng, plugin (thuộc app nào), extension (editor + trình duyệt),
git (binary + mọi repo), cache, log, junk** (build artifact, installer, backup cũ, leftover của app đã gỡ, simulator hỏng).
Mỗi mục có đường dẫn, app sở hữu, phiên bản, dung lượng và mức an toàn. Dashboard có sidebar, KPI, biểu đồ, drawer.

## Cài (máy Mac bất kỳ, macOS 13+, Apple Silicon hoặc Intel)

```sh
git clone https://github.com/KmSlime/mac-cleaner.git && cd mac-cleaner && ./install.sh
```

`install.sh` không dùng `sudo`, chạy lại được nhiều lần:

| Bước | Làm gì |
|---|---|
| Command Line Tools | Thiếu thì mở trình cài chính thức của Apple (cho Python 3 + Swift); cài xong chạy lại `./install.sh` |
| Mole (`mo`) | Homebrew nếu có; không thì tải bản phát hành `V1.68.0`, **kiểm sha256** rồi chép vào `~/.local/share/mole` + `~/.local/bin/mo` (không chạy script cài của bên thứ ba) |
| App **Mac Cleaner** | Build Universal từ source → `~/Applications/MacCleaner.app`. Thiếu Python 3 thì app tự hỏi và mở trình cài |
| Lệnh `mac-cleaner` | `~/.local/bin/mac-cleaner` — dùng chung report với app (`~/Library/Application Support/MacCleaner`) |
| Plugin Claude Code | Có `claude` thì cài plugin `mac-cleaner` (skill `mac-cleaner` — kiểm kê + dọn sâu bằng Mole —, hook xin duyệt) từ chính repo; không có thì bỏ qua |

Tuỳ chọn: `--no-app`, `--no-mole`, `--no-plugin`. Chỉ cần app: tải `.zip` ở trang Releases.
Chỉ cần skill trong Claude Code (đã đăng nhập GitHub có quyền đọc repo): `/plugin install mac-cleaner --marketplace KmSlime/mac-cleaner`.

## Chạy

```sh
python3 maccleaner.py scan                       # quét → reports/report-<ts>.{html,md,json}
open reports/report-*.html                       # report tĩnh (tick chọn → copy lệnh dọn)
python3 maccleaner.py clean                      # preview: mọi mục "an toàn" của report mới nhất
python3 maccleaner.py clean --category caches --yes          # dọn thật một nhóm
python3 maccleaner.py clean --ids a1b2c3d4e5,... --yes       # dọn đúng các id (lấy từ report/app)
python3 maccleaner.py clean --include-review ...             # gồm cả mục "cân nhắc"
python3 maccleaner.py clean --ids <id> --force --yes          # xoá đích danh cả mục kiểm kê/bảo vệ (app, repo, plugin…)

swift run MacCleaner                             # app desktop: quét lại, chọn, xem trước, dọn ngay trong UI
swift run MacCleaner --snapshot x.png            # render report mới nhất ra ảnh rồi thoát
./scripts/make-app.sh && open build/MacCleaner.app
python3 -m unittest discover tests               # unit test
```

Yêu cầu: macOS 13+, Python 3.9+ (chỉ thư viện chuẩn), Swift 5.9+ cho app. Không có dependency ngoài.
App `.app` ghi report/log vào `~/Library/Application Support/MacCleaner`; `swift run`/CLI ghi cạnh source (`reports/`, `logs/`, đã gitignore).

## Mục lục

Sidebar là cây mục lục: **Tổng quan** → từng nhóm (Ứng dụng, Plugin, Extension, Git, Cache & log, Junk) → từng loại bên trong (vd Cache & log › Log, Cache người dùng, Xcode DerivedData…), mỗi nút có số mục và dung lượng. Bấm mũi tên để mở/thu gọn, bấm tên để xem. Bấm bất kỳ chỗ nào trên trang dẫn tới danh sách (một loại trong cây, một thanh biểu đồ, thẻ KPI, một app trong "App chiếm cache", một app trong "Plugin theo app", tiêu đề các thẻ) thì bảng được lọc và **trang cuộn tới bảng**. Bộ lọc đang bật hiện thành chip phía trên bảng, bấm ✕ để bỏ.

## Xoá

**Mọi mục hiện trên dashboard đều xoá được**, trừ những chỗ macOS khoá: phần hệ thống được SIP bảo vệ (`/System`, `/usr` ngoài `/usr/local`, `/bin`, `/sbin` — app và plugin có sẵn của macOS), thư mục gốc (`/Applications`, `~`, `~/Documents`, `~/Library`…), và thư mục Mac Cleaner đang chạy trên đó. Những mục này có nút xoá mờ kèm lý do.

- **Từng mục:** nút **🗑** trên mỗi dòng của bảng, của "Lớn nhất" và "Git repos" (nút **ⓘ** để xem chi tiết). Hộp thoại có đường dẫn, loại, dung lượng, cách xoá, **Xem trước** và **Hiện trong Finder**. Mục `an toàn` xoá ngay; mục `cân nhắc` phải tick xác nhận; mục `kiểm kê`/`bảo vệ` (app, repo, plugin, backup mới nhất…) phải tick xác nhận và có cảnh báo theo loại (repo còn thay đổi chưa commit, plugin trong bundle app khác, app cài bằng quyền admin…).
- **Cả nhóm:** nút 🗑 cạnh mỗi app trong "App chiếm cache" (mọi cache của app đó) và "Plugin theo app".
- **Xoá toàn bộ** (cạnh Log, Quét lại): xoá mọi mục đang hiện trong bảng theo nhóm và bộ lọc hiện tại. Mặc định chỉ gồm mục dọn được (`an toàn` + `cân nhắc`); tick thêm để gồm cả mục kiểm kê/bảo vệ.
- **Chọn nhiều:** checkbox trên mỗi dòng → thanh dưới đáy có **Xem trước** và **Xoá → Thùng rác**.
- **Thùng rác:** mục Thùng rác trong Junk có nút **Đổ Thùng rác** riêng (xoá vĩnh viễn, phải tick xác nhận; macOS có thể hỏi quyền cho Mac Cleaner điều khiển Finder). Thùng rác không bao giờ gộp vào xoá hàng loạt.

Mọi thứ khác đều **chuyển vào Thùng rác**, khôi phục được cho tới khi đổ. LaunchAgent được ngừng (`launchctl bootout`) trước khi chuyển plist vào Thùng rác. Mỗi mục được kiểm lại ngay trước khi xoá. Trong report HTML tĩnh, nút xoá copy lệnh `clean --ids …` (thêm `--force` nếu có mục kiểm kê) để dán vào Terminal.

## Terminal log

Nút **Log** (⌘L) mở bảng log ở đáy app: lệnh đang chạy, từng bước quét kèm số mục và thời gian, nhịp báo trong các bước dài (duyệt home, tìm backup trên toàn ổ, đo dung lượng), từng mục khi dọn, và lỗi nếu có. Lần mở đầu tiên (chưa có report) app tự quét và tự mở log. Log cũng ghi ra `~/Library/Application Support/MacCleaner/logs/app-<ngày>.log`; nút **Copy** để gửi khi cần báo lỗi.

Chạy app với thư mục dữ liệu khác: `MACCLEANER_DATA=/đường/dẫn open -a ~/Applications/MacCleaner.app` (dùng chung biến với lệnh `mac-cleaner`).

## Hook xin duyệt (khi dùng qua Claude Code)

Plugin có hook `PreToolUse`: trước khi agent chạy lệnh **xoá thật** — `mac-cleaner clean … --yes`, `mo clean|purge|uninstall|optimize|installer` không kèm `--dry-run`, hay lệnh đụng tới Thùng rác — Claude Code **phải hỏi bạn duyệt**, kể cả ở auto mode. Lệnh xem trước chạy bình thường. Hook không chặn cứng: bạn luôn là người quyết.

## Mức an toàn

| Mức | Ý nghĩa | Ví dụ |
|---|---|---|
| `safe` | dọn không mất dữ liệu, app tự tạo lại | `~/Library/Caches/*` (cache của macOS `com.apple.*` để mức `review`), `~/Library/Logs/*`, cache trong Containers, DerivedData, simulator unavailable, backup cũ (đã có bản mới hơn), extension editor obsolete |
| `review` | dọn được nhưng nên xem | build artifact (node_modules, Pods, .venv…; vừa dùng trong 7 ngày thì để `info`), cache tải lại rất lâu (`ms-playwright`, `JetBrains`), installer .dmg/.pkg/.xip, iOS DeviceSupport, Archives, `~/.cache/*`, bản extension cũ, leftover app (Containers, Preferences, `Application Support/<bundle id>` — chỉ khi không còn app, Spotlight cũng không thấy, và 30+ ngày không được ghi), LaunchAgent mồ côi (chương trình của nó không còn), `/Library/Caches` |
| `info` | kiểm kê (xoá được khi chọn đích danh) | ứng dụng, plugin, extension đang dùng, git, backup mới nhất |
| `protected` | không dọn tự động (chỉ khi chọn đích danh) | cache đang là dữ liệu sống: `~/Library/Caches/pypoetry` (virtualenv), `org.R-project.R` (renv), `com.apple.FontRegistry`, `com.apple.Spotlight`; Keychains, `~/.ssh`, `~/.gnupg`; **mọi thư mục trong `~/Library` ngoài** `Caches`, `Logs`, `Containers`, `Group Containers`, `Preferences`, `Developer` (dữ liệu ứng dụng, thư mục đồng bộ…); **mọi thư mục ẩn ở home ngoài** vùng cache `~/.cache`, `~/.npm`, `~/.gradle/caches` (dữ liệu, model, cấu hình của các tool) |

`clean` **kiểm lại từng mục lúc chạy** (không tin report): phải là `safe`/`review`, còn tồn tại, không protected,
nằm trong `~` (sâu ≥ 2 cấp) hoặc `/Library/Caches`, `/Library/Logs`; lệnh đặc biệt chỉ cho phép `xcrun simctl delete`.
`--force` (chỉ đi kèm `--ids`, tức là chọn đích danh) bỏ qua mức an toàn và vùng bảo vệ nhưng **vẫn chặn cứng**: SIP và
cây hệ thống (`/System`, `/usr` ngoài `/usr/local`, `/etc`, `/var`, `/Library/Keychains`, `/Library/Extensions`…), thư mục gốc
(`/Applications`, `/Library/Application Support`, `/Users/*`, `/Users/Shared`, `/opt/homebrew`…; so sánh không phân biệt hoa thường
vì APFS), **phần mềm bảo mật / quản lý thiết bị** (EDR, MDM — xoá có thể bị coi là can thiệp), và thư mục Mac Cleaner đang dùng;
với mục Thùng rác thì `--force` nghĩa là đổ Thùng rác. Cache của **app đang chạy** được bỏ qua (`skip`) — đóng app rồi dọn.
Dữ liệu trình quản lý mật khẩu, keychain, ssh/gpg, ví, app xác thực không bao giờ bị coi là "sót lại".
Các rào này học từ cách [Mole](https://github.com/tw93/mole) vận hành và được viết lại cho mac-cleaner (không chép mã).
Xoá = **chuyển vào Thùng rác** (`/usr/bin/trash` trên macOS 15+; macOS 13–14 chuyển thẳng vào `~/.Trash`) — khôi phục được; đổ Thùng rác để lấy lại dung lượng. Mỗi lần dọn ghi `logs/clean-<ts>.jsonl`.

## Nguồn dữ liệu

| Nhóm | Nguồn |
|---|---|
| applications | `/Applications`, `~/Applications`, `/System/Applications` — `Info.plist` (tên, version, bundle id), App Store / thủ công / macOS |
| plugins | `pluginkit -mAvvv` (app extension + app cha), `{/,~/}Library/{Internet Plug-Ins, Audio/Plug-Ins, QuickLook, Spotlight, PreferencePanes, Input Methods, Services, …}`, `systemextensionsctl list` |
| extensions | `~/.<editor>/extensions/*/package.json` (mọi editor lưu extension theo kiểu này), `<Chromium browser>/<profile>/Extensions/<id>/<ver>/manifest.json` |
| git | `which -a git`, mọi thư mục có `.git` trong `~` (bỏ Library, node_modules, cache…) và `/opt/homebrew` — branch, remote, số file dirty, commit cuối, dung lượng `.git` |
| caches | `~/Library/{Caches,Logs}`, `~/Library/Containers/*/Data/Library/Caches`, `/Library/{Caches,Logs}`, Xcode DerivedData/DeviceSupport/Archives, `simctl list devices unavailable`, `~/.npm`, `~/.gradle/caches`, `~/.cache/*`… |
| junk | build artifact khi đi qua `~`, installer trong Downloads/Desktop/Documents, file backup trên toàn volume Data (`find -x`, gom theo thư mục + tên gốc, giữ bản mtime mới nhất), Containers/Preferences không khớp bundle id nào đang cài, Thùng rác |

Bổ sung: [Mole](https://github.com/tw93/mole) (`mo clean`) dọn sâu hơn ở cấp app (Application Support caches, Homebrew cleanup…) — skill `mac-cleaner` chỉ cách dùng cả hai. Lưu ý: `mo clean` **xoá hẳn**, còn mac-cleaner luôn chuyển vào Thùng rác.

## Giới hạn đã biết

- Không dùng `sudo`: `/Library/Caches` của root và cache hệ thống có thể từ chối; Thùng rác cần Full Disk Access cho terminal để đo.
- "Leftover" dựa trên bundle id của app + plugin đang cài, nên vẫn có thể nhầm với helper/daemon → để mức `review`.
- Dung lượng là ước lượng `du -sk` lúc quét; dung lượng thật giải phóng xem `df` trước/sau (CLI và app đều in).

## Cấu trúc

```
maccleaner.py                  quét + dọn + xuất report (Python stdlib, có test)
Web/board.html                 toàn bộ UI (HTML/CSS/JS thuần, offline) — dùng cho cả app và report tĩnh
Sources/MacCleaner/main.swift  cửa sổ AppKit + WKWebView, cầu nối JS → maccleaner.py
scripts/make-app.sh            đóng gói .app Universal (arm64 + x86_64; ARCHS="arm64" để build nhanh)
install.sh                     cài một lệnh: Command Line Tools, Mole, app, lệnh mac-cleaner, plugin Claude Code
skills/mac-cleaner/           skill duy nhất: kiểm kê + dọn bằng mac-cleaner và Mole (plugin Claude Code, manifest ở .claude-plugin/)
hooks/                         hook xin duyệt: Claude phải hỏi trước khi dọn thật (--yes), chạy Mole không --dry-run, hay đụng Thùng rác
tests/test_maccleaner.py       unit test
LICENSE                        MIT
```

## Giấy phép

[MIT](LICENSE): được dùng, sửa, phân phối lại (kể cả thương mại), miễn giữ nguyên thông báo bản quyền. Phần mềm cung cấp "nguyên trạng", không bảo hành.

Mole không nằm trong repo hay file phát hành: `install.sh` tải bản chính thức của nó (giấy phép GPL-3.0, [tw93/mole](https://github.com/tw93/mole)) và mac-cleaner chỉ gọi lệnh `mo` như một chương trình riêng.
