#!/usr/bin/env python3
"""classify.py — phân loại kết quả quét thành 3 nhóm: NÊN XOÁ · CÂN NHẮC · GIỮ, kèm mục đích và hệ quả khi xoá.

  python3 scripts/classify.py --report R.json              phân loại report của `maccleaner.py scan`
  python3 scripts/classify.py --fs                         quét TOÀN BỘ hệ thống file từ / (không sudo; chỗ không đọc được bỏ qua)
  python3 scripts/classify.py --fs --root ~/Library --root ~/Developer   quét theo boundary
  python3 scripts/classify.py --report R.json --fs ...     cả hai; thêm --out DIR để ghi classify-<giờ>.{md,json}

Ba nhóm:
  NÊN XOÁ   dữ liệu tự tạo lại (cache, log, build, bản cài đã dùng) — xoá chỉ tốn thời gian dựng lại.
  CÂN NHẮC  xoá được nhưng có hệ quả: mất trạng thái/đăng nhập/index của app, phải tải lại SDK hay simulator,
            hoặc là dữ liệu của app không còn dùng. Mỗi mục ghi rõ "xoá thì sao" để người dùng tự quyết.
  GIỮ       hệ thống (SIP), dữ liệu người dùng, khoá/bí mật, thứ người dùng đánh dấu giữ, app/repo đang dùng.
Không xoá gì — chỉ đọc. Danh sách giữ thêm: --keep PATH (lặp) hoặc file MACCLEANER_KEEP_FILE (mỗi dòng một tiền tố).
Lịch sử dọn: đọc logs/clean-*.jsonl của mac-cleaner (+ MACCLEANER_EXTRA_LOGS, phân cách ':') để báo đã dọn gì trước đây.
Chỉ dùng thư viện chuẩn.
"""
import argparse, fnmatch, glob, json, os, stat, sys, time
from collections import defaultdict

HOME = os.path.expanduser("~")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("MACCLEANER_DATA") or ROOT
XOA, CAN, GIU = "nen-xoa", "can-nhac", "giu"
LABEL = {XOA: "NÊN XOÁ", CAN: "CÂN NHẮC", GIU: "GIỮ"}

# Luật hệ thống file: (mẫu, nhóm, mục đích, xoá thì sao). Mẫu: tiền tố tuyệt đối (~ mở rộng) hoặc glob khớp tên thư mục (*/x).
# Thứ tự quan trọng: luật đầu tiên khớp thắng; GIỮ đứng trước.
FS_RULES = [
    ("/System", GIU, "macOS (volume hệ thống, SIP khoá)", "không xoá được, không nên thử"),
    ("/usr/local", CAN, "phần mềm cài thêm ngoài Homebrew", "gỡ bằng trình cài của nó, không xoá tay"),
    ("/usr", GIU, "công cụ hệ thống", "hỏng hệ thống"),
    ("/opt/homebrew", GIU, "Homebrew (công cụ + gói)", "để Homebrew quản: `brew cleanup`, `brew uninstall`"),
    ("/Library/Developer/CommandLineTools", GIU, "Xcode Command Line Tools", "git/clang hỏng tới khi cài lại"),
    ("/Library/Application Support", CAN, "dữ liệu app dùng chung", "chỉ xoá của app đã gỡ"),
    ("/bin", GIU, "lệnh hệ thống", "hỏng hệ thống"), ("/sbin", GIU, "lệnh hệ thống", "hỏng hệ thống"),
    ("/private/var/db", GIU, "CSDL hệ thống", "hỏng hệ thống"),
    ("/private/var/vm", GIU, "swap + sleepimage do macOS quản", "macOS tự tạo lại ngay; xoá tay vô ích"),
    ("/Library/Apple", GIU, "thành phần Apple", "hỏng hệ thống"),
    ("~/Library/Keychains", GIU, "khoá, mật khẩu", "mất mật khẩu"), ("~/.ssh", GIU, "khoá SSH", "mất quyền truy cập máy chủ"),
    ("~/.gnupg", GIU, "khoá GPG", "mất khoá ký/giải mã"),
    ("~/Library/Mobile Documents", GIU, "iCloud Drive", "xoá cả trên iCloud"),
    ("~/Documents", GIU, "tài liệu người dùng", "mất dữ liệu"), ("~/Desktop", GIU, "tài liệu người dùng", "mất dữ liệu"),
    ("~/Pictures", GIU, "ảnh", "mất dữ liệu"), ("~/Movies", GIU, "video", "mất dữ liệu"), ("~/Music", GIU, "nhạc", "mất dữ liệu"),
    ("~/Library/Mail", GIU, "thư", "mất thư offline"), ("~/Library/Messages", GIU, "tin nhắn", "mất lịch sử"),
    ("~/Library/Photos", GIU, "thư viện ảnh", "mất ảnh"),
    ("~/.Trash", CAN, "Thùng rác", "đổ Thùng rác = xoá VĨNH VIỄN, không khôi phục được"),
    ("~/Library/Caches", XOA, "cache của app", "app tự tạo lại; lần mở đầu chậm hơn"),
    ("~/Library/Logs", XOA, "log của app", "mất log cũ (chỉ cần khi gỡ lỗi)"),
    ("/Library/Logs", XOA, "log hệ thống/app", "mất log cũ"), ("/Library/Caches", XOA, "cache dùng chung", "tự tạo lại"),
    ("/private/var/log", CAN, "log hệ thống", "cần quyền root; macOS tự xoay vòng"),
    ("/private/var/folders", CAN, "temp + cache theo phiên của macOS", "macOS tự dọn sau 3 ngày; xoá tay khi app đang chạy có thể làm app lỗi"),
    ("~/Library/Developer/Xcode/DerivedData", XOA, "build trung gian của Xcode", "build lại lần sau, lâu hơn"),
    ("~/Library/Developer/Xcode/Archives", CAN, "bản archive đã phát hành", "mất dSYM để giải mã crash bản cũ"),
    ("~/Library/Developer/Xcode/iOS DeviceSupport", CAN, "symbol của iOS đã từng cắm máy", "cắm lại máy iOS đó thì Xcode tải lại (vài GB)"),
    ("~/Library/Developer/CoreSimulator/Caches", XOA, "cache simulator", "tự tạo lại"),
    ("~/Library/Developer/CoreSimulator", CAN, "simulator + dữ liệu app thử", "mất app/dữ liệu trong simulator; `xcrun simctl delete unavailable` an toàn hơn"),
    ("~/Library/Containers", CAN, "dữ liệu app sandbox", "mất cài đặt + dữ liệu của app đó; chỉ xoá app đã gỡ"),
    ("~/Library/Group Containers", CAN, "dữ liệu chung nhóm app", "mất dữ liệu của bộ app đó"),
    ("~/Library/Application Support", CAN, "dữ liệu + cài đặt app", "mất dữ liệu app; chỉ xoá của app đã gỡ"),
    ("~/.ollama/models", GIU, "model AI local", "tải lại hàng GB"),
    ("~/.cache", XOA, "cache của công cụ dòng lệnh", "tự tạo lại / tải lại"),
    ("~/.npm/_cacache", XOA, "cache npm", "tải lại khi cài"), ("~/.gradle/caches", XOA, "cache Gradle", "tải lại khi build"),
    ("~/Library/Caches/Homebrew", XOA, "bản tải của Homebrew", "dùng `brew cleanup` thay vì xoá tay"),
    ("~/Downloads", CAN, "file tải về", "xem từng file; .dmg/.pkg/.zip đã cài xong thì xoá được"),
    ("/Applications", CAN, "ứng dụng", "gỡ app không dùng bằng `mac-cleaner uninstall <app>` (kèm file liên quan)"),
    ("*/node_modules", XOA, "dependency JS", "`npm install` lại"), ("*/.build", XOA, "build Swift", "build lại"),
    ("*/Pods", XOA, "dependency CocoaPods", "`pod install` lại"), ("*/.venv", XOA, "môi trường Python", "tạo lại venv"),
    ("*/DerivedData", XOA, "build Xcode cục bộ", "build lại"), ("*/.gradle", XOA, "cache Gradle của dự án", "tải lại"),
    ("*/target", CAN, "build Rust/Maven", "build lại — kiểm đúng là thư mục build"),
    ("*/__pycache__", XOA, "bytecode Python", "tự tạo lại"),
    ("~/Library", CAN, "dữ liệu khác trong Library", "xem từng mục"),
    ("~", CAN, "file khác trong home", "xem từng mục"),
]
SKIP_FS = ("/System/Volumes", "/Volumes", "/dev", "/net", "/home", "/cores")  # Data volume đã thấy qua firmlink (/Users…); ổ ngoài; ảo

REPORT_CAN = {  # chức năng của "cân nhắc" theo loại mục trong report của mac-cleaner
    "user-cache": "cache của app đang dùng/dữ liệu sống — xoá thì app mất trạng thái (đăng nhập, index), tự tạo lại",
    "system-cache": "cache do macOS quản — thường tự dọn; xoá tay ít lợi",
    "dev-cache": "cache công cụ dev — xoá thì lần build/cài sau tải lại",
    "build-artifact": "build/dependency của dự án còn mới — xoá thì phải build/cài lại",
    "installer": "bản cài (.dmg/.pkg) — xoá được nếu đã cài xong",
    "device-support": "symbol iOS — cắm lại máy đó thì Xcode tải lại",
}


def expand(p):
    return HOME + p[1:] if p == "~" or p.startswith("~/") else p


def keep_list(extra):
    out = [expand(k.rstrip("/")) for k in extra]
    f = os.environ.get("MACCLEANER_KEEP_FILE")
    if f and os.path.isfile(f):
        out += [expand(l.strip().rstrip("/")) for l in open(f, encoding="utf-8") if l.strip() and not l.startswith("#")]
    return out


def under(p, prefix):
    return p == prefix or p.startswith(prefix.rstrip("/") + "/")


CONTAINERS = ("~", "~/Library", "~/Documents", "~/Desktop", "~/Downloads")  # đi tiếp xuống để thấy build/dependency bên trong


def match_rule(path, keep):
    """(nhóm, mục đích, xoá thì sao, là_thư_mục_chứa). Thứ tự: giữ của người dùng → glob tên thư mục → tiền tố."""
    if any(under(path, k) for k in keep):
        return (GIU, "người dùng đánh dấu giữ", "—", False)
    name = os.path.basename(path)
    for pat, cls, why, effect in FS_RULES:
        if pat.startswith("*/") and name == pat[2:]:
            return (cls, why, effect, False)
    for pat, cls, why, effect in FS_RULES:
        if not pat.startswith("*/") and under(path, expand(pat)):
            return (cls, why, effect, pat in CONTAINERS)
    return None


def du(path):
    """Kích thước thật (st_blocks) của cây, không theo symlink. Trả (byte, số file, số chỗ không đọc được)."""
    total = files = denied = 0
    stack = [path]
    while stack:
        d = stack.pop()
        if any(under(d, s) for s in SKIP_FS):  # /System chứa firmlink tới Data volume → không đếm lại
            continue
        try:
            it = os.scandir(d)
        except OSError:
            denied += 1
            continue
        with it:
            for e in it:
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    denied += 1
                    continue
                total += st.st_blocks * 512
                if stat.S_ISDIR(st.st_mode):
                    stack.append(e.path)
                else:
                    files += 1
    return total, files, denied


def scan_fs(roots, keep, min_mb, depth):
    """Đi từ gốc tới lá. Thư mục khớp luật cụ thể → tính cả cây, ghi một mục; thư mục chứa (~, Library, Documents…)
    hoặc chưa có luật → đi tiếp xuống; tới --depth thì gộp phần còn lại theo luật gần nhất."""
    rows, stats = [], dict(files=0, denied=0, bytes=0)

    def visit(d, level):
        if any(under(d, s) for s in SKIP_FS):
            return
        r = match_rule(d, keep)
        holds_keep = any(under(k, d) and k != d for k in keep)  # trong có thứ phải giữ → đi tiếp, đừng gộp cả cây
        if (r and not r[3] and not holds_keep) or level >= depth:
            b, n, den = du(d)
            stats["files"] += n; stats["denied"] += den; stats["bytes"] += b
            if b >= min_mb * 2 ** 20:
                cls, why, effect = r[:3] if r else (CAN, "chưa có luật — xem tay", "chưa rõ")
                rows.append(dict(path=d, size=b, files=n, cls=cls, why=why, effect=effect, source="fs"))
            return
        try:
            entries = list(os.scandir(d))
        except OSError:
            stats["denied"] += 1
            return
        loose = 0
        for e in entries:
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                stats["denied"] += 1
                continue
            if stat.S_ISDIR(st.st_mode):
                visit(e.path, level + 1)
            else:
                loose += st.st_blocks * 512; stats["files"] += 1
        stats["bytes"] += loose
        if loose >= min_mb * 2 ** 20:
            cls, why, effect = r[:3] if r else (CAN, "file rời chưa có luật — xem tay", "chưa rõ")
            rows.append(dict(path=d + "/*(file rời)", size=loose, files=0, cls=cls, why=why, effect=effect, source="fs"))

    for root in roots:
        visit(os.path.abspath(expand(root)), 0)
    return rows, stats


def classify_report(path, keep):
    rep = json.load(open(path, encoding="utf-8"))
    rows = []
    for it in rep.get("items", []):
        p, s, kind = it.get("path", ""), it.get("safety"), it.get("kind", "")
        if any(under(p, k) for k in keep):
            cls, why, effect = GIU, "người dùng đánh dấu giữ", "—"
        elif s == "safe":
            cls, why, effect = XOA, it.get("note") or "mac-cleaner xếp an toàn", "chuyển vào Thùng rác (khôi phục được); dữ liệu tự tạo lại"
        elif s == "review":
            cls, why = CAN, REPORT_CAN.get(kind, it.get("note") or "mac-cleaner xếp cân nhắc")
            effect = it.get("note") or "xem mô tả"
        else:
            cls, why, effect = GIU, it.get("note") or ("được bảo vệ" if s == "protected" else "chỉ kiểm kê (app, repo, plugin đang có)"), "—"
        rows.append(dict(path=p, size=it.get("size") or 0, files=None, cls=cls, why=why, effect=effect, source="report",
                         id=it.get("id"), category=it.get("category"), kind=kind))
    return rows, rep


def history(extra_dirs):
    """Đọc log các lần dọn trước: mac-cleaner logs/clean-*.jsonl + log ngoài (vd log của agent)."""
    files = glob.glob(os.path.join(DATA, "logs", "clean-*.jsonl"))
    for d in extra_dirs:
        files += glob.glob(os.path.join(expand(d), "*.jsonl"))
    by, freed, n = defaultdict(int), 0, 0
    for f in files:
        for line in open(f, encoding="utf-8", errors="replace"):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            n += 1; by[e.get("status", "?")] += 1
            if e.get("status") == "ok":
                freed += e.get("size") or 0
    return dict(files=len(files), entries=n, by_status=dict(by), freed=freed)


def human(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


def render(rows, meta, top):
    out = [f"# Phân loại dọn dẹp — {meta['generated']}", "",
           f"Nguồn: {', '.join(meta['sources'])}. Ổ: trống {human(meta['disk']['free'])} / {human(meta['disk']['total'])}.", ""]
    fs = [r for r in rows if r["source"] == "fs"]
    rp = [r for r in rows if r["source"] == "report"]
    out.append("| Nhóm | " + " | ".join(h for h, rs in (("Hệ thống file (không trùng)", fs), ("Report mac-cleaner (có id để dọn)", rp)) if rs) + " |")
    out.append("|---|" + "---|" * sum(1 for x in (fs, rp) if x))
    for c in (XOA, CAN, GIU):
        cells = [f"{sum(r['cls'] == c for r in rs)} mục · {human(sum(r['size'] for r in rs if r['cls'] == c))}" for rs in (fs, rp) if rs]
        out.append(f"| {LABEL[c]} | " + " | ".join(cells) + " |")
    if fs and rp:
        out.append("\nHai cột chồng nhau (mục của report nằm trong các thư mục của hệ thống file) — đừng cộng hai cột.")
    out.append("\nNÊN XOÁ: tự tạo lại, xoá chỉ tốn thời gian dựng lại. CÂN NHẮC: xoá được nhưng có hệ quả (cột *xoá thì sao*) — người dùng quyết. GIỮ: hệ thống, dữ liệu, bí mật, thứ đang dùng.")
    for c in (XOA, CAN, GIU):
        rs = sorted((r for r in rows if r["cls"] == c), key=lambda r: -r["size"])[:top]
        if not rs:
            continue
        out += ["", f"## {LABEL[c]} — {top} mục lớn nhất", "", "| Dung lượng | Đường dẫn | Mục đích | Xoá thì sao | Nguồn |", "|---|---|---|---|---|"]
        for r in rs:
            out.append(f"| {human(r['size'])} | `{r['path'].replace(HOME, '~')}` | {r['why']} | {r['effect']} | {r['source']}{' ' + r['id'] if r.get('id') else ''} |")
    h = meta.get("history")
    if h:
        out += ["", "## Lịch sử dọn (log)", "", f"{h['entries']} dòng trong {h['files']} file log · trạng thái {h['by_status']} · đã chuyển Thùng rác {human(h['freed'])}"]
    if meta.get("fs"):
        s = meta["fs"]
        out += ["", f"Quét hệ thống file: {s['files']:,} file, {human(s['bytes'])}, {s['denied']:,} chỗ không đọc được (cần quyền), {s['seconds']} giây. Gốc: {', '.join(meta['roots'])}."]
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", help="report JSON của maccleaner.py scan ('latest' = mới nhất trong DATA/reports)")
    ap.add_argument("--fs", action="store_true", help="quét hệ thống file")
    ap.add_argument("--root", action="append", help="boundary (lặp được); mặc định / = toàn bộ")
    ap.add_argument("--depth", type=int, default=6, help="độ sâu tối đa trước khi gộp cả cây (mặc định 6)")
    ap.add_argument("--min-mb", type=float, default=100, help="chỉ liệt kê mục từ N MB (mặc định 100)")
    ap.add_argument("--keep", action="append", default=[], help="tiền tố luôn GIỮ (lặp được)")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--out", help="ghi classify-<giờ>.{md,json} vào thư mục này")
    ap.add_argument("--summary", action="store_true", help="chỉ in bảng tổng + 5 mục NÊN XOÁ lớn nhất (dùng cho hook)")
    a = ap.parse_args(argv)
    if not a.report and not a.fs:
        ap.error("cần --report và/hoặc --fs")
    keep, rows, sources, meta = keep_list(a.keep), [], [], {}
    if a.report:
        path = a.report if a.report != "latest" else max(glob.glob(os.path.join(DATA, "reports", "report-[0-9]*.json")), key=os.path.getmtime)
        rr, rep = classify_report(path, keep)
        rows += rr; sources.append(os.path.basename(path))
    if a.fs:
        t0 = time.time()
        roots = a.root or ["/"]
        fr, st = scan_fs(roots, keep, a.min_mb, a.depth)
        st["seconds"] = round(time.time() - t0, 1)
        report_paths = {r["path"] for r in rows}
        rows += [r for r in fr if r["path"] not in report_paths]
        sources.append("hệ thống file"); meta.update(fs=st, roots=roots)
    du_ = os.statvfs("/System/Volumes/Data" if os.path.isdir("/System/Volumes/Data") else "/")
    meta.update(generated=time.strftime("%Y-%m-%d %H:%M"), sources=sources,
                disk=dict(total=du_.f_blocks * du_.f_frsize, free=du_.f_bavail * du_.f_frsize),
                history=history([d for d in os.environ.get("MACCLEANER_EXTRA_LOGS", "").split(":") if d]))
    md = render(rows, meta, a.top)
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        stem = os.path.join(a.out, "classify-" + time.strftime("%Y%m%d-%H%M%S"))
        open(stem + ".md", "w", encoding="utf-8").write(md)
        json.dump(dict(meta=meta, rows=rows), open(stem + ".json", "w", encoding="utf-8"), ensure_ascii=False)
        meta["written"] = stem
    if a.summary:
        xs = sorted((r for r in rows if r["cls"] == XOA), key=lambda r: -r["size"])[:5]
        print("\n".join(l for l in md.split("\n")[:9]))
        for r in xs:
            print(f"  NÊN XOÁ {human(r['size']):>9}  {r['path'].replace(HOME, '~')} — {r['why']}")
        if meta.get("written"):
            print(f"  đủ: {meta['written']}.md")
    else:
        print(md + (f"\nghi: {meta['written']}.md / .json" if meta.get("written") else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
