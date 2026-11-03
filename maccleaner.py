#!/usr/bin/env python3
"""mac-cleaner: kiểm kê toàn bộ máy macOS (app, plugin, extension, git, cache, junk) và dọn dẹp an toàn.

  python3 maccleaner.py scan [--out DIR] [--json]      quét, ghi reports/report-<ts>.{json,html,md}
  python3 maccleaner.py clean --report R [lọc] [--yes] dọn item trong report (mặc định chỉ preview)

Chỉ dùng thư viện chuẩn. Xoá = chuyển vào Thùng rác (/usr/bin/trash), khôi phục được.
"""
import argparse
import fnmatch
import glob
import hashlib
import html
import json
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path

HOME = str(Path.home())
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("MACCLEANER_DATA") or ROOT  # reports/ và logs/; app .app đặt ra ngoài bundle
DATA_VOL = "/System/Volumes/Data"

# Không bao giờ dọn: khoá và bí mật.
PROTECTED = [os.path.expanduser(p) for p in ("~/Library/Keychains*", "~/.ssh*", "~/.gnupg*")]
# Trong ~/Library chỉ dọn ở các vùng được quét; mọi thư mục khác (dữ liệu ứng dụng, thư mục đồng bộ…) được bảo vệ.
LIBRARY_CLEANABLE = ("Caches", "Logs", "Containers", "Group Containers", "Preferences", "Developer")
# Thư mục ẩn ở home: chỉ dọn các vùng cache này; còn lại (dữ liệu, model, cấu hình của các tool) được bảo vệ.
DOT_CLEANABLE = (".cache", ".npm/_cacache", ".npm/_npx", ".gradle/caches")
# Cache do macOS tự quản lý: dọn được nhưng để mức "cân nhắc".
SYSTEM_CACHE_PREFIX = "com.apple."
# clean chỉ chạm các vùng này; path phải sâu ít nhất 2 cấp dưới vùng.
ALLOWED_ROOTS = [HOME, "/Library/Caches", "/Library/Logs"]
CLEANABLE = {"safe", "review"}

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


def plist(path):
    try:
        with open(path, "rb") as f:
            return plistlib.load(f)
    except Exception:
        return {}


def is_protected(path):
    if any(fnmatch.fnmatch(path, p) or path.startswith(p.rstrip("*") + "/") for p in PROTECTED):
        return True
    lib = os.path.join(HOME, "Library") + "/"
    if path.startswith(lib):
        return path[len(lib):].split("/")[0] not in LIBRARY_CLEANABLE
    if path.startswith(HOME + "/."):
        rel = path[len(HOME) + 1:]
        return not any(rel == c or rel.startswith(c + "/") for c in DOT_CLEANABLE)
    return False


def vkey(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or ""))


def item(category, kind, name, path, *, owner="", version="", safety="info", action=None, note="", size=None, **extra):
    if safety in CLEANABLE and is_protected(path):
        safety, action, note = "protected", None, (note + " · " if note else "") + "được bảo vệ"
    iid = hashlib.sha1(f"{category}|{kind}|{path}".encode()).hexdigest()[:10]
    return dict(id=iid, category=category, kind=kind, name=name, owner=owner, version=version, path=path,
                size=size, safety=safety, action=action, note=note, **extra)


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
                              note="gỡ sạch bằng `mo uninstall`" if src != "macOS" else ""))
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
        sysc = Path(p).name.startswith(SYSTEM_CACHE_PREFIX)
        items.append(item("caches", "user-cache", Path(p).name, p, owner=Path(p).name, safety="review" if sysc else "safe",
                          action="trash", note="cache của macOS — tự tạo lại" if sysc else ""))
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
        items.append(item("caches", "dev-cache", Path(p).name, p, owner=Path(p).name, safety="review", action="trash",
                          note="cache của công cụ CLI"))
    return items


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
        items.append(item("junk", "build-artifact", Path(p).name, p, owner=Path(p).parent.name, safety="review",
                          action="trash", note="tải/build lại được"))
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

    def leftover(bid):
        b = bid.lower()
        return (re.fullmatch(r"[a-z0-9\-]+(\.[a-z0-9\-]+){2,}", b) and not b.startswith(("com.apple.", "group.com.apple.", "systemgroup.", "group.is.workflow", "is.workflow."))  # is.workflow = Shortcuts của Apple
                and not any(k == b or k.startswith(b + ".") or b.startswith(k + ".") for k in known))
    for p in children(os.path.join(lib, "Containers")) + children(os.path.join(lib, "Group Containers")):
        n = Path(p).name
        bid = n.split(".", 1)[1] if re.match(r"^[A-Z0-9]{10}\.", n) else n
        if leftover(bid):
            items.append(item("junk", "app-leftover", n, p, owner=bid, safety="review", action="trash",
                              note="không thấy app nào có bundle id này"))
    for p in children(os.path.join(lib, "Preferences")):
        n = Path(p).name
        if n.endswith(".plist") and leftover(n[:-6]):
            items.append(item("junk", "app-leftover", n, p, owner=n[:-6], safety="review", action="trash",
                              note="file cấu hình của app không còn cài"))
    trash = os.path.join(HOME, ".Trash")
    items.append(item("junk", "trash", "Thùng rác", trash, owner="Finder",
                      note="đổ bằng Finder; terminal cần Full Disk Access để đo"))
    return items


# ---------- scan ----------
def scan(log=print):
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
    usage = shutil.disk_usage(DATA_VOL if os.path.isdir(DATA_VOL) else "/")
    log(f"✓ Quét xong: {len(items)} mục trong {time.time() - t0:.0f}s")
    return dict(generatedAt=int(time.time() * 1000), host=os.uname().nodename, home=HOME,
                disk=dict(total=usage.total, used=usage.used, free=usage.free),
                seconds=round(time.time() - t0, 1), items=items)


def fmt(n):
    if n is None:
        return "—"
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


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


# ---------- clean ----------
def validate(it):
    """Lý do từ chối dọn item, hoặc None nếu được phép. Kiểm lại lúc clean, không tin report."""
    p = it.get("path", "")
    if it.get("safety") not in CLEANABLE or not it.get("action"):
        return "item chỉ để kiểm kê, không dọn được"
    if not os.path.isabs(p) or os.path.normpath(p) != p.rstrip("/"):
        return "đường dẫn không hợp lệ"
    if is_protected(p):
        return "đường dẫn được bảo vệ"
    root = next((r for r in ALLOWED_ROOTS if p.startswith(r + "/")), None)
    if not root or len(Path(p).relative_to(root).parts) < (2 if root == HOME else 1):
        return "ngoài vùng cho phép dọn"
    if isinstance(it["action"], list):
        if it["action"][:3] != ["xcrun", "simctl", "delete"]:
            return "lệnh không nằm trong danh sách cho phép"
    elif it["action"] != "trash":
        return "action không hỗ trợ"
    elif not os.path.lexists(p):
        return "không còn tồn tại"
    return None


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


def clean(rep, chosen, yes=False, log=print):
    results, freed = [], 0
    log_path = os.path.join(DATA, "logs", "clean-" + time.strftime("%Y%m%d-%H%M%S") + ".jsonl")
    if yes:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
    for it in chosen:
        why = validate(it)
        status = "skip" if why else "dry-run" if not yes else "ok"
        if not why and yes:
            cmd = it["action"] if isinstance(it["action"], list) else ["/usr/bin/trash", it["path"]]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                status, why = "error", (r.stderr or r.stdout).strip()[:200]
        if status in ("ok", "dry-run"):
            freed += it.get("size") or 0
        res = dict(id=it["id"], path=it["path"], size=it.get("size"), status=status, reason=why or "")
        results.append(res)
        log(f"[{status:7}] {fmt(it.get('size')):>9}  {it['path']}" + (f"  ({why})" if why else ""))
        if yes:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(res, ensure_ascii=False) + "\n")
    return dict(results=results, freed=freed, log=log_path if yes else None)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="quét và ghi report")
    s.add_argument("--out", default=os.path.join(DATA, "reports"))
    s.add_argument("--json", action="store_true", help="in JSON report ra stdout (dùng cho app)")
    c = sub.add_parser("clean", help="dọn item trong report")
    c.add_argument("--report", help="report JSON (mặc định: mới nhất trong reports/)")
    c.add_argument("--ids", help="danh sách id, cách nhau dấu phẩy")
    c.add_argument("--category", action="append", help="chỉ nhóm này (lặp được)")
    c.add_argument("--include-review", action="store_true", help="gồm cả mục 'review' (cân nhắc)")
    c.add_argument("--yes", action="store_true", help="thực thi (không có: chỉ preview)")
    c.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "scan":
        rep = scan(log=(lambda m: print(m, file=sys.stderr, flush=True)))
        stem = write_reports(rep, a.out)
        if a.json:
            print(json.dumps(rep, ensure_ascii=False))
        else:
            print(f"Xong: {len(rep['items'])} mục trong {rep['seconds']}s\n  {stem}.html\n  {stem}.md\n  {stem}.json")
        return 0
    path = a.report or max(glob.glob(os.path.join(DATA, "reports", "report-*.json")), default=None, key=os.path.getmtime)
    if not path:
        ap.error("chưa có report, chạy `scan` trước")
    rep = json.load(open(path, encoding="utf-8"))
    chosen = select(rep["items"], a.ids.split(",") if a.ids else None, a.category, a.include_review)
    before = shutil.disk_usage(DATA_VOL if os.path.isdir(DATA_VOL) else "/").free
    err = lambda m: print(m, file=sys.stderr, flush=True)
    (err if a.json else print)(f"{'Dọn' if a.yes else 'Xem trước'} {len(chosen)} mục từ {os.path.basename(path)}…")
    res = clean(rep, chosen, a.yes, log=err if a.json else print)  # --json: log ra stderr (app hiện ở terminal log)
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
