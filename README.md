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
| Plugin Claude Code | Có `claude` thì cài plugin `mac-cleaner` (skill `mac-cleaner` + `mole`, hook xin duyệt) từ chính repo; không có thì bỏ qua |

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

swift run MacCleaner                             # app desktop: quét lại, chọn, xem trước, dọn ngay trong UI
swift run MacCleaner --snapshot x.png            # render report mới nhất ra ảnh rồi thoát
./scripts/make-app.sh && open build/MacCleaner.app
python3 -m unittest discover tests               # unit test
```

Yêu cầu: macOS 13+, Python 3.9+ (chỉ thư viện chuẩn), Swift 5.9+ cho app. Không có dependency ngoài.
App `.app` ghi report/log vào `~/Library/Application Support/MacCleaner`; `swift run`/CLI ghi cạnh source (`reports/`, `logs/`, đã gitignore).

## Hook xin duyệt (khi dùng qua Claude Code)

Plugin có hook `PreToolUse`: trước khi agent chạy lệnh **xoá thật** — `mac-cleaner clean … --yes`, `mo clean|purge|uninstall|optimize|installer` không kèm `--dry-run`, hay lệnh đụng tới Thùng rác — Claude Code **phải hỏi bạn duyệt**, kể cả ở auto mode. Lệnh xem trước chạy bình thường. Hook không chặn cứng: bạn luôn là người quyết.

## Mức an toàn

| Mức | Ý nghĩa | Ví dụ |
|---|---|---|
| `safe` | dọn không mất dữ liệu, app tự tạo lại | `~/Library/Caches/*` (cache của macOS `com.apple.*` để mức `review`), `~/Library/Logs/*`, cache trong Containers, DerivedData, simulator unavailable, backup cũ (đã có bản mới hơn), extension editor obsolete |
| `review` | dọn được nhưng nên xem | build artifact (node_modules, Pods, .venv…), installer .dmg/.pkg/.xip, iOS DeviceSupport, Archives, `~/.cache/*`, bản extension cũ, leftover app, `/Library/Caches` |
| `info` | chỉ kiểm kê | ứng dụng, plugin, extension đang dùng, git, backup mới nhất |
| `protected` | không bao giờ dọn | Keychains, `~/.ssh`, `~/.gnupg`; **mọi thư mục trong `~/Library` ngoài** `Caches`, `Logs`, `Containers`, `Group Containers`, `Preferences`, `Developer` (dữ liệu ứng dụng, thư mục đồng bộ…); **mọi thư mục ẩn ở home ngoài** vùng cache `~/.cache`, `~/.npm`, `~/.gradle/caches` (dữ liệu, model, cấu hình của các tool) |

`clean` **kiểm lại từng mục lúc chạy** (không tin report): phải là `safe`/`review`, còn tồn tại, không protected,
nằm trong `~` (sâu ≥ 2 cấp) hoặc `/Library/Caches`, `/Library/Logs`; lệnh đặc biệt chỉ cho phép `xcrun simctl delete`.
Xoá = **chuyển vào Thùng rác** (`/usr/bin/trash`) — khôi phục được; đổ Thùng rác để lấy lại dung lượng. Mỗi lần dọn ghi `logs/clean-<ts>.jsonl`.

## Nguồn dữ liệu

| Nhóm | Nguồn |
|---|---|
| applications | `/Applications`, `~/Applications`, `/System/Applications` — `Info.plist` (tên, version, bundle id), App Store / thủ công / macOS |
| plugins | `pluginkit -mAvvv` (app extension + app cha), `{/,~/}Library/{Internet Plug-Ins, Audio/Plug-Ins, QuickLook, Spotlight, PreferencePanes, Input Methods, Services, …}`, `systemextensionsctl list` |
| extensions | `~/.<editor>/extensions/*/package.json` (mọi editor lưu extension theo kiểu này), `<Chromium browser>/<profile>/Extensions/<id>/<ver>/manifest.json` |
| git | `which -a git`, mọi thư mục có `.git` trong `~` (bỏ Library, node_modules, cache…) và `/opt/homebrew` — branch, remote, số file dirty, commit cuối, dung lượng `.git` |
| caches | `~/Library/{Caches,Logs}`, `~/Library/Containers/*/Data/Library/Caches`, `/Library/{Caches,Logs}`, Xcode DerivedData/DeviceSupport/Archives, `simctl list devices unavailable`, `~/.npm`, `~/.gradle/caches`, `~/.cache/*`… |
| junk | build artifact khi đi qua `~`, installer trong Downloads/Desktop/Documents, file backup trên toàn volume Data (`find -x`, gom theo thư mục + tên gốc, giữ bản mtime mới nhất), Containers/Preferences không khớp bundle id nào đang cài, Thùng rác |

Bổ sung: [Mole](https://github.com/tw93/mole) (`mo clean`, skill `/mole`) dọn sâu hơn ở cấp app (Application Support caches, Homebrew cleanup…).

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
skills/                        skill mac-cleaner + mole (plugin Claude Code, manifest ở .claude-plugin/)
hooks/                         hook xin duyệt: Claude phải hỏi trước khi dọn thật (--yes), chạy Mole không --dry-run, hay đụng Thùng rác
tests/test_maccleaner.py       unit test
```
