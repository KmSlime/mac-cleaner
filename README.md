# Mac Cleaner

Xem máy Mac đang chứa gì, thứ nào xoá được, rồi dọn một cách an toàn.

- **Mặc định chỉ xem trước.** Không có gì bị xoá cho tới khi bạn bấm xoá (hoặc gõ `--yes`).
- **Xoá = chuyển vào Thùng rác**, khôi phục được cho tới khi bạn đổ Thùng rác. Mỗi lần dọn đều có log.
- **Không dùng `sudo`.** Phần hệ thống của macOS, khoá, mật khẩu và dữ liệu cá nhân không bao giờ bị dọn tự động.

Dùng theo ba cách, cùng một lõi: **app** (dashboard), **dòng lệnh** `mac-cleaner`, hoặc **plugin Claude Code**.

| Mac Cleaner xem và dọn | |
|---|---|
| Kiểm kê + dọn | ứng dụng, plugin (thuộc app nào), extension (editor + trình duyệt), git (binary + mọi repo), cache, log, junk (build artifact, installer, backup cũ, phần sót lại của app đã gỡ, simulator hỏng), rác hệ thống/trình duyệt |
| Ứng dụng | gỡ app kèm file liên quan, app lâu không mở, bản cập nhật (Sparkle, Homebrew cask, macOS — chỉ liệt kê) |
| Dung lượng | bản đồ thư mục, file lớn lâu không dùng, file trùng, thư mục cloud (bản local so với chỉ còn trên cloud) |
| Hệ thống | sức khoẻ máy (ổ, RAM, CPU, pin, mạng), mục khởi động, phần mềm bám trụ + chữ ký mã, quyền riêng tư (TCC), bảo trì cấp user |

Mỗi mục có đường dẫn, app sở hữu, phiên bản, dung lượng và mức an toàn.

**Trong tài liệu này (hướng dẫn sử dụng):**
[1. Cài đặt](#1-cài-đặt) · [2. Cập nhật](#2-cập-nhật) · [3. Dùng app](#3-dùng-app) · [4. Mức an toàn](#4-mức-an-toàn) · [5. Dòng lệnh](#5-dòng-lệnh) · [6. Quyền quản trị](#6-quyền-quản-trị-cấp-một-lần-thu-hồi-được) · [7. Dùng với Claude Code](#7-dùng-với-claude-code) · [8. Giới hạn và câu hỏi thường gặp](#8-giới-hạn-và-câu-hỏi-thường-gặp)

**Phần kỹ thuật để riêng:** [docs/TECHNICAL.md](docs/TECHNICAL.md) — kiến trúc, cấu trúc mã nguồn, rào an toàn chi tiết, giao thức nhúng, build/test, phát hành (viết bằng tiếng Anh).

---

## 1. Cài đặt

Cần macOS 13 trở lên (Apple Silicon hoặc Intel) và Python 3 — thiếu Python thì app tự hỏi và mở trình cài chính thức của Apple.

### Cách 1 — Chỉ cần app (nhanh nhất)

Mở **Terminal**, dán một dòng:

```sh
curl -fsSL https://raw.githubusercontent.com/KmSlime/mac-cleaner/develop/update.sh | bash
```

Lệnh tải bản phát hành mới nhất, kiểm gói (SHA-256, chữ ký mã) rồi đặt `MacCleaner.app` vào `~/Applications`. Không cần `sudo`. Mở app: `open ~/Applications/MacCleaner.app`.

Không muốn dùng Terminal: tải `MacCleaner-<bản>-macos-universal.zip` ở trang [Releases](https://github.com/KmSlime/mac-cleaner/releases/latest), giải nén, kéo `MacCleaner.app` vào Applications. App ký ad-hoc nên lần đầu macOS sẽ chặn: chuột phải vào app → **Mở** (macOS 15 trở lên: Cài đặt hệ thống → Quyền riêng tư & Bảo mật → **Vẫn mở**).

### Cách 2 — Đủ bộ: app + lệnh `mac-cleaner` + plugin Claude Code

```sh
git clone https://github.com/KmSlime/mac-cleaner.git && cd mac-cleaner && ./install.sh
```

`install.sh` không dùng `sudo`, chạy lại bao nhiêu lần cũng được:

| Bước | Làm gì |
|---|---|
| Command Line Tools | Thiếu thì mở trình cài chính thức của Apple (cho Python 3 + Swift); cài xong chạy lại `./install.sh` |
| App **Mac Cleaner** | Build từ source → `~/Applications/MacCleaner.app` |
| Lệnh `mac-cleaner` | `~/.local/bin/mac-cleaner` — dùng chung report với app |
| Plugin Claude Code | Có `claude` thì cài plugin `mac-cleaner` (skill + hook xin duyệt); không có thì bỏ qua |

Tuỳ chọn: `./install.sh --no-app` (bỏ build app), `./install.sh --no-plugin` (bỏ plugin).

### Cách 3 — Chỉ skill trong Claude Code

```
/plugin marketplace add KmSlime/mac-cleaner
/plugin install mac-cleaner@mac-cleaner
```

---

## 2. Cập nhật

### Một lệnh, dùng được cho mọi phiên bản

```sh
curl -fsSL https://raw.githubusercontent.com/KmSlime/mac-cleaner/develop/update.sh | bash
```

Đây cũng chính là lệnh cài ở trên. **App cũ đến đâu cũng dùng được** — kể cả những bản đầu tiên, vốn chưa có nút Cài đặt hay lệnh `self-update`.

- Lệnh tự tìm app ở `~/Applications` rồi `/Applications`. App để chỗ khác thì chỉ đường dẫn:
  `curl -fsSL https://raw.githubusercontent.com/KmSlime/mac-cleaner/develop/update.sh | bash -s -- /đường/dẫn/MacCleaner.app`
- Gói tải về được kiểm SHA-256, bundle id, số phiên bản và chữ ký mã. **Mọi kiểm tra đạt thì mới thay app**; có gì sai thì dừng, app đang cài còn nguyên.
- App cũ được chuyển vào Thùng rác (lấy lại được), app mới nằm đúng chỗ cũ.
- Đang ở bản mới nhất thì lệnh báo vậy và không làm gì.
- App đang mở vẫn là bản cũ: thoát (⌘Q) rồi mở lại.

### Từ bản 1.10.0: cập nhật ngay trong app

Nút **Cài đặt** ở cuối sidebar:

- **Kiểm tra / Cập nhật:** hỏi GitHub bản phát hành mới nhất và cài nếu bạn bấm.
- **Đang cài bản nào:** số phiên bản hiện ngay dưới tên app ở đầu sidebar (từ 1.11.0), trong Cài đặt (dòng *Đang dùng*) và ở menu **Mac Cleaner → Giới thiệu Mac Cleaner**.
- **Tự kiểm tra theo lịch — mặc định TẮT, bạn phải tick đồng ý mới bật.** Chọn hằng ngày hoặc thứ Hai hằng tuần, vào giờ bạn đặt; chọn *chỉ thông báo* (mặc định) hoặc *tự cài*. Mỗi lần chạy là một request HTTPS tới `api.github.com` để đọc số phiên bản, không gửi dữ liệu nào của máy. Tắt lúc nào cũng được.

### Lịch đang có trên máy (từ 1.11.0)

Máy đã từng cài Mac Cleaner thì có thể vẫn còn lịch cũ đang chạy. Bản mới tự tìm các lịch **của chính Mac Cleaner** — LaunchAgent của riêng bạn trong `~/Library/LaunchAgents` có nhãn bắt đầu bằng `local.maccleaner.` — mỗi lần mở app và ngay sau khi cài; có lịch cũ thì nút Cài đặt đổi màu và app báo một lần.

Trong **Cài đặt → Lịch đang có trên máy**, mỗi lịch hiện tên, giờ chạy, lệnh nó chạy và một nhãn:

| Nhãn | Nghĩa là | **Sửa** làm gì |
|---|---|---|
| **bản đang cài** | Lịch đang chạy đúng script của bản bạn vừa cài | Đổi giờ chạy |
| **script cũ** | Lịch trỏ tới bản Mac Cleaner khác, hoặc bản đó không còn | Thay bằng script của bản đang cài (đổi giờ nếu muốn) |
| **lệnh riêng** | Lệnh của lịch không gọi thẳng `maccleaner.py` (ví dụ gọi qua lệnh `mac-cleaner`) | Chỉ đổi được giờ chạy; lệnh giữ nguyên |

- **Sửa** lịch tự kiểm tra bản mới của chính Mac Cleaner: bản đang cài viết lại toàn bộ lịch đó.
- **Xoá** (bấm hai lần để xác nhận): launchd ngừng chạy lịch, file lịch vào Thùng rác — lấy lại được. Sau đó máy không còn lịch nào cho tới khi bạn đặt lịch mới ở phần *Tự kiểm tra theo lịch* ngay phía trên.
- Lịch do công cụ khác đặt (nhãn không bắt đầu bằng `local.maccleaner.`) không hiện ở đây, kể cả khi nó gọi Mac Cleaner: Mac Cleaner không đọc lệnh, không sửa và không xoá lịch đó — quản lý nó ở chính công cụ kia. (Bản 1.11.0 từng liệt kê cả các lịch này; từ 1.11.1 thì không.)

### Cài bằng `install.sh` (có lệnh `mac-cleaner`)

```sh
mac-cleaner self-update check                 # có bản mới không
mac-cleaner self-update apply                 # xem trước các bước, chưa làm gì
mac-cleaner self-update apply --yes           # cập nhật cả lệnh mac-cleaner lẫn app
mac-cleaner self-update auto on --every weekly --at 09:00 --yes    # tự kiểm tra theo lịch (thêm --install để tự cài)
mac-cleaner self-update auto off              # gỡ lịch
mac-cleaner self-update schedule list         # các lịch chạy Mac Cleaner đang có trên máy
mac-cleaner self-update schedule edit <nhãn> [--every daily|weekly] [--at HH:MM] [--yes]   # trỏ về bản đang cài / đổi giờ
mac-cleaner self-update schedule remove <nhãn> [--yes]                                    # xoá lịch (file vào Thùng rác)
mac-cleaner --version                         # đang cài bản nào
```

Sau khi cập nhật, macOS có thể hỏi lại vài quyền (Full Disk Access, Automation) vì app ký ad-hoc.

---

## 3. Dùng app

### Lần đầu mở

App tự quét và hiện terminal để bạn thấy tiến độ. Quét xong, popup **Đề xuất xoá** tự bật.

### Trang Tổng quan — đọc từ trên xuống

1. **Có thể giải phóng:** dung lượng dọn được, thanh đĩa (đã dùng / an toàn / cân nhắc / trống), nút **Xem đề xuất xoá**.
2. **Theo mức an toàn:** thẻ Dọn an toàn / Cân nhắc / Kiểm kê & bảo vệ.
3. **Theo nhóm:** biểu đồ theo nhóm + mục lớn nhất.
4. **Chi tiết:** bảng mọi mục, lọc theo mức / loại / tìm kiếm.
5. **Theo chủ sở hữu.**

Đang quét thì có thanh trạng thái (bước hiện tại). Chưa có report thì có màn hình trống với nút **Quét ngay**.

### Sidebar — cây mục lục

- **Tổng quan** → từng nhóm (Ứng dụng, Plugin, Extension, Git, Cache & log, Junk) → từng loại bên trong (vd Cache & log › Log, Cache người dùng, Xcode DerivedData…). Mỗi nút có số mục và dung lượng. Bấm mũi tên để mở/thu gọn, bấm tên để xem.
- Khu **Tính năng**: sức khoẻ máy, rác hệ thống, dung lượng, file trùng, cloud, gỡ app, cập nhật, khởi động, bám trụ, riêng tư, bảo trì. Lệnh làm thay đổi trạng thái máy chỉ hiện để bạn copy và chạy trong Terminal.
- Bấm bất kỳ chỗ nào dẫn tới một danh sách (một loại trong cây, một thanh biểu đồ, thẻ KPI, một app trong "App chiếm cache" hay "Plugin theo app", tiêu đề các thẻ) thì bảng được lọc và **trang cuộn tới bảng**. Bộ lọc đang bật hiện thành chip phía trên bảng, bấm ✕ để bỏ.

### Xoá

**Mọi mục hiện trên dashboard đều xoá được**, trừ những chỗ macOS khoá (xem [Không bao giờ xoá được](#không-bao-giờ-xoá-được)). Những mục đó có nút xoá mờ kèm lý do.

| Cách xoá | Dùng khi | Hoạt động |
|---|---|---|
| **Đề xuất xoá** (popup) | muốn dọn nhanh sau khi quét | Quét xong tự bật; nút **Đề xuất xoá** ở đầu trang để mở lại. Hai nhóm: **Khuyến nghị** (mục *an toàn*, chọn sẵn) và **Cân nhắc** (không chọn sẵn). Tick cả nhóm hoặc từng mục, có tổng dung lượng đã chọn |
| **Từng mục** | xoá đúng một thứ | Nút **🗑** trên mỗi dòng của bảng, của "Lớn nhất" và "Git repos" (nút **ⓘ** để xem chi tiết). Hộp thoại có đường dẫn, loại, dung lượng, cách xoá, **Xem trước** và **Hiện trong Finder** |
| **Cả nhóm** | dọn mọi cache/plugin của một app | Nút 🗑 cạnh mỗi app trong "App chiếm cache" và "Plugin theo app" |
| **Chọn nhiều** | tự chọn vài mục | Checkbox trên mỗi dòng → thanh dưới đáy có **Xem trước** và **Xoá → Thùng rác** |
| **Xoá toàn bộ** | dọn hết những gì đang lọc | Cạnh nút Log, Quét lại. Xoá mọi mục đang hiện trong bảng theo nhóm và bộ lọc hiện tại; mặc định chỉ gồm mục dọn được (*an toàn* + *cân nhắc*), tick thêm để gồm cả mục kiểm kê/bảo vệ |
| **Đổ Thùng rác** | lấy lại dung lượng thật | Nút riêng ở mục Thùng rác trong Junk: xoá vĩnh viễn, phải tick xác nhận (macOS có thể hỏi quyền cho Mac Cleaner điều khiển Finder). Thùng rác không bao giờ gộp vào xoá hàng loạt |

Mức xác nhận tuỳ theo mục:

- Mục *an toàn*: xoá ngay.
- Mục *cân nhắc*: phải tick xác nhận.
- Mục *kiểm kê* / *bảo vệ* (app, repo, plugin, backup mới nhất…): phải tick xác nhận, có cảnh báo theo loại (repo còn thay đổi chưa commit, plugin nằm trong app khác, app cài bằng quyền admin…).

Kiểu xoá: **Thùng rác** (mặc định, khôi phục được) hoặc **Xoá vĩnh viễn** (hỏi xác nhận lần hai, phải tick "Tôi hiểu"; chỉ áp dụng cho mục *an toàn*/*cân nhắc* nằm trong thư mục home). Cache của app đang chạy được bỏ qua — đóng app rồi dọn.

### Terminal trong app

Phía dưới dashboard là một **terminal thật**: kéo thanh chia để đổi cỡ, **Thu gọn ▾** để còn thanh tiêu đề, ⌘L hoặc nút **Terminal** để hiện và focus.

- Terminal hiện log của app: lệnh đang chạy, từng bước quét kèm số mục và thời gian, từng mục khi dọn, lỗi nếu có.
- Ctrl-C để thôi xem log và dùng shell ngay tại thư mục dữ liệu (gõ lệnh `mac-cleaner` nếu đã cài theo Cách 2). Phiên kết thúc thì bấm **Mở lại**.
- Nút **Terminal.app** mở cùng nội dung đó trong Terminal.app.
- Log lưu ở `~/Library/Application Support/MacCleaner/logs/app-<ngày>.log`.

### Cài đặt

Nút **Cài đặt** ở cuối sidebar gồm hai phần: **Cập nhật** — phiên bản đang cài, kiểm tra/cài bản mới, lịch tự kiểm tra và các lịch đang có trên máy ([mục 2](#2-cập-nhật)) — và **Quyền quản trị** ([mục 6](#6-quyền-quản-trị-cấp-một-lần-thu-hồi-được)).

---

## 4. Mức an toàn

Mỗi mục được xếp vào một trong bốn mức:

| Mức | Nghĩa | Ví dụ |
|---|---|---|
| **An toàn** (`safe`) | dọn không mất dữ liệu, app tự tạo lại | cache và log của user, cache trong Containers, simulator hỏng, backup cũ (đã có bản mới hơn), bản extension editor đã lỗi thời |
| **Cân nhắc** (`review`) | dọn được nhưng nên xem trước | **Xcode DerivedData**, build artifact (`node_modules`, `Pods`, `.venv`…), cache tải lại rất lâu, installer `.dmg`/`.pkg`/`.xip`, iOS DeviceSupport, Archives, phần sót lại của app đã gỡ |
| **Kiểm kê** (`info`) | chỉ liệt kê; xoá được khi bạn chọn đích danh | ứng dụng, plugin, extension đang dùng, git repo, backup mới nhất |
| **Bảo vệ** (`protected`) | không bao giờ dọn tự động; chỉ xoá khi bạn chọn đích danh | Keychains, `~/.ssh`, `~/.gnupg`, dữ liệu ứng dụng trong `~/Library`, thư mục ẩn ở home chứa dữ liệu/cấu hình của các tool |

**Xcode DerivedData** luôn được đề xuất kèm dung lượng nhưng **không bao giờ dọn tự động** (lần build sau sẽ lâu hơn): nó nằm ở nhóm *Cân nhắc*, bạn tự tick mới dọn.

### Không bao giờ xoá được

Kể cả khi chọn đích danh:

- Phần hệ thống được SIP bảo vệ: `/System`, `/usr` (trừ `/usr/local`), `/bin`, `/sbin` — tức là app và plugin có sẵn của macOS.
- Thư mục gốc: `/Applications`, `~`, `~/Documents`, `~/Library`…
- Phần mềm bảo mật / quản lý thiết bị (EDR, MDM).
- Thư mục Mac Cleaner đang chạy trên đó.

Dữ liệu của trình quản lý mật khẩu, keychain, ssh/gpg, ví, app xác thực không bao giờ bị coi là "sót lại".

### Phân loại sau khi quét

Mỗi lần quét xong, Mac Cleaner in thêm một bảng tóm tắt ba nhóm, kèm "xoá thì sao" cho từng mục:

| Nhóm | Nghĩa |
|---|---|
| **NÊN XOÁ** | tự tạo lại (cache, log, build, dependency, bản cài đã dùng) — xoá chỉ tốn thời gian dựng lại |
| **CÂN NHẮC** | xoá được nhưng có hệ quả: mất trạng thái/đăng nhập/index của app, phải tải lại SDK/simulator, dữ liệu của app không còn dùng, đổ Thùng rác là vĩnh viễn |
| **GIỮ** | hệ thống (SIP), dữ liệu người dùng, khoá/bí mật, Homebrew (để `brew` quản), thứ bạn đánh dấu giữ |

Bảng đầy đủ các đường dẫn theo từng mức và cách `clean` kiểm lại từng mục: [docs/TECHNICAL.md](docs/TECHNICAL.md#5-safety-tiers-and-the-clean-guard).

---

## 5. Dòng lệnh

Lệnh `mac-cleaner` có sau khi cài theo [Cách 2](#cách-2--đủ-bộ-app--lệnh-mac-cleaner--plugin-claude-code) (trong thư mục clone, `python3 maccleaner.py …` là một). Chỉ cài app thì mọi việc làm trong app.

Quy tắc chung: **không có `--yes` thì chỉ xem trước**, có `--yes` mới làm thật.

**Quét và dọn**

```sh
mac-cleaner scan                                   # quét, ghi report (html, md, json), rồi in bảng phân loại
mac-cleaner clean                                  # xem trước: mọi mục "an toàn" của report mới nhất
mac-cleaner clean --yes                            # dọn các mục đó (→ Thùng rác)
mac-cleaner clean --category caches --yes          # dọn một nhóm
mac-cleaner clean --category junk:app-cache --yes  # dọn một loại của một tính năng (<tính năng>:<loại>)
mac-cleaner clean --ids a1b2c3d4e5,... --yes       # dọn đúng các mục (id lấy từ report hoặc app)
mac-cleaner clean --include-review --yes           # gồm cả mục "cân nhắc" (DerivedData, node_modules…)
mac-cleaner clean --ids <id> --force --yes         # xoá đích danh một mục kiểm kê/bảo vệ (app, repo, plugin…)
mac-cleaner clean --ids <id>,... --mode permanent --yes   # xoá VĨNH VIỄN, không qua Thùng rác
```

**Ứng dụng**

```sh
mac-cleaner uninstall --list                       # app lâu không mở
mac-cleaner uninstall <tên|bundle id|.app>         # xem trước: app + file liên quan; thêm --yes để gỡ (→ Thùng rác)
mac-cleaner updates [--offline]                    # bản cập nhật app (Sparkle, Homebrew cask) + macOS — chỉ liệt kê
```

**Dung lượng**

```sh
mac-cleaner analyze [path] [--depth 3]             # bản đồ dung lượng
mac-cleaner large [path…] --min 500M --older 180d  # file lớn và lâu không dùng
mac-cleaner dupes [path…] --min-size 5M            # file trùng (luôn giữ một bản mỗi nhóm)
mac-cleaner cloud                                  # iCloud/Dropbox/…: bản local so với chỉ còn trên cloud
mac-cleaner junk                                   # rác hệ thống mở rộng: cache trình duyệt/app, log container, plist hỏng…
```

**Hệ thống**

```sh
mac-cleaner status [--json]                        # sức khoẻ máy: ổ, RAM/swap, CPU, pin, mạng, thiết bị, cảnh báo
mac-cleaner startup list                           # mục khởi động (LaunchAgent, login item)
mac-cleaner startup disable|enable <label> --yes   # tắt/bật một mục cấp user (hoàn tác được)
mac-cleaner audit [--gatekeeper]                   # phần mềm bám trụ + chữ ký mã (mặc định offline)
mac-cleaner privacy list                           # dấu vết trình duyệt, quyền TCC
mac-cleaner privacy reset <dịch vụ> <bundle id> --yes     # thu hồi một quyền của một app
mac-cleaner maintain list                          # tác vụ bảo trì cấp user
mac-cleaner maintain run <tác vụ> --yes            # chạy một tác vụ; tác vụ cần sudo chỉ in lệnh, trừ khi đã cấp quyền (mục 6)
```

**Cập nhật và quyền:** `mac-cleaner self-update …` ([mục 2](#2-cập-nhật)), `mac-cleaner admin …` ([mục 6](#6-quyền-quản-trị-cấp-một-lần-thu-hồi-được)).

**Phân loại cả ổ đĩa** (chạy trong thư mục clone, chỉ đọc):

```sh
python3 scripts/classify.py --report latest        # phân loại report mới nhất: NÊN XOÁ / CÂN NHẮC / GIỮ
python3 scripts/classify.py --fs                   # quét toàn bộ hệ thống file từ / (không sudo)
python3 scripts/classify.py --fs --root ~/Library --root ~/Developer --out reports   # chỉ vài thư mục, ghi kết quả ra file
```

Report và log nằm ở `~/Library/Application Support/MacCleaner` (app và lệnh `mac-cleaner` dùng chung). Trong thư mục `reports/` có `report-<giờ>.html` là report tĩnh mở bằng trình duyệt: tick chọn rồi copy lệnh dọn.

---

## 6. Quyền quản trị (cấp một lần, thu hồi được)

Vài việc cần quyền root: đọc login item (`sfltool dumpbtm` — macOS hỏi mật khẩu mỗi lần) và mấy tác vụ bảo trì. Thay vì hỏi mật khẩu mãi hay lưu mật khẩu, bạn **cấp một lần** cho đúng những lệnh cố định dưới đây:

- Trong app: **Cài đặt** → **Cấp**.
- Dòng lệnh: `mac-cleaner admin grant <id> --yes` (hoặc `all`).

macOS hiện hộp thoại quản trị **một lần**; sau đó Mac Cleaner chạy đúng lệnh đó mà không hỏi lại.

| id | Lệnh được cấp | Tác dụng |
|---|---|---|
| `btm-read` | `sfltool dumpbtm` | đọc login item và mục chạy nền, chỉ đọc |
| `dns-flush` | `dscacheutil -flushcache`, `killall -HUP mDNSResponder` | xoá cache DNS |
| `purge-ram` | `purge` | giải phóng bộ nhớ đệm đĩa |
| `spotlight-reindex` | `mdutil -E /` | đánh chỉ mục lại Spotlight |
| `font-cache-system` | `atsutil databases -remove` | xoá cache font |

- **Xem / thu hồi:** `mac-cleaner admin list`, `mac-cleaner admin revoke <id>|all --yes` (hoặc trong Cài đặt).
- **Không lưu mật khẩu ở đâu cả.** Mật khẩu admin cất trong Keychain với "Always Allow" thì mọi tiến trình của bạn đọc được — tức là root cho mọi thứ, thay vì root cho đúng năm lệnh cố định ở trên.
- Quyền chỉ cấp cho tài khoản của bạn, tham số của lệnh cố định, không sửa được.

---

## 7. Dùng với Claude Code

Cài plugin (Cách 2 hoặc Cách 3 ở [mục 1](#1-cài-đặt)) rồi nhờ Claude kiểm kê hay dọn máy — skill `mac-cleaner` biết dùng các lệnh trên.

**Claude luôn phải hỏi bạn trước khi làm thật.** Plugin có hook xin duyệt: trước mọi lệnh thay đổi máy, Claude Code **phải hỏi bạn duyệt**, kể cả ở auto mode. Lệnh xem trước chạy bình thường. Hook không chặn cứng: bạn luôn là người quyết.

Những lệnh phải duyệt: `mac-cleaner clean|uninstall … --yes`, `startup disable|enable … --yes`, `privacy reset … --yes`, `maintain run … --yes`, `self-update apply … --yes`, `self-update auto on … --yes`, `self-update schedule edit|remove … --yes`, `admin grant|revoke … --yes`, lệnh đụng tới Thùng rác, và `mo clean|purge|uninstall|optimize|installer` không kèm `--dry-run` (nếu bạn tự cài Mole).

---

## 8. Giới hạn và câu hỏi thường gặp

**Xoá nhầm thì lấy lại thế nào?** Mở Thùng rác và kéo mục đó về chỗ cũ (log dọn ghi lại đường dẫn gốc của từng mục). Chỉ *Xoá vĩnh viễn* và *Đổ Thùng rác* là không lấy lại được — cả hai đều hỏi xác nhận riêng.

**Dọn xong sao dung lượng trống chưa tăng?** Mục vừa dọn còn nằm trong Thùng rác; đổ Thùng rác mới lấy lại dung lượng. Dung lượng hiện lúc quét là ước lượng; con số thật xem trước/sau khi dọn (app và dòng lệnh đều in).

**Gõ lệnh `self-update` báo lỗi "invalid choice"?** App đang cài cũ hơn 1.10.0. Dùng lệnh một dòng ở [mục 2](#2-cập-nhật) — chạy được với mọi phiên bản.

**Kiểm tra bản mới báo `CERTIFICATE_VERIFY_FAILED`?** Python trên máy không có sẵn bộ chứng chỉ gốc (hay gặp khi cài Python từ python.org mà chưa chạy *Install Certificates.command*). Từ 1.11.2 Mac Cleaner dùng thêm bộ chứng chỉ có sẵn của macOS nên không còn lỗi này. Bản cũ hơn không tự cập nhật trong app được — chạy lệnh một dòng ở [mục 2](#2-cập-nhật) một lần.

**Đang cài bản nào?** Xem dưới tên app ở đầu sidebar, hoặc menu **Mac Cleaner → Giới thiệu Mac Cleaner**; dòng lệnh: `mac-cleaner --version`. Bản cũ hơn 1.11.0 chưa hiện số này ở sidebar — cập nhật bằng lệnh một dòng ở [mục 2](#2-cập-nhật).

**Cài bản mới rồi mà lịch cũ vẫn chạy?** Mở **Cài đặt → Lịch đang có trên máy**: bấm **Sửa** để lịch chạy script của bản mới, hoặc **Xoá** rồi đặt lịch mới ([mục 2](#lịch-đang-có-trên-máy-từ-1110)).

**Cập nhật xong macOS hỏi lại quyền?** App ký ad-hoc nên sau khi thay app, macOS có thể hỏi lại Full Disk Access, Automation.

**Cập nhật trong app có cập nhật lệnh `mac-cleaner` không?** Không. App ở `~/Applications` và bản clone chứa lệnh `mac-cleaner` là hai bản cài riêng: cập nhật từ app chỉ thay app; `mac-cleaner self-update apply --yes` từ bản clone cập nhật cả hai.

**Không đo được Thùng rác, một số cache hệ thống từ chối dọn?** Mac Cleaner không dùng `sudo` (ngoài mấy lệnh cố định bạn tự cấp ở mục 6): `/Library/Caches` của root và cache hệ thống có thể từ chối; đo Thùng rác cần cấp Full Disk Access cho terminal.

**"Phần sót lại của app đã gỡ" có chắc là rác không?** Mac Cleaner so với bundle id của app và plugin đang cài nên vẫn có thể nhầm với helper/daemon — vì vậy chúng nằm ở mức *Cân nhắc*, không bao giờ chọn sẵn.

**Lúc quét bị hỏi mật khẩu admin?** Từ 1.11.3 quét (kể cả định kỳ/tự động) không bao giờ hiện hộp thoại này: nếu chưa cấp `btm-read` và danh sách login item đã đổi, Mac Cleaner dùng kết quả cũ lần trước (ghi chú "stale") hoặc báo thiếu dữ liệu. Chỉ khi bạn chủ động chạy `startup --refresh-btm` / `uninstall <app> --refresh-btm` mới được hỏi mật khẩu. Cấp `btm-read` một lần ở [mục 6](#6-quyền-quản-trị-cấp-một-lần-thu-hồi-được) thì không hỏi nữa và dữ liệu luôn mới.

---

## Tài liệu kỹ thuật

Dành cho người đọc mã, nhúng dashboard vào app khác, hoặc đóng góp — [docs/TECHNICAL.md](docs/TECHNICAL.md):

- Kiến trúc và sơ đồ ([bản tương tác](docs/architecture/architecture.html)), cấu trúc mã nguồn, build và test
- Nguồn dữ liệu của từng nhóm quét
- Mức an toàn đầy đủ, rào kiểm của `clean`, `--force`, xoá vĩnh viễn
- Hook sau quét và `classify.py`
- Cơ chế cập nhật (`self-update`, `update.sh`), quyền quản trị (`sudoers`)
- Giao thức host ↔ board để nhúng vào app khác
- Hook xin duyệt của plugin Claude Code, quy trình phát hành
- Thêm một tính năng mới: [mclib/README.md](mclib/README.md) · Đối chiếu với CleanMyMac: [docs/CLEANMYMAC-PARITY.md](docs/CLEANMYMAC-PARITY.md)

## Giấy phép

[MIT](LICENSE): được dùng, sửa, phân phối lại (kể cả thương mại), miễn giữ nguyên thông báo bản quyền. Phần mềm cung cấp "nguyên trạng", không bảo hành.

Mole không nằm trong repo hay file phát hành và `install.sh` không còn cài nó. Nếu bạn tự cài `mo` (giấy phép GPL-3.0, [tw93/mole](https://github.com/tw93/mole)), skill chỉ gọi nó như một chương trình riêng.
