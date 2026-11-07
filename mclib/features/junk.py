"""Rác hệ thống mở rộng (thay `mo clean`), bổ sung cho nhóm caches/junk của scan gốc — không lặp lại mục đã có.

  python3 maccleaner.py junk [--json]
  scan → report["features"]["junk"] = {"summary": {...}, "items": [registry.entry(...)]}
  clean --category junk | junk:<kind> [--include-review] [--yes]

kind (mức an toàn / cách xoá):
  browser-cache   thư mục cache của trình duyệt (Chromium: Code Cache, GPUCache…; Firefox cache2; Safari) — safe/trash,
                  Service Worker → review. KHÔNG đụng cookie, lịch sử, mật khẩu, profile.
  app-cache       cache Chromium/Electron trong Application Support của app desktop (IDE, chat, công cụ AI) — safe/trash
  dev-cache       cache công cụ lập trình chưa có trong scan gốc (bun, yarn, nuget, go mod, CoreSimulator…) — review/trash
  ai-model        model AI tải về (ollama, LM Studio) — inventory/list-only (tải lại rất lâu)
  container-log   log trong ~/Library/{Containers,Group Containers}/*/…/Logs — safe/trash
  broken-pref     ~/Library/Preferences/*.plist không đọc được (plistlib và plutil đều lỗi) — review/trash
  ios-backup      bản sao lưu thiết bị iOS/iPadOS — inventory/list-only (có thể là bản duy nhất; cần Full Disk Access)
  firmware        file .ipsw đã tải — review/trash (tải lại được từ Apple)
Snapshot Time Machine local: chỉ liệt kê trong summary["snapshots"] kèm lệnh gợi ý (không hoàn tác được, không tự chạy).
App/trình duyệt đang chạy → mục của nó hạ xuống inventory/list-only; `clean` kiểm lại lúc dọn (khoá owners, lock_dir).
"""
import bisect
import datetime
import glob
import json
import os
import plistlib
import re

from mclib import featclean, registry, safety as S

NAME = "junk"

# (tên, bundle id, gốc dữ liệu trong ~/Library/Application Support, gốc cache trong ~/Library/Caches)
CHROMIUM_BROWSERS = (
    ("Google Chrome", "com.google.Chrome", "Google/Chrome", "Google/Chrome"),
    ("Microsoft Edge", "com.microsoft.edgemac", "Microsoft Edge", "Microsoft Edge"),
    ("Brave", "com.brave.Browser", "BraveSoftware/Brave-Browser", "BraveSoftware/Brave-Browser"),
    ("Chromium", "org.chromium.Chromium", "Chromium", "Chromium"),
    ("Vivaldi", "com.vivaldi.Vivaldi", "Vivaldi", "Vivaldi"),
    ("Arc", "company.thebrowser.Browser", "Arc/User Data", "Arc/User Data"),
    ("Opera", "com.operasoftware.Opera", "com.operasoftware.Opera", "com.operasoftware.Opera"),
)
# Trình duyệt có cache nằm hẳn trong ~/Library (đường dẫn tương đối, glob)
OTHER_BROWSERS = (
    ("Firefox", "org.mozilla.firefox", ("Caches/Firefox/Profiles/*/cache2", "Caches/Firefox/Profiles/*/startupCache")),
    ("Safari", "com.apple.Safari", ("Caches/com.apple.Safari", "Containers/com.apple.Safari/Data/Library/Caches")),
)
PROFILE_CACHES = [n for n in S.CHROMIUM_CACHE_DIRS if n not in ("Cache", "CachedData", "GrShaderCache", "ShaderCache")]
ROOT_CACHES = ("GrShaderCache", "ShaderCache", "GraphiteDawnCache", "GPUPersistentCache")
REVIEW_CACHES = ("Service Worker/CacheStorage", "Service Worker/ScriptCache")  # dữ liệu offline của web app
# Cache công cụ lập trình ngoài phạm vi scan gốc: (đường dẫn so với home, chủ, lý do)
DEV_CACHES = (
    (".bun/install/cache", "Bun", "cache gói của bun — tải lại khi cài"),
    (".yarn/berry/cache", "Yarn", "cache gói toàn cục của Yarn Berry — dự án dùng cache này cần `yarn install` lại"),
    (".nuget/packages", "NuGet", "gói NuGet toàn cục — `dotnet restore` tải lại"),
    ("go/pkg/mod", "Go", "module cache của Go — nên dọn bằng `go clean -modcache`; tải lại khi build"),
    ("Library/Developer/CoreSimulator/Caches", "Xcode Simulator", "cache dyld của simulator — tạo lại khi khởi động simulator (chậm)"),
    ("Library/Developer/Xcode/UserData/Previews", "Xcode", "simulator của SwiftUI Preview — tạo lại khi mở preview"),
)
AI_MODELS = (
    (".ollama/models", "Ollama"),
    (".lmstudio/models", "LM Studio"),
)
SNAPSHOT_RE = re.compile(r"(\d{4}-\d{2}-\d{2}-\d{6})")


def _children(d, denied=None):
    try:
        return [os.path.join(d, n) for n in sorted(os.listdir(d)) if n != ".DS_Store"]
    except PermissionError:
        if denied is not None:
            denied.append(d)
        return []
    except OSError:
        return []


def _isdir(p):
    return os.path.isdir(p) and not os.path.islink(p)


def _has_content(d):
    try:
        return any(n != ".DS_Store" for n in os.listdir(d))
    except OSError:
        return False


# ---------- parse thuần (test bằng chuỗi mẫu) ----------
def parse_snapshots(text):
    """Output `tmutil listlocalsnapshots /` → [{name, date}] (date dạng YYYY-MM-DD-HHMMSS)."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        m = SNAPSHOT_RE.search(line)
        if line.startswith("com.apple.") and m:
            out.append(dict(name=line, date=m.group(1)))
    return out


def pref_broken(path, run=S.run):
    """Lý do plist hỏng, hoặc None. Đọc không được (quyền) → None: không biết ≠ hỏng.
    plistlib không đọc kiểu OpenStep cũ nên hỏi lại `plutil -lint` trước khi kết luận."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    if not data:
        return "file rỗng — macOS coi như chưa có cấu hình"
    try:
        plistlib.loads(data)
        return None
    except Exception:  # noqa: BLE001 — plistlib ném nhiều loại lỗi khác nhau
        pass
    if run(["/usr/bin/plutil", "-lint", path], timeout=10).strip().endswith("OK"):
        return None
    return "plist không đọc được (plistlib và plutil -lint đều báo lỗi)"


# ---------- dò từng nhóm: trả về [(path, kind, safety, reason, extra)] ----------
def browser_caches(home, denied_all=None):
    lib = os.path.join(home, "Library")
    out, browsers = [], []
    for name, bid, data_rel, cache_rel in CHROMIUM_BROWSERS:
        data, cache = os.path.join(lib, "Application Support", data_rel), os.path.join(lib, "Caches", cache_rel)
        if not (_isdir(data) or _isdir(cache)):
            continue
        found = []
        profiles = [p for p in _children(data) if _isdir(p) and os.path.isfile(os.path.join(p, "Preferences"))]
        for prof in profiles:
            found += [(os.path.join(prof, n), os.path.basename(prof), n) for n in PROFILE_CACHES]
        found += [(os.path.join(data, n), "", n) for n in ROOT_CACHES]
        for prof in _children(cache):
            found += [(os.path.join(prof, n), os.path.basename(prof), n) for n in ("Cache", "Code Cache")]
        n0 = len(out)
        for p, prof, sub in found:
            if _isdir(p) and _has_content(p):
                rel = next((r for r in REVIEW_CACHES if p.endswith("/" + r)), None)
                out.append((p, "browser-cache", "review" if rel else "safe",
                            "dữ liệu offline của web app — trang tự tải lại khi online" if rel
                            else "cache trình duyệt — tự tạo lại; không đụng cookie/lịch sử/mật khẩu",
                            dict(owner=name, profile=prof, owners=[bid, name], lock_dir=data if _isdir(data) else None,
                                 label=" · ".join(x for x in (name, prof, sub) if x))))
        browsers.append(dict(name=name, bundle_id=bid, caches=len(out) - n0))
    for name, bid, globs in OTHER_BROWSERS:
        n0, denied = len(out), []
        for g in globs:
            for p in sorted(glob.glob(os.path.join(lib, g))):
                if _isdir(p) and _has_content(p):
                    out.append((p, "browser-cache", "review" if name == "Safari" else "safe",
                                "cache trình duyệt — tự tạo lại; không đụng cookie/lịch sử/mật khẩu",
                                dict(owner=name, owners=[bid, name], label=f"{name} · {os.path.basename(p)}")))
        if name == "Safari":
            _children(os.path.join(lib, "Containers", "com.apple.Safari"), denied)
        if denied_all is not None:
            denied_all += denied
        if len(out) > n0 or denied or os.path.isdir(os.path.join(lib, "Caches", "Firefox" if name == "Firefox" else bid)):
            browsers.append(dict(name=name, bundle_id=bid, caches=len(out) - n0, needsFDA=bool(denied)))
    return out, browsers


def app_caches(home):
    """Cache Chromium/Electron của app desktop: thư mục con của Application Support có dấu hiệu Chromium
    (file `Local State` hoặc thư mục `Code Cache`/`GPUCache`). Trình duyệt và app nhạy cảm bị bỏ qua."""
    asup = os.path.join(home, "Library", "Application Support")
    skip = {r.split("/")[0].lower() for _, _, r, _ in CHROMIUM_BROWSERS}
    out = []
    for d in _children(asup):
        n = os.path.basename(d)
        low = n.lower()
        if not _isdir(d) or low in skip or any(w in low for w in S.SENSITIVE_WORDS) or S.security_agent("/" + low):
            continue
        if not (os.path.isfile(os.path.join(d, "Local State")) or _isdir(os.path.join(d, "Code Cache"))
                or _isdir(os.path.join(d, "GPUCache"))):
            continue
        for c in S.CHROMIUM_CACHE_DIRS:
            p = os.path.join(d, c)
            if _isdir(p) and _has_content(p):
                rev = c in REVIEW_CACHES
                out.append((p, "app-cache", "review" if rev else "safe",
                            "dữ liệu offline của app web — tự tải lại" if rev else "cache Chromium/Electron của app — tự tạo lại",
                            dict(owner=n, owners=[n], lock_dir=d, label=f"{n} · {c}")))
    return out


def dev_caches(home):
    out = []
    for rel, owner, why in DEV_CACHES:
        p = os.path.join(home, rel)
        if _isdir(p) and _has_content(p):
            out.append((p, "dev-cache", "review", why, dict(owner=owner, label=f"{owner} · {rel}")))
    for rel, owner in AI_MODELS:
        p = os.path.join(home, rel)
        if _isdir(p) and _has_content(p):
            out.append((p, "ai-model", "inventory", "model AI local — tải lại rất lâu; gỡ bằng lệnh của chính công cụ",
                        dict(owner=owner, label=f"{owner} · models")))
    return out


def container_logs(home):
    lib = os.path.join(home, "Library")
    out = []
    for c in _children(os.path.join(lib, "Containers")):
        p = os.path.join(c, "Data", "Library", "Logs")
        if _isdir(p) and _has_content(p):
            n = os.path.basename(c)
            out.append((p, "container-log", "safe", "log của app sandbox — app tự tạo lại",
                        dict(owner=n, owners=[n], label=f"{n} · Logs")))
    for c in _children(os.path.join(lib, "Group Containers")):
        p = os.path.join(c, "Library", "Logs")
        if _isdir(p) and _has_content(p):
            n = os.path.basename(c)
            bid = n.split(".", 1)[1] if re.match(r"^[A-Z0-9]{10}\.", n) else n
            out.append((p, "container-log", "safe", "log của nhóm app — app tự tạo lại",
                        dict(owner=bid, owners=[bid], label=f"{n} · Logs")))
    return out


def broken_prefs(home, run=S.run):
    pref = os.path.join(home, "Library", "Preferences")
    out = []
    for p in _children(pref) + _children(os.path.join(pref, "ByHost")):
        n = os.path.basename(p)
        if not n.endswith(".plist") or os.path.islink(p) or not os.path.isfile(p):
            continue
        why = pref_broken(p, run)
        if why:
            out.append((p, "broken-pref", "review", why + " · app sẽ tạo lại cấu hình mặc định",
                        dict(owner=n[:-6], owners=[n[:-6]], label=n)))
    return out


def ios_backups(home, denied):
    d = os.path.join(home, "Library", "Application Support", "MobileSync", "Backup")
    out = []
    for b in _children(d, denied):
        if not _isdir(b):
            continue
        try:
            os.listdir(b)
        except PermissionError:
            denied.append(b)  # thấy thư mục nhưng không đọc được bên trong → cần Full Disk Access
        info = S.plist(os.path.join(b, "Info.plist"))
        last = info.get("Last Backup Date")
        extra = dict(owner="Finder", device=str(info.get("Device Name", "")), product=str(info.get("Product Type", "")),
                     ios=str(info.get("Product Version", "")),
                     last_backup=last.isoformat() if isinstance(last, datetime.datetime) else None)
        extra["label"] = " · ".join(x for x in (extra["device"] or os.path.basename(b), extra["product"], extra["ios"]) if x)
        out.append((b, "ios-backup", "inventory",
                    "bản sao lưu thiết bị — có thể là bản duy nhất; xoá trong Finder › thiết bị › Quản lý bản sao lưu", extra))
    return out


def firmware(home):
    pats = [os.path.join(home, "Library", "iTunes", "*Software Updates", "*.ipsw"),
            os.path.join(home, "Downloads", "*.ipsw"), os.path.join(home, "Desktop", "*.ipsw")]
    out = []
    for g in pats:
        for p in sorted(glob.glob(g)):
            if os.path.isfile(p) and not os.path.islink(p):
                out.append((p, "firmware", "review", "firmware iOS/iPadOS đã tải — Apple cho tải lại khi cần khôi phục",
                            dict(owner="Apple", label=os.path.basename(p))))
    return out


def snapshots(run=S.run):
    snaps = parse_snapshots(run(["/usr/bin/tmutil", "listlocalsnapshots", "/"], timeout=20))
    for s in snaps:
        s["delete_command"] = f"tmutil deletelocalsnapshots {s['date']}"
    return snaps


# ---------- gộp ----------
class _Covered:
    """Đường dẫn đã có trong mục dọn được của scan gốc (bằng nhau, là cha, hoặc là con) → không lặp lại."""

    def __init__(self, items):
        self.paths = sorted({i["path"].rstrip("/") for i in items
                             if i.get("category") in ("caches", "junk") and i.get("path")})
        self.set = set(self.paths)

    def __call__(self, p):
        p = p.rstrip("/")
        q = p
        while q and q != "/":
            if q in self.set:
                return True
            q = os.path.dirname(q)
        k = bisect.bisect_left(self.paths, p + "/")
        return k < len(self.paths) and self.paths[k].startswith(p + "/")


def gather(home, base_items=(), running=None, run=S.run, du=S.du):
    """Dò mọi nhóm → (summary, items). running: tập tên app đang chạy (None = tự hỏi ps)."""
    denied = []
    found, browsers = browser_caches(home, denied)
    found += app_caches(home) + dev_caches(home) + container_logs(home) + broken_prefs(home, run)
    found += ios_backups(home, denied) + firmware(home)
    covered = _Covered(base_items)
    kept = [f for f in found if not covered(f[0])]
    names = featclean.running_names(run) if running is None else running
    sizes = du([f[0] for f in kept])
    items, skipped_running = [], 0
    for p, kind, safety, reason, extra in kept:
        mode = "list-only" if safety == "inventory" else "trash"
        if mode == "trash" and featclean.owner_running(extra, names):
            safety, mode, skipped_running = "inventory", "list-only", skipped_running + 1
            reason = "app đang chạy — đóng app rồi quét lại · " + reason
        label = extra.pop("label", "")
        if sizes.get(p) == 0 and kind in ("browser-cache", "app-cache", "dev-cache", "container-log"):
            continue  # thư mục rỗng thực tế: không đáng liệt kê
        items.append(registry.entry(NAME, p, name=label, size=sizes.get(p), safety=safety, delete_mode=mode,
                                    reason=reason, kind=kind, **extra))
    by_kind = {}
    for e in items:
        k = by_kind.setdefault(e["kind"], dict(count=0, size=0, cleanable=0))
        k["count"] += 1
        k["size"] += e["size"] or 0
        if e["delete_mode"] == "trash":
            k["cleanable"] += e["size"] or 0
    for b in browsers:
        b["running"] = featclean.owner_running(dict(owners=[b["bundle_id"], b["name"]]), names)
    snaps = snapshots(run)
    summary = dict(kinds=by_kind, browsers=browsers, snapshots=snaps,
                   snapshot_hint=("liệt kê: tmutil listlocalsnapshots / · xoá (không hoàn tác, có thể cần sudo): "
                                  "tmutil deletelocalsnapshots <ngày>") if snaps else "",
                   needsFDA=sorted(set(denied)), deduped=len(found) - len(kept), skipped_running=skipped_running)
    return summary, items


def collect(ctx):
    summary, items = gather(ctx.home, ctx.items, run=ctx.run, du=ctx.du)
    return {"summary": summary, "items": items}


def cli(args):
    summary, items = gather(S.HOME)
    if args.json:
        print(json.dumps({"summary": summary, "items": items}, ensure_ascii=False))
        return 0
    for e in sorted(items, key=lambda e: -(e["size"] or 0)):
        print(f"{S.fmt(e['size']):>9}  {e['safety']:9} {e['delete_mode']:9} {e['kind']:13} {e['name']}")
    for s in summary["snapshots"]:
        print(f"{'—':>9}  snapshot  list-only tm-snapshot    {s['name']}  (xoá: {s['delete_command']})")
    if summary["needsFDA"]:
        print("Cần Full Disk Access để đọc: " + ", ".join(summary["needsFDA"]))
    total = sum(e["size"] or 0 for e in items if e["delete_mode"] == "trash")
    print(f"{len(items)} mục · dọn được ~{S.fmt(total)} (python3 maccleaner.py clean --category junk[:<kind>] sau khi scan)")
    return 0


def register(subparsers):
    p = subparsers.add_parser(NAME, help="rác hệ thống mở rộng: cache trình duyệt/app, log container, plist hỏng… (chỉ xem)")
    p.add_argument("--json", action="store_true", help="in JSON")
    p.set_defaults(func=cli)
