#!/usr/bin/env python3
"""mac-cleaner: kiểm kê toàn bộ máy macOS (app, plugin, extension, git, cache, junk) và dọn dẹp an toàn.

  python3 maccleaner.py scan [--out DIR] [--json]      quét, ghi reports/report-<ts>.{json,html,md}
  python3 maccleaner.py clean --report R [lọc] [--yes] dọn item trong report (mặc định chỉ preview)
  python3 maccleaner.py <tính năng> ...                 lệnh con nạp từ mclib/features/ (vd `status`)

Chỉ dùng thư viện chuẩn. Xoá = chuyển vào Thùng rác (/usr/bin/trash), khôi phục được.
"""
import argparse
import glob
import html
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:  # `python3 -I` bỏ thư mục script khỏi sys.path; thêm vào CUỐI để thư viện chuẩn luôn thắng
    sys.path.append(ROOT)
sys.dont_write_bytecode = True  # không ghi __pycache__ của mclib vào bundle .app đã ký (hay vào repo)

from mclib import featclean, registry, safety  # noqa: E402
# Rào an toàn + dọn nằm ở mclib/safety.py; giữ tên cũ ở đây cho test/caller.
from mclib.safety import (  # noqa: E402,F401
    ALLOWED_ROOTS, CLEANABLE, DATA, DATA_VOL, DENY_EXACT, DENY_TREE_OK, DENY_TREES, DOT_CLEANABLE, HOME,
    HOME_FOLDERS, LEFTOVER_LIBRARY, LIBRARY_CLEANABLE, LIVE_DATA_CACHES, PROTECTED, SECURITY_AGENT_IDS,
    SENSITIVE_WORDS, SIP_OK, SIP_ROOTS, TRASH_BIN, action_of, cache_owner_running, clean, disk_usage, du, fmt,
    guard_reason, hard_deny, is_protected, item, plist, run, run_action, running_bundle_ids, security_agent, select,
    to_trash, validate)

# Cache do macOS tự quản lý: dọn được nhưng để mức "cân nhắc".
SYSTEM_CACHE_PREFIX = "com.apple."
# Cache dọn được nhưng tải lại rất lâu → để "cân nhắc" kèm lý do.
SLOW_REFETCH_CACHES = {"ms-playwright": "trình duyệt của Playwright — tải lại vài trăm MB",
                       "jetbrains": "index của IDE JetBrains — mở project sẽ index lại rất lâu"}
ORPHAN_MIN_IDLE_DAYS = 30   # dữ liệu còn được ghi trong 30 ngày qua thì app chắc chắn còn dùng nó
ARTIFACT_RECENT_DAYS = 7    # build artifact vừa dùng trong tuần → đang làm dự án, không đề xuất dọn


ARTIFACT_DIRS = {"node_modules", "Pods", ".build", ".venv", "venv", "DerivedData", ".gradle", ".dart_tool",
                 ".next", ".nuxt", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".turbo", ".parcel-cache"}
# build/dist/target chỉ tính khi thư mục cha là project (có file đánh dấu).
MARKED_ARTIFACTS = {"build", "dist", "target", "coverage"}
PROJECT_MARKERS = ("package.json", "build.gradle", "build.gradle.kts", "Package.swift", "pyproject.toml",
                   "Cargo.toml", "pubspec.yaml", "Podfile", "setup.py", "pom.xml")
WALK_PRUNE = {".git", "Library", "__pycache__"}
# Ở gốc home chỉ đi vào thư mục ẩn này (repo cấu hình); các thư mục ẩn khác là dữ liệu/cache của tool.
WALK_HIDDEN_OK = {".config"}
INSTALLER_EXT = (".dmg", ".pkg", ".mpkg", ".xip", ".iso")
BACKUP_MARK = re.compile(r"(?i)(\.bak|\.backup|\.old|\.orig|\.prev|[_-]backup|~$)")
BACKUP_FIND = ["*.bak", "*.bak[0-9]*", "*.bak.*", "*.backup", "*.backup.*", "*.backup-*", "*.old", "*.orig",
               "*~", "*_backup*", "*-backup*", "*.prev"]
BACKUP_SKIP = ("/opt/homebrew/Cellar/", "/Library/Ruby/", "/node_modules/", "/build/", "/DerivedData/",
               "/Library/Containers/")
BACKUP_SKIP_RE = re.compile(r"/\.[^/]+/extensions/")  # extension của editor: bản cũ do editor tự quản lý
BROWSERS = {"Google Chrome": "Google/Chrome", "Microsoft Edge": "Microsoft Edge", "Brave": "BraveSoftware/Brave-Browser",
            "Arc": "Arc/User Data", "Chromium": "Chromium", "Vivaldi": "Vivaldi", "Opera": "com.operasoftware.Opera"}
PLUGIN_DIRS = ["Internet Plug-Ins", "Audio/Plug-Ins/Components", "Audio/Plug-Ins/VST", "Audio/Plug-Ins/VST3",
               "Audio/Plug-Ins/HAL", "QuickLook", "Spotlight", "PreferencePanes", "Input Methods", "Services",
               "Screen Savers", "Contextual Menu Items", "Application Support/Adobe/Plug-Ins"]


# ---------- helpers ----------
def vkey(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or ""))


def idle_days(path):
    try:
        return (time.time() - os.lstat(path).st_mtime) / 86400
    except OSError:
        return None


def children(d):
    try:
        return [os.path.join(d, n) for n in sorted(os.listdir(d)) if n != ".DS_Store"]
    except OSError:
        return []


# ---------- applications ----------
def scan_apps():
    roots = ["/Applications", "/Applications/Utilities", os.path.join(HOME, "Applications"),
             "/System/Applications", "/System/Applications/Utilities"]
    seen, items = set(), []
    for r in roots:
        for app in glob.glob(os.path.join(r, "*.app")) + glob.glob(os.path.join(r, "*/*.app")):
            real = os.path.realpath(app)
            if real in seen or ".app/" in real:
                continue
            seen.add(real)
            info = plist(os.path.join(app, "Contents/Info.plist"))
            src = ("macOS" if app.startswith("/System/") else
                   "App Store" if os.path.exists(os.path.join(app, "Contents/_MASReceipt")) else "Cài thủ công")
            items.append(item("applications", "app", info.get("CFBundleDisplayName") or info.get("CFBundleName") or Path(app).stem,
                              app, owner=src, version=str(info.get("CFBundleShortVersionString", "")),
                              bundle_id=info.get("CFBundleIdentifier", ""),
                              note=f"gỡ sạch bằng `maccleaner.py uninstall {info.get('CFBundleIdentifier') or Path(app).stem}`"
                              if src != "macOS" else ""))
    return items


# ---------- plugins ----------
def parse_pluginkit(text):
    """Tách output `pluginkit -mAvvv` thành list dict {id, version, path, parent, name, sdk}."""
    out, cur = [], None
    for line in text.splitlines():
        m = re.match(r"^\s*[+\-!=?]?\s*([\w.\-]+)\((.*)\)\s*$", line)
        if m and "=" not in line:
            cur = {"id": m.group(1), "version": m.group(2)}
            out.append(cur)
        elif cur is not None and " = " in line:
            k, v = (s.strip() for s in line.split(" = ", 1))
            cur[{"Path": "path", "Parent Bundle": "parent", "Display Name": "name", "SDK": "sdk", "Parent Name": "parent_name"}.get(k, k)] = v
    return [p for p in out if p.get("path")]


def owner_of(path, parent=""):
    if parent:
        return Path(parent).stem
    m = re.search(r"/([^/]+)\.app/", path)
    if m:
        return m.group(1)
    return "macOS" if path.startswith("/System/") else "—"


def scan_plugins():
    items = []
    for p in parse_pluginkit(run(["pluginkit", "-mAvvv"])):
        items.append(item("plugins", "app-extension", p.get("name") or p["id"], p["path"],
                          owner=owner_of(p["path"], p.get("parent", "")), version=p["version"],
                          bundle_id=p["id"], note=p.get("sdk", "")))
    for base in ["/Library", os.path.join(HOME, "Library")]:
        for d in PLUGIN_DIRS:
            for p in children(os.path.join(base, d)):
                info = plist(os.path.join(p, "Contents/Info.plist"))
                bid = info.get("CFBundleIdentifier", "")
                items.append(item("plugins", d.split("/")[-1], info.get("CFBundleName") or Path(p).stem, p,
                                  owner=bid.split(".")[1] if bid.count(".") >= 2 else "—",
                                  version=str(info.get("CFBundleShortVersionString", "")), bundle_id=bid,
                                  note="xoá thủ công nếu app chủ đã gỡ"))
    for line in run(["systemextensionsctl", "list"]).splitlines():
        cols = line.split("\t")
        if len(cols) >= 6 and "." in cols[3]:
            items.append(item("plugins", "system-extension", cols[4].strip(), cols[3].strip(), owner=cols[2].strip(),
                              version=cols[4].strip(), note=cols[-1].strip()))
    return items


# ---------- extensions ----------
def old_versions(groups):
    """groups: key -> [(version, path)]; trả về set path không phải bản mới nhất."""
    old = set()
    for vs in groups.values():
        if len(vs) > 1:
            vs.sort(key=lambda t: vkey(t[0]), reverse=True)
            old.update(p for _, p in vs[1:])
    return old


def scan_extensions():
    items, groups, rows = [], defaultdict(list), []
    for d in glob.glob(os.path.join(HOME, ".*/extensions")):
        editor = Path(d).parent.name.lstrip(".")
        obsolete = {}
        try:
            obsolete = json.load(open(os.path.join(d, ".obsolete")))
        except Exception:
            pass
        for ext in children(d):
            pkg = os.path.join(ext, "package.json")
            if not os.path.isfile(pkg):
                continue
            try:
                meta = json.load(open(pkg, encoding="utf-8"))
            except Exception:
                meta = {}
            key = f"{meta.get('publisher', '?')}.{meta.get('name', Path(ext).name)}".lower()
            name = meta.get("displayName", "")
            name = key if not name or name.startswith("%") else name
            rows.append((editor, key, name, meta.get("version", ""), ext, Path(ext).name in obsolete))
            groups[(editor, key)].append((meta.get("version", ""), ext))
    old = old_versions(groups)
    for editor, key, name, ver, ext, obs in rows:
        safety = "safe" if obs else "review" if ext in old else "info"
        note = "editor đã đánh dấu obsolete" if obs else "bản cũ, đã có bản mới hơn" if ext in old else key
        items.append(item("extensions", "editor", name, ext, owner=editor, version=ver, safety=safety,
                          action="trash" if safety != "info" else None, note=note))

    groups, rows = defaultdict(list), []
    for browser, rel in BROWSERS.items():
        base = os.path.join(HOME, "Library/Application Support", rel)
        for man in glob.glob(os.path.join(base, "*/Extensions/*/*/manifest.json")):
            vdir = os.path.dirname(man)
            ext_id, profile = Path(vdir).parent.name, Path(vdir).parents[2].name
            try:
                meta = json.load(open(man, encoding="utf-8"))
            except Exception:
                meta = {}
            name = meta.get("name", ext_id)
            if name.startswith("__MSG_"):
                loc = meta.get("default_locale", "en")
                try:
                    msgs = json.load(open(os.path.join(vdir, "_locales", loc, "messages.json"), encoding="utf-8"))
                    k = name[6:-2]
                    name = next((v.get("message") for kk, v in msgs.items() if kk.lower() == k.lower()), name)
                except Exception:
                    pass
            rows.append((browser, profile, ext_id, name, meta.get("version", ""), vdir))
            groups[(browser, profile, ext_id)].append((meta.get("version", ""), vdir))
    old = old_versions(groups)
    for browser, profile, ext_id, name, ver, vdir in rows:
        o = vdir in old
        items.append(item("extensions", "browser", name, vdir, owner=f"{browser} · {profile}", version=ver,
                          safety="review" if o else "info", action="trash" if o else None,
                          note="bản cũ, đã có bản mới hơn" if o else ext_id))
    return items


# ---------- home walk: git repos, artifacts, installers ----------
def walk_home(roots=None, log=None):
    repos, artifacts, installers = [], [], []
    last, t0, seen = time.time(), time.time(), 0
    for root in roots or [HOME]:
        for d, dirs, files in os.walk(root):
            seen += 1
            if log and time.time() - last >= 3:
                last = time.time()
                log(f"  … {int(last - t0)}s, {seen} thư mục, {len(repos)} repo, {len(artifacts)} build artifact — {d[:90]}")
            rel_hidden = any(part.startswith(".") for part in os.path.relpath(d, root).split(os.sep) if part != ".")
            if ".git" in dirs or ".git" in files:
                repos.append(d)
            keep = []
            for n in dirs:
                p = os.path.join(d, n)
                if os.path.islink(p) or n.endswith((".app", ".framework", ".photoslibrary", ".xcarchive")):
                    continue
                if n in ARTIFACT_DIRS or (n in MARKED_ARTIFACTS and any(f in files for f in PROJECT_MARKERS)):
                    if not rel_hidden:
                        artifacts.append(p)
                    continue
                if n in WALK_PRUNE or (d == root and (n in ("Movies", "Music", "Pictures")
                                                     or (n.startswith(".") and n not in WALK_HIDDEN_OK))):
                    continue
                keep.append(n)
            dirs[:] = keep
            if d.startswith(tuple(os.path.join(root, x) for x in ("Downloads", "Desktop", "Documents"))):
                installers += [os.path.join(d, f) for f in files if f.lower().endswith(INSTALLER_EXT)]
    return repos, artifacts, installers


def git_info(repo):
    g = ["git", "-C", repo]
    return dict(branch=run(g + ["rev-parse", "--abbrev-ref", "HEAD"]).strip(),
                remote=run(g + ["config", "--get", "remote.origin.url"]).strip(),
                dirty=len(run(g + ["status", "--porcelain"]).splitlines()),
                last=run(g + ["log", "-1", "--format=%cs"]).strip())


def scan_git(repos):
    items = []
    seen = set()
    for b in run(["which", "-a", "git"]).split():
        real = os.path.realpath(b)
        if real in seen:
            continue
        seen.add(real)
        ver = run([b, "--version"]).strip()
        src = "Homebrew" if "/homebrew/" in real or "/Cellar/" in real else "Xcode CLT (Apple)" if b == "/usr/bin/git" else "—"
        items.append(item("git", "binary", f"git ({b})", real, owner=src, version=ver.replace("git version ", "")))
    for r in sorted(set(repos)):
        info = git_info(r)
        items.append(item("git", "repo", Path(r).name, r, owner=info["remote"] or "(không có remote)",
                          version=info["branch"], note=f"{info['dirty']} file thay đổi · commit cuối {info['last'] or '—'}",
                          dirty=info["dirty"], last=info["last"]))
    return items


# ---------- caches ----------
def scan_caches():
    items = []
    lib = os.path.join(HOME, "Library")
    for p in children(os.path.join(lib, "Caches")):
        n = Path(p).name
        sysc = n.startswith(SYSTEM_CACHE_PREFIX)
        slow = next((why for k, why in SLOW_REFETCH_CACHES.items() if n.lower().startswith(k)), None)
        items.append(item("caches", "user-cache", n, p, owner=n, safety="review" if sysc or slow else "safe",
                          action="trash", note=slow or ("cache của macOS — tự tạo lại" if sysc else "")))
    for p in children(os.path.join(lib, "Logs")):
        items.append(item("caches", "log", Path(p).name, p, owner=Path(p).name, safety="safe", action="trash"))
    for c in children(os.path.join(lib, "Containers")):
        p = os.path.join(c, "Data/Library/Caches")
        if os.path.isdir(p) and children(p):
            items.append(item("caches", "container-cache", Path(c).name, p, owner=Path(c).name, safety="safe", action="trash"))
    for base in ("/Library/Caches", "/Library/Logs"):
        for p in children(base):
            items.append(item("caches", "system-cache", Path(p).name, p, owner="hệ thống", safety="review",
                              action="trash", note="có thể cần quyền admin"))
    dev = os.path.join(lib, "Developer")
    for p in children(os.path.join(dev, "Xcode/DerivedData")):
        items.append(item("caches", "xcode-deriveddata", Path(p).name, p, owner="Xcode", safety="safe", action="trash",
                          note="Xcode tự build lại"))
    for p in children(os.path.join(dev, "Xcode/iOS DeviceSupport")) + children(os.path.join(dev, "Xcode/watchOS DeviceSupport")):
        items.append(item("caches", "device-support", Path(p).name, p, owner="Xcode", safety="review", action="trash",
                          note="Xcode tạo lại khi cắm thiết bị"))
    for p in children(os.path.join(dev, "Xcode/Archives")):
        items.append(item("caches", "xcode-archive", Path(p).name, p, owner="Xcode", safety="review", action="trash",
                          note="archive bản build đã phát hành"))
    try:
        devs = json.loads(run(["xcrun", "simctl", "list", "devices", "unavailable", "-j"]) or "{}").get("devices", {})
    except json.JSONDecodeError:
        devs = {}
    for runtime, lst in devs.items():
        for dv in lst:
            p = os.path.join(dev, "CoreSimulator/Devices", dv["udid"])
            items.append(item("caches", "simulator-unavailable", dv.get("name", dv["udid"]), p, owner="Xcode Simulator",
                              version=runtime.rsplit(".", 1)[-1], safety="safe",
                              action=["xcrun", "simctl", "delete", dv["udid"]], note="runtime không còn, không chạy được"))
    for p, owner, safety in [(os.path.join(HOME, ".npm/_cacache"), "npm", "safe"),
                             (os.path.join(HOME, ".npm/_npx"), "npm", "safe"),
                             (os.path.join(HOME, ".gradle/caches"), "Gradle", "review"),
                             (os.path.join(HOME, ".cocoapods/repos"), "CocoaPods", "review"),
                             (os.path.join(HOME, ".pub-cache"), "Flutter/Dart", "review"),
                             (os.path.join(HOME, ".cargo/registry"), "Cargo", "review")]:
        if os.path.exists(p):
            items.append(item("caches", "dev-cache", Path(p).name, p, owner=owner, safety=safety, action="trash"))
    for p in children(os.path.join(HOME, ".cache")):
        if Path(p).name.lower() in AI_MODEL_CACHES:  # model AI tải về: tải lại rất lâu → chỉ liệt kê (PARITY §4)
            items.append(item("caches", "ai-model", Path(p).name, p, owner=Path(p).name, safety="info",
                              note="model AI đã tải — chỉ liệt kê, xoá tay nếu chắc chắn"))
            continue
        items.append(item("caches", "dev-cache", Path(p).name, p, owner=Path(p).name, safety="review", action="trash",
                          note="cache của công cụ CLI"))
    return items


# Thư mục con của ~/.cache chứa model AI (huggingface hub, LM Studio, torch hub, whisper…)
AI_MODEL_CACHES = {"huggingface", "lm-studio", "lmstudio", "torch", "whisper", "ollama"}

# ---------- junk ----------
def group_backups(paths):
    """Gom file backup theo (thư mục, tên gốc). Trả về (giữ, cũ): list path."""
    groups = defaultdict(list)
    for p in paths:
        if any(s in p for s in BACKUP_SKIP) or BACKUP_SKIP_RE.search(p) or p.lower().endswith(".sav"):
            continue
        try:
            st = os.lstat(p)
        except OSError:
            continue
        d, n = os.path.split(p)
        m = BACKUP_MARK.search(n)
        if not m or m.start() == 0:
            continue
        groups[(d, n[:m.start()])].append((st.st_mtime, p))
    keep, old = [], []
    for v in groups.values():
        v.sort(reverse=True)
        keep.append(v[0][1])
        old += [p for _, p in v[1:]]
    return keep, old


def find_backups(log=None):
    vol = DATA_VOL if os.path.isdir(os.path.join(DATA_VOL, "Users")) else "/"
    prune = ["*/node_modules", "*/.git", "*.app", "*.framework", "*/.Trash"]
    cmd = ["/usr/bin/find", "-x", vol, "("]
    for i, p in enumerate(prune):
        cmd += (["-o"] if i else []) + ["-path" if "/" in p else "-name", p]
    cmd += ["-o", "-path", os.path.join(vol, "System"), "-o", "-path", os.path.join(vol, "private/var/db"), ")", "-prune",
            "-o", "-type", "f", "("]
    for i, p in enumerate(BACKUP_FIND):
        cmd += (["-o"] if i else []) + ["-iname", p]
    cmd += [")", "-print"]
    out, t0, done = [], time.time(), threading.Event()

    def beat():  # nhịp đều kể cả khi find chưa thấy file nào (đoạn dài không có kết quả trông như treo)
        while not done.wait(5):
            where = f" — gần nhất: {os.path.dirname(out[-1])[:90]}" if out else ""
            log(f"  … {int(time.time() - t0)}s, đã thấy {len(out)} file backup{where}")
    if log:
        threading.Thread(target=beat, daemon=True).start()
    try:
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True) as pr:
            for line in pr.stdout:
                out.append(line.rstrip("\n"))
    except OSError:
        pass
    finally:
        done.set()
    return [p[len(DATA_VOL):] if p.startswith(DATA_VOL + "/") else p for p in out]


def scan_junk(artifacts, installers, backups, bundle_ids):
    items = []
    for p in artifacts:
        d = idle_days(p)
        recent = d is not None and d < ARTIFACT_RECENT_DAYS
        items.append(item("junk", "build-artifact", Path(p).name, p, owner=Path(p).parent.name,
                          safety="info" if recent else "review", action=None if recent else "trash",
                          note=f"dùng trong {ARTIFACT_RECENT_DAYS} ngày qua — đang làm dự án này, không đề xuất dọn"
                          if recent else "tải/build lại được"))
    for p in installers:
        items.append(item("junk", "installer", Path(p).name, p, owner="file cài đặt", safety="review", action="trash",
                          note="app đã cài thì không cần nữa"))
    keep, old = group_backups(backups)
    for p in old:
        items.append(item("junk", "backup-old", Path(p).name, p, owner=Path(p).parent.name, safety="safe",
                          action="trash", note="đã có bản backup mới hơn"))
    for p in keep:
        items.append(item("junk", "backup-latest", Path(p).name, p, owner=Path(p).parent.name, note="bản backup mới nhất — giữ"))
    lib = os.path.join(HOME, "Library")
    known = {b.lower() for b in bundle_ids if b}

    spotlight = {}

    def leftover(bid, path):
        """Dữ liệu của app đã gỡ? Lần lượt từ rẻ tới đắt; nghi ngờ thì GIỮ (không đoán là mồ côi)."""
        b = bid.lower()
        if not re.fullmatch(r"[a-z0-9\-]+(\.[a-z0-9\-]+){2,}", b) or "com.apple." in b or b.startswith(
                ("systemgroup.", "group.is.workflow", "is.workflow.")):  # của Apple (kể cả groups.com.apple.*); is.workflow = Shortcuts
            return False
        if any(k == b or k.startswith(b + ".") or b.startswith(k + ".") for k in known):
            return False                                      # app còn cài ở chỗ đã quét
        if any(w in b for w in SENSITIVE_WORDS) or security_agent("/" + b):
            return False                                      # mật khẩu / khoá / ví / phần mềm bảo mật
        d = idle_days(path)
        if d is None or d < ORPHAN_MIN_IDLE_DAYS:
            return False                                      # còn được ghi gần đây → app vẫn đang dùng
        if b not in spotlight:                                # app cài chỗ khác (Steam, ~/Downloads…)
            spotlight[b] = spotlight_has_app(bid)
        return spotlight[b] is False
    for p in children(os.path.join(lib, "Containers")) + children(os.path.join(lib, "Group Containers")):
        n = Path(p).name
        bid = n.split(".", 1)[1] if re.match(r"^[A-Z0-9]{10}\.", n) else n
        if leftover(bid, p):
            items.append(item("junk", "app-leftover", n, p, owner=bid, safety="review", action="trash",
                              note=f"không thấy app nào có bundle id này (kể cả qua Spotlight), {ORPHAN_MIN_IDLE_DAYS}+ ngày không dùng"))
    for p in children(os.path.join(lib, "Preferences")):
        n = Path(p).name
        if n.endswith(".plist") and leftover(n[:-6], p):
            items.append(item("junk", "app-leftover", n, p, owner=n[:-6], safety="review", action="trash",
                              note="file cấu hình của app không còn cài"))
    for p in children(os.path.join(lib, "Application Support")):
        if leftover(Path(p).name, p):
            items.append(item("junk", "app-leftover", Path(p).name, p, owner=Path(p).name, safety="review", action="trash",
                              note="dữ liệu của app không còn cài (theo bundle id)"))
    for p in children(os.path.join(lib, "LaunchAgents")):
        prog = agent_program(p)
        if prog and not os.path.exists(prog):
            items.append(item("junk", "agent-leftover", Path(p).name, p, owner=plist(p).get("Label", ""), safety="review",
                              action="trash", note=f"chương trình của agent không còn: {prog}"))
    trash = os.path.join(HOME, ".Trash")
    items.append(item("junk", "trash", "Thùng rác", trash, owner="Finder",
                      note="đổ bằng Finder; terminal cần Full Disk Access để đo"))
    return items


def spotlight_has_app(bid):
    """Spotlight có thấy app mang bundle id này không: True / False; None khi Spotlight lỗi hay quá giờ (→ giữ lại)."""
    try:
        r = subprocess.run(["/usr/bin/mdfind", f"kMDItemCFBundleIdentifier == '{bid}'"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return bool(r.stdout.strip())


def agent_program(path):
    """Đường dẫn chương trình mà LaunchAgent chạy (None nếu không đọc được / không phải đường dẫn tuyệt đối)."""
    if not path.endswith(".plist"):
        return None
    d = plist(path)
    prog = d.get("Program") or (d.get("ProgramArguments") or [None])[0]
    return os.path.expanduser(prog) if isinstance(prog, str) and prog.startswith(("/", "~")) else None


# ---------- scan ----------
def scan(log=print, features=None):
    """features: [(tên, module)] từ registry.discover(); None = tự nạp mclib/features/."""
    t0 = time.time()
    steps = {}

    def step(title, key, fn):
        t = time.time()
        log(f"▸ {title}…")
        steps[key] = fn()
        log(f"  ✓ {len(steps[key])} mục · {time.time() - t:.1f}s")

    log(f"Quét {HOME} trên {os.uname().nodename} (macOS {platform.mac_ver()[0]}, {platform.machine()}), python {platform.python_version()}")
    step("Ứng dụng", "applications", scan_apps)
    step("Plugin", "plugins", scan_plugins)
    step("Extension", "extensions", scan_extensions)
    log("▸ Duyệt thư mục home (git, build artifact, installer)…")
    log("  macOS có thể hỏi quyền truy cập Desktop/Documents/Downloads — bấm Cho phép để quét đủ.")
    t = time.time()
    repos, artifacts, installers = walk_home(log=log)
    repos += [r for r in ["/opt/homebrew"] if os.path.isdir(os.path.join(r, ".git"))]
    log(f"  ✓ {len(repos)} repo, {len(artifacts)} build artifact, {len(installers)} installer · {time.time() - t:.1f}s")
    step("Git repo", "git", lambda: scan_git(repos))
    step("Cache và log", "caches", scan_caches)
    log("▸ Tìm file backup trên toàn ổ (bước lâu nhất)…")
    t = time.time()
    backups = find_backups(log=log)
    log(f"  ✓ {len(backups)} file backup · {time.time() - t:.1f}s")
    bundle_ids = [i.get("bundle_id", "") for i in steps["applications"] + steps["plugins"]]
    step("Junk (artifact, installer, backup, file sót lại)", "junk", lambda: scan_junk(artifacts, installers, backups, bundle_ids))
    items = [i for v in steps.values() for i in v]
    log(f"▸ Đo dung lượng {len(items)} mục…")
    t = time.time()
    sizes = du([i["path"] for i in items if i["kind"] not in ("binary", "repo", "app-extension")], log=log)
    git_sizes = du([os.path.join(i["path"], ".git") for i in items if i["kind"] == "repo"], log=log)
    log(f"  ✓ {time.time() - t:.1f}s")
    for i in items:
        i["size"] = git_sizes.get(os.path.join(i["path"], ".git")) if i["kind"] == "repo" else sizes.get(i["path"])
    feats = registry.collect_all(registry.ReportContext(items=items, log=log),
                                 registry.discover() if features is None else features)
    usage = shutil.disk_usage(DATA_VOL if os.path.isdir(DATA_VOL) else "/")
    log(f"✓ Quét xong: {len(items)} mục trong {time.time() - t0:.0f}s")
    return dict(generatedAt=int(time.time() * 1000), host=os.uname().nodename, home=HOME,
                disk=dict(total=usage.total, used=usage.used, free=usage.free),
                seconds=round(time.time() - t0, 1), items=items, features=feats)


def to_markdown(rep):
    lines = [f"# Mac cleaner report · {time.strftime('%Y-%m-%d %H:%M', time.localtime(rep['generatedAt'] / 1000))}", "",
             f"Đĩa: dùng {fmt(rep['disk']['used'])} / {fmt(rep['disk']['total'])}, trống {fmt(rep['disk']['free'])}. "
             f"Quét {rep['seconds']}s, {len(rep['items'])} mục.", "",
             "| Nhóm | Số mục | Dung lượng | Dọn an toàn | Cân nhắc |", "|---|---:|---:|---:|---:|"]
    by = defaultdict(list)
    for i in rep["items"]:
        by[i["category"]].append(i)
    for c, lst in by.items():
        s = lambda f: sum(x["size"] or 0 for x in lst if f(x))
        lines.append(f"| {c} | {len(lst)} | {fmt(s(lambda x: True))} | {fmt(s(lambda x: x['safety'] == 'safe'))} | {fmt(s(lambda x: x['safety'] == 'review'))} |")
    for c, lst in by.items():
        lines += ["", f"## {c} ({len(lst)})", "", "| Tên | Thuộc | Phiên bản | Dung lượng | Mức | Đường dẫn |", "|---|---|---|---:|---|---|"]
        for x in sorted(lst, key=lambda x: -(x["size"] or 0)):
            cell = lambda s: str(s).replace("|", "\\|")
            lines.append(f"| {cell(x['name'])} | {cell(x['owner'])} | {cell(x['version'])} | {fmt(x['size'])} | {x['safety']} | `{cell(x['path'])}` |")
    return "\n".join(lines) + "\n"


def to_html(rep, report_path):
    page = open(os.path.join(ROOT, "Web", "board.html"), encoding="utf-8").read()
    data = json.dumps(dict(rep, reportPath=report_path), ensure_ascii=False).replace("</", "<\\/")
    return page.replace("<!--EMBED-->", f"<script>window.EMBEDDED = {data};</script>")


def write_reports(rep, out):
    os.makedirs(out, exist_ok=True)
    stem = os.path.join(out, "report-" + time.strftime("%Y%m%d-%H%M%S"))
    rep["reportPath"] = stem + ".json"
    with open(stem + ".json", "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    with open(stem + ".md", "w", encoding="utf-8") as f:
        f.write(to_markdown(rep))
    with open(stem + ".html", "w", encoding="utf-8") as f:
        f.write(to_html(rep, stem + ".json"))
    return stem


def run_post_scan_hooks(report_json, out=sys.stdout, hook_dir=None):
    """Chạy mọi file thực thi trong hooks/post-scan/ (theo tên) với đối số là report JSON vừa ghi.
    Hook lỗi chỉ in cảnh báo, không làm scan thất bại."""
    d = hook_dir or os.path.join(ROOT, "hooks", "post-scan")
    for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        f = os.path.join(d, name)
        if not (os.path.isfile(f) and os.access(f, os.X_OK)):
            continue
        try:
            r = subprocess.run([f, report_json], capture_output=True, text=True, timeout=600)
            print((r.stdout or "").rstrip() or f"[hook {name}] không in gì", file=out, flush=True)
            if r.returncode:
                print(f"[hook {name}] thoát mã {r.returncode}: {(r.stderr or '').strip()[:300]}", file=out, flush=True)
        except (OSError, subprocess.TimeoutExpired) as e:
            print(f"[hook {name}] lỗi: {e}", file=out, flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="quét và ghi report")
    s.add_argument("--out", default=os.path.join(DATA, "reports"))
    s.add_argument("--json", action="store_true", help="in JSON report ra stdout (dùng cho app)")
    s.add_argument("--no-hooks", action="store_true", help="không chạy hooks/post-scan/* (phân loại sau quét)")
    c = sub.add_parser("clean", help="dọn item trong report")
    c.add_argument("--report", help="report JSON (mặc định: mới nhất trong reports/)")
    c.add_argument("--ids", help="danh sách id, cách nhau dấu phẩy")
    c.add_argument("--category", action="append", help="chỉ nhóm này (lặp được)")
    c.add_argument("--include-review", action="store_true", help="gồm cả mục 'review' (cân nhắc)")
    c.add_argument("--force", action="store_true",
                   help="cho phép cả mục kiểm kê/bảo vệ chọn đích danh bằng --ids (vẫn chặn SIP và thư mục gốc; Thùng rác = đổ vĩnh viễn)")
    c.add_argument("--permanent", action="store_true",
                   help="cho phép mục tính năng có delete_mode 'permanent' (xoá hẳn, không hoàn tác)")
    c.add_argument("--yes", action="store_true", help="thực thi (không có: chỉ preview)")
    c.add_argument("--json", action="store_true")
    features = registry.discover()
    registry.register_all(sub, features)
    a = ap.parse_args(argv)
    if a.cmd not in ("scan", "clean"):
        return a.func(a)
    if a.cmd == "scan":
        rep = scan(log=(lambda m: print(m, file=sys.stderr, flush=True)), features=features)
        stem = write_reports(rep, a.out)
        if a.json:
            print(json.dumps(rep, ensure_ascii=False))
        else:
            print(f"Xong: {len(rep['items'])} mục trong {rep['seconds']}s\n  {stem}.html\n  {stem}.md\n  {stem}.json")
        if not a.no_hooks:
            run_post_scan_hooks(stem + ".json", out=sys.stderr if a.json else sys.stdout)  # --json: stdout giữ nguyên JSON
        return 0
    if a.force and not a.ids:
        ap.error("--force chỉ dùng kèm --ids (chọn đích danh từng mục)")
    path = a.report or max(glob.glob(os.path.join(DATA, "reports", "report-*.json")), default=None, key=os.path.getmtime)
    if not path:
        ap.error("chưa có report, chạy `scan` trước")
    rep = json.load(open(path, encoding="utf-8"))
    chosen = select(rep["items"], a.ids.split(",") if a.ids else None, a.category, a.include_review)
    fchosen = featclean.select(rep.get("features"), a.ids.split(",") if a.ids else None, a.category, a.include_review)
    before = shutil.disk_usage(DATA_VOL if os.path.isdir(DATA_VOL) else "/").free
    err = lambda m: print(m, file=sys.stderr, flush=True)
    (err if a.json else print)(f"{'Dọn' if a.yes else 'Xem trước'} {len(chosen) + len(fchosen)} mục từ {os.path.basename(path)}…")
    res = clean(rep, chosen, a.yes, log=err if a.json else print, force=a.force)  # --json: log ra stderr (app hiện ở terminal log)
    if fchosen:  # mục của tính năng (report["features"]): list-only không bao giờ dọn, permanent cần --permanent
        fres = featclean.clean(fchosen, a.yes, log=err if a.json else print, force=a.force, permanent=a.permanent,
                               log_path=res["log"])
        res["results"] += fres["results"]
        res["freed"] += fres["freed"]
        chosen = chosen + fchosen
    res["freeBefore"], res["freeAfter"] = before, shutil.disk_usage(DATA_VOL if os.path.isdir(DATA_VOL) else "/").free
    if a.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        mode = "Đã dọn" if a.yes else "Preview (thêm --yes để dọn)"
        print(f"{mode}: {sum(r['status'] in ('ok', 'dry-run') for r in res['results'])}/{len(chosen)} mục, ~{fmt(res['freed'])}"
              + (f" · trống {fmt(before)} → {fmt(res['freeAfter'])} · log {res['log']}" if a.yes else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
