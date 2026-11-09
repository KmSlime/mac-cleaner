"""Rào an toàn và dọn dẹp dùng chung: vùng bảo vệ, chặn cứng (SIP/thư mục gốc), app đang chạy, Thùng rác,
log `logs/clean-*.jsonl`, đo dung lượng, chế độ xem trước / --yes / --force.

Mọi tính năng (maccleaner.py và mclib/features/*) PHẢI đi qua các hàm ở đây khi đánh dấu hay xoá một đường dẫn.
"""
import fnmatch
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HOME = str(Path.home())
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # thư mục chứa maccleaner.py
DATA = os.environ.get("MACCLEANER_DATA") or ROOT  # reports/ và logs/; app .app đặt ra ngoài bundle
DATA_VOL = "/System/Volumes/Data"

# Không bao giờ dọn: khoá và bí mật.
PROTECTED = [os.path.expanduser(p) for p in ("~/Library/Keychains*", "~/.ssh*", "~/.gnupg*")]
# Trong ~/Library chỉ dọn ở các vùng được quét; mọi thư mục khác (dữ liệu ứng dụng, thư mục đồng bộ…) được bảo vệ.
LIBRARY_CLEANABLE = ("Caches", "Logs", "Containers", "Group Containers", "Preferences", "Developer")
# Thư mục ẩn ở home: chỉ dọn các vùng cache này; còn lại (dữ liệu, model, cấu hình của các tool) được bảo vệ.
DOT_CLEANABLE = (".cache", ".npm/_cacache", ".npm/_npx", ".gradle/caches",
                 ".bun/install/cache", ".yarn/berry/cache", ".nuget/packages")  # 3 mục cuối: module junk
# Thư mục cache Chromium/Electron nằm trong Application Support (ngoài ~/Library/Caches): chỉ dọn được khi mục có
# kind browser-cache/app-cache VÀ tên thư mục cuối đúng một trong các tên này (module junk).
CHROMIUM_CACHE_DIRS = ("Cache", "Code Cache", "GPUCache", "GPUPersistentCache", "DawnCache", "DawnGraphiteCache",
                       "DawnWebGPUCache", "GraphiteDawnCache", "GrShaderCache", "ShaderCache", "CachedData",
                       "Service Worker/CacheStorage", "Service Worker/ScriptCache")
# clean chỉ chạm các vùng này; path phải sâu ít nhất 2 cấp dưới vùng.
ALLOWED_ROOTS = [HOME, "/Library/Caches", "/Library/Logs"]
CLEANABLE = {"safe", "review"}
# Xoá mạnh (--force) cho phép xoá MỌI mục hiện trên dashboard, trừ những chỗ macOS khoá (SIP) hay thư mục gốc.
SIP_ROOTS = ("/System/", "/usr/", "/bin/", "/sbin/", "/private/var/db/", "/Library/Apple/")
SIP_OK = ("/usr/local/",)
HOME_FOLDERS = {"Library", "Documents", "Desktop", "Downloads", "Applications", "Movies", "Music", "Pictures", "Public"}
# Mục "sót lại" nằm trong thư mục Library thường được bảo vệ, nhưng vẫn dọn được (mức cân nhắc)
LEFTOVER_LIBRARY = ("Application Support", "LaunchAgents")
# Gỡ app chọn đích danh (`uninstall`, kind "app-uninstall"): dọn thêm dữ liệu của CHÍNH app đó ở các thư mục này.
UNINSTALL_LIBRARY = LEFTOVER_LIBRARY + ("Saved Application State", "HTTPStorages", "WebKit", "Cookies")
TRASH_BIN = "/usr/bin/trash"  # có từ macOS 15; máy cũ hơn dùng phương án chuyển thẳng vào ~/.Trash

# ---- Rào an toàn học từ cách Mole (tw93/mole) vận hành — viết lại theo ý, không chép mã/danh sách (Mole là GPL-3.0) ----
# Thư mục gốc không bao giờ xoá dù đã chọn đích danh (so sánh không phân biệt hoa thường: APFS mặc định như vậy,
# nên "/SYSTEM" cũng là "/System").
DENY_TREES = ("/system/", "/usr/", "/bin/", "/sbin/", "/etc/", "/private/etc/", "/var/", "/private/var/",
              "/library/apple/", "/library/keychains/", "/library/extensions/", "/dev/", "/cores/")
DENY_TREE_OK = ("/usr/local/",)
DENY_EXACT = {"/applications", "/library", "/library/application support", "/library/caches", "/library/logs",
              "/users", "/users/shared", "/volumes", "/opt", "/opt/homebrew", "/usr/local", "/private", "/tmp"}
# Nằm trong ~/Library/Caches nhưng là dữ liệu ĐANG DÙNG: xoá thì hỏng thứ khác chứ không chỉ phải tải lại.
LIVE_DATA_CACHES = {
    "pypoetry": "chứa virtualenv đang dùng của các project Poetry",
    "org.r-project.r": "thư viện renv của project R trỏ vào đây",
    "com.apple.fontregistry": "xoá làm hỏng hiển thị font của macOS",
    "com.apple.spotlight": "xoá làm hỏng tìm kiếm Spotlight",
}
# Phần mềm bảo mật / quản lý thiết bị (EDR, MDM): xoá bất cứ gì của chúng có thể bị coi là can thiệp (tamper)
# và báo động phía bảo mật công ty. Không bao giờ xoá; gỡ bằng trình gỡ của hãng.
SECURITY_AGENT_IDS = ("com.crowdstrike", "com.sentinelone", "com.jamf", "com.jamfsoftware", "io.kandji", "com.kandji",
                      "com.microsoft.wdav", "com.paloaltonetworks", "com.sophos", "com.trendmicro", "com.mcafee",
                      "com.vmware.carbonblack", "com.carbonblack", "com.cisco.amp", "com.mosyle", "com.addigy",
                      "com.jumpcloud", "com.zscaler", "com.netskope", "com.forcepoint", "com.tanium")
# Tên chứa từ này → dữ liệu nhạy cảm (mật khẩu, khoá, ví): không bao giờ coi là "sót lại".
SENSITIVE_WORDS = ("1password", "bitwarden", "lastpass", "keepass", "dashlane", "enpass", "keychain", "authenticator",
                   "authy", "ssh", "gpg", "gnupg", "wallet", "ledger", "trezor", "electrum", "exodus", "metamask")


# ---------- helpers ----------
def run(cmd, timeout=60):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def du(paths, log=None):
    """Kích thước (byte) của nhiều path bằng một vài lệnh du (nhanh hơn walk Python)."""
    out = {}
    paths = [p for p in dict.fromkeys(paths) if os.path.lexists(p)]
    for i in range(0, len(paths), 200):
        for line in run(["du", "-sk", *paths[i:i + 200]], timeout=900).splitlines():
            k, _, p = line.partition("\t")
            if k.isdigit():
                out[p] = int(k) * 1024
        if log and len(paths) > 200:
            log(f"  đo dung lượng: {min(i + 200, len(paths))}/{len(paths)}")
    return out


def disk_usage():
    """shutil.disk_usage của volume dữ liệu (Data volume nếu có, không thì /)."""
    return shutil.disk_usage(DATA_VOL if os.path.isdir(DATA_VOL) else "/")


def fmt(n):
    if n is None:
        return "—"
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


def plist(path):
    try:
        with open(path, "rb") as f:
            return plistlib.load(f)
    except Exception:
        return {}


# ---------- vùng bảo vệ ----------
def is_protected(path, kind=""):
    if any(fnmatch.fnmatch(path, p) or path.startswith(p.rstrip("*") + "/") for p in PROTECTED):
        return True
    lib = os.path.join(HOME, "Library") + "/"
    if path.startswith(lib):
        top = path[len(lib):].split("/")[0]
        extra = (LEFTOVER_LIBRARY if kind in ("app-leftover", "agent-leftover") else
                 UNINSTALL_LIBRARY if kind == "app-uninstall" else ())
        return top not in LIBRARY_CLEANABLE and top not in extra and not _feature_cleanable(path, kind, top)
    if path.startswith(HOME + "/."):
        rel = path[len(HOME) + 1:]
        return not any(rel == c or rel.startswith(c + "/") for c in DOT_CLEANABLE)
    return False


def _feature_cleanable(path, kind, top):
    """Ngoại lệ hẹp cho module junk: cache Chromium/Electron trong Application Support, firmware .ipsw của iTunes."""
    if kind in ("browser-cache", "app-cache") and top == "Application Support":
        rel = path.split("/Library/Application Support/", 1)[-1]  # phải là <app>/…/<tên cache>, không phải chính thư mục app
        return (any(rel.endswith("/" + n) for n in CHROMIUM_CACHE_DIRS) and not os.path.islink(path)
                and not any(w in rel.lower() for w in SENSITIVE_WORDS))  # mật khẩu/ví: giữ, kể cả thư mục cache
    if kind == "firmware" and top == "iTunes":
        return path.lower().endswith(".ipsw") and not os.path.islink(path)
    return False


def guard_reason(path):
    """Lý do giữ lại một mục dù vị trí của nó thường dọn được (cache là dữ liệu sống, phần mềm bảo mật), hoặc None."""
    low = path.lower()
    if security_agent(low):
        return "thuộc phần mềm bảo mật/quản lý thiết bị — xoá có thể bị coi là can thiệp; gỡ bằng trình gỡ của hãng"
    caches = os.path.join(HOME, "Library", "Caches").lower() + "/"
    if low.startswith(caches):
        top = low[len(caches):].split("/")[0]
        for k, why in LIVE_DATA_CACHES.items():
            if top == k or top.startswith(k + "."):
                return why
    return None


def security_agent(low_path):
    return any(f"/{i}" in low_path or f".{i}" in low_path for i in SECURITY_AGENT_IDS)


def item(category, kind, name, path, *, owner="", version="", safety="info", action=None, note="", size=None, **extra):
    """Một mục của report["items"] (định dạng dashboard/app đang dùng). Mục dọn được nằm trong vùng bảo vệ → protected."""
    if safety in CLEANABLE and is_protected(path, kind):
        safety, action, note = "protected", None, (note + " · " if note else "") + "được bảo vệ"
    elif safety in CLEANABLE and (why := guard_reason(path)):
        safety, action, note = "protected", None, why
    iid = hashlib.sha1(f"{category}|{kind}|{path}".encode()).hexdigest()[:10]
    return dict(id=iid, category=category, kind=kind, name=name, owner=owner, version=version, path=path,
                size=size, safety=safety, action=action, note=note, **extra)


# ---------- app đang chạy ----------
def running_bundle_ids():
    """Bundle id của các app đang chạy (từ đường dẫn tiến trình *.app/Contents/MacOS/…)."""
    out, seen = set(), set()
    for line in run(["/bin/ps", "-axo", "comm="], timeout=10).splitlines():
        m = re.match(r"(.+?\.app)/Contents/MacOS/", line.strip())
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            bid = plist(os.path.join(m.group(1), "Contents/Info.plist")).get("CFBundleIdentifier")
            if bid:
                out.add(bid.lower())
    return out


def cache_owner_running(path, running):
    """Cache (~/Library/Caches/<id>, Containers/<id>/…/Caches) của một app đang chạy? Xoá lúc đó dễ hỏng dữ liệu app."""
    lib = os.path.join(HOME, "Library").lower() + "/"
    low = path.lower()
    if not low.startswith(lib):
        return False
    parts = low[len(lib):].split("/")
    if parts[0] not in ("caches", "containers") or len(parts) < 2:
        return False
    name = parts[1]
    return any(name == r or name.startswith(r + ".") for r in running)


# ---------- clean ----------
def hard_deny(p):
    """Chỗ không bao giờ xoá, kể cả --force: macOS khoá (SIP), thư mục gốc, phần mềm bảo mật, hoặc thứ Mac Cleaner
    đang chạy trên đó. So sánh không phân biệt hoa thường (APFS)."""
    low = p.rstrip("/").lower()
    if (p.startswith(SIP_ROOTS) and not p.startswith(SIP_OK)) or (
            (low + "/").startswith(DENY_TREES) and not (low + "/").startswith(DENY_TREE_OK)):
        return "thuộc hệ thống macOS (SIP khoá) — không xoá được"
    home = HOME.lower()
    if (len(Path(p).parts) < 3 or low in DENY_EXACT or low == home or home.startswith(low + "/")
            or re.fullmatch(r"/users/[^/]+", low)):
        return "thư mục gốc — không xoá"
    if security_agent(low):
        return "thuộc phần mềm bảo mật/quản lý thiết bị — chỉ gỡ bằng trình gỡ của hãng"
    if p.startswith(HOME + "/") and len(Path(p).relative_to(HOME).parts) == 1 and Path(p).name in HOME_FOLDERS:
        return "thư mục chuẩn của macOS — không xoá"
    for used in (os.path.realpath(sys.executable), ROOT, DATA):
        if used == p or used.startswith(p + "/"):
            return "Mac Cleaner đang dùng thư mục này (Python / chính nó / report)"
    return None


def action_of(it, force=False):
    """Cách dọn: theo report với mục dọn được; --force thì mọi mục khác chuyển vào Thùng rác (Thùng rác: đổ)."""
    if it.get("safety") in CLEANABLE and it.get("action"):
        return it["action"]
    if not force:
        return None
    if it.get("kind") == "trash":
        return "empty-trash"
    return it.get("action") or "trash"


def validate(it, force=False):
    """Lý do từ chối dọn item, hoặc None nếu được phép. Kiểm lại lúc clean, không tin report.
    force=True (người dùng chọn đích danh trên dashboard): bỏ qua mức an toàn và vùng bảo vệ, chỉ giữ chặn cứng."""
    p = it.get("path", "")
    act = action_of(it, force)
    if act is None:
        return "item chỉ để kiểm kê — chọn xoá đích danh (--force) nếu thật sự muốn xoá"
    if not os.path.isabs(p) or os.path.normpath(p) != p.rstrip("/"):
        return "đường dẫn không hợp lệ"
    if act == "empty-trash":
        return None if p == os.path.join(HOME, ".Trash") else "đường dẫn Thùng rác không hợp lệ"
    deny = hard_deny(p)
    if deny:
        return deny
    if not force:
        if is_protected(p, it.get("kind", "")):
            return "đường dẫn được bảo vệ"
        if why := guard_reason(p):
            return why
        root = next((r for r in ALLOWED_ROOTS if p.startswith(r + "/")), None)
        if not root or len(Path(p).relative_to(root).parts) < (2 if root == HOME else 1):
            return "ngoài vùng cho phép dọn"
    if isinstance(act, list):
        if act[:3] != ["xcrun", "simctl", "delete"]:
            return "lệnh không nằm trong danh sách cho phép"
    elif act != "trash":
        return "action không hỗ trợ"
    elif not os.path.lexists(p):
        return "không còn tồn tại"
    return None


def to_trash(p):
    """Chuyển vào Thùng rác: /usr/bin/trash (macOS 15+, có "Đưa trở lại"); máy cũ hơn thì chuyển thẳng vào ~/.Trash."""
    if os.path.exists(TRASH_BIN):
        r = subprocess.run([TRASH_BIN, p], capture_output=True, text=True)
        return None if r.returncode == 0 else (r.stderr or r.stdout).strip()[:200] or f"trash thoát mã {r.returncode}"
    dest = os.path.join(HOME, ".Trash", Path(p).name)
    if os.path.lexists(dest):
        dest += time.strftime(" %H.%M.%S")
    try:
        shutil.move(p, dest)
        return None
    except OSError as e:
        return str(e)[:200]


def permanent_reason(it):
    """Rào thêm cho xoá vĩnh viễn (mode "permanent"), chạy SAU validate(force=False). Lý do từ chối hoặc None.
    Chỉ mục quét gốc dọn được (safe/review, action "trash"), id khớp report, nằm trong home (sâu ≥ 2 cấp) và thư mục
    cha thật (realpath) cũng ở trong home. Ngoài home (vd /Library/Caches), Thùng rác, lệnh đặc biệt → chỉ Thùng rác."""
    p = it.get("path", "")
    if it.get("kind") == "trash":
        return "Thùng rác: dùng Đổ Thùng rác, không xoá vĩnh viễn từng mục"
    if it.get("safety") not in CLEANABLE or it.get("action") != "trash":
        return "chỉ xoá vĩnh viễn được mục dọn được chuyển-vào-Thùng-rác"
    iid = hashlib.sha1(f"{it.get('category', '')}|{it.get('kind', '')}|{p}".encode()).hexdigest()[:10]
    if it.get("id") != iid:
        return "id không khớp đường dẫn trong report"
    home = HOME.rstrip("/")
    if not p.startswith(home + "/") or len(Path(p).relative_to(home).parts) < 2:
        return "ngoài thư mục home — chỉ chuyển vào Thùng rác"
    real_home = os.path.realpath(home)
    if not os.path.realpath(os.path.dirname(p)).startswith(real_home + "/"):
        return "thư mục cha trỏ ra ngoài home (symlink) — chỉ chuyển vào Thùng rác"
    return None


def remove_tree(p):
    """Xoá hẳn một đường dẫn (file, symlink, thư mục). Trả lỗi hoặc None."""
    try:
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p)
        else:
            os.remove(p)
        return None
    except OSError as e:
        return str(e)[:200]


def trash_then_delete(p):
    """Xoá vĩnh viễn an toàn: đổi tên mục vào ~/.Trash dưới một tên mới chưa tồn tại (os.rename — nguyên tử, cùng ổ;
    khác ổ thì lỗi và không đụng gì), rồi xoá ĐÚNG bản vừa chuyển. Không bao giờ rmtree đường dẫn gốc.
    Xoá hẳn lỗi giữa chừng → phần còn lại vẫn nằm trong Thùng rác (khôi phục được)."""
    trash = os.path.join(HOME, ".Trash")
    try:
        os.makedirs(trash, exist_ok=True)
    except OSError as e:
        return f"không tạo được Thùng rác: {str(e)[:160]}"
    dest = os.path.join(trash, f"{Path(p).name} (mac-cleaner {time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{time.monotonic_ns() % 10**6})")
    if os.path.lexists(dest):
        return "trùng tên trong Thùng rác — thử lại"
    try:
        os.rename(p, dest)
    except OSError as e:
        return f"không chuyển được vào Thùng rác: {str(e)[:160]}"
    if not os.path.lexists(dest):
        return "đã chuyển nhưng không thấy bản trong Thùng rác để xoá hẳn"
    err = remove_tree(dest)
    return f"đã vào Thùng rác nhưng xoá hẳn lỗi: {err}" if err else None


def run_action(it, act, permanent=False):
    """Thực thi một mục đã qua validate. Trả về lỗi (chuỗi) hoặc None.
    permanent=True (đã qua permanent_reason): chuyển vào Thùng rác rồi xoá hẳn đúng bản đó."""
    p = it["path"]
    if act == "empty-trash":
        r = subprocess.run(["/usr/bin/osascript", "-e", 'tell application "Finder" to empty trash'], capture_output=True, text=True)
        return None if r.returncode == 0 else (r.stderr or r.stdout).strip()[:200]
    if isinstance(act, list):
        r = subprocess.run(act, capture_output=True, text=True)
        return None if r.returncode == 0 else (r.stderr or r.stdout).strip()[:200]
    if p.startswith(os.path.join(HOME, "Library", "LaunchAgents") + "/") and p.endswith(".plist"):
        subprocess.run(["/bin/launchctl", "bootout", f"gui/{os.getuid()}", p], capture_output=True)  # ngừng agent trước khi bỏ plist
    return trash_then_delete(p) if permanent else to_trash(p)


def select(items, ids=None, categories=None, include_review=False):
    ids = set(ids or [])
    out = []
    for it in items:
        if ids:
            if it["id"] in ids:
                out.append(it)
            continue
        if categories and it["category"] not in categories:
            continue
        if it["safety"] == "safe" or (include_review and it["safety"] == "review"):
            out.append(it)
    return out


MODES = ("trash", "permanent")


def clean(rep, chosen, yes=False, log=print, force=False, mode="trash"):
    """Dọn các mục đã chọn. yes=False: chỉ xem trước (không chạm gì). Mỗi mục được validate lại; --yes ghi
    logs/clean-<ts>.jsonl. mode="permanent": thêm rào permanent_reason, xoá hẳn thay vì để trong Thùng rác
    (không dùng chung với force)."""
    if mode not in MODES:
        raise ValueError(f"mode không hợp lệ: {mode}")
    permanent = mode == "permanent"
    if permanent and force:
        raise ValueError("xoá vĩnh viễn không dùng chung với --force")
    results, freed = [], 0
    log_path = os.path.join(DATA, "logs", "clean-" + time.strftime("%Y%m%d-%H%M%S") + ".jsonl")
    if yes:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
    running = running_bundle_ids() if yes else set()
    for it in chosen:
        why = validate(it, force) or (permanent_reason(it) if permanent else None)
        if not why and yes and it.get("category") == "caches" and cache_owner_running(it.get("path", ""), running):
            why = "app sở hữu cache đang chạy — đóng app rồi dọn lại"
        status = "skip" if why else "dry-run" if not yes else "ok"
        if not why and yes:
            err = run_action(it, action_of(it, force), permanent=permanent)
            if err:
                status, why = "error", err
        if status in ("ok", "dry-run"):
            freed += it.get("size") or 0
        res = dict(id=it["id"], path=it["path"], size=it.get("size"), status=status, reason=why or "", mode=mode)
        results.append(res)
        log(f"[{status:7}] {fmt(it.get('size')):>9}  {it['path']}" + (f"  ({why})" if why else "")
            + ("  [xoá vĩnh viễn]" if permanent and status in ("ok", "dry-run") else ""))
        if yes:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(res, ensure_ascii=False) + "\n")
    return dict(results=results, freed=freed, log=log_path if yes else None, mode=mode)
