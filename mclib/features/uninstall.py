"""Gỡ app sạch (app + file liên quan) và kiểm kê app lâu không mở. Không sudo.

  python3 maccleaner.py uninstall <tên app | đường dẫn .app | bundle id> [--yes] [--json]
  python3 maccleaner.py uninstall --list [--json]      danh sách app, lần mở cuối, dung lượng kèm file liên quan
  scan → report["features"]["uninstall"] = {"summary": {...}, "items": [mỗi app một mục, kèm related[] và lastUsed]}

Mặc định chỉ xem trước. --yes: app rồi tới file liên quan vào Thùng rác qua mclib.safety (log logs/clean-*.jsonl).
Từ chối cứng: app của Apple/SIP, phần mềm bảo mật/MDM. Từ chối: app đang chạy. App thuộc root, không ghi được hoặc
cài bằng .pkg: chỉ in hướng dẫn (cần quyền admin), không xoá gì.
"""
import concurrent.futures
import datetime
import glob
import json
import os
import re
import stat
import subprocess
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

from mclib import registry, safety as S

NAME = "uninstall"
KIND = "app-uninstall"
STALE_DAYS = 90
# Thư mục trong ~/Library chứa dữ liệu của app, tìm theo bundle id (và theo tên ở NAME_MATCH_DIRS).
LIB_DIRS = ("Application Support", "Caches", "Preferences", "Preferences/ByHost", "Saved Application State",
            "Containers", "Group Containers", "HTTPStorages", "WebKit", "Logs", "LaunchAgents", "Cookies")
# Xoá ở đây là mất dữ liệu / cài đặt của app → mức "review"; còn lại (cache, log, trạng thái cửa sổ…) là "safe".
DATA_DIRS = ("Application Support", "Containers", "Group Containers", "Preferences", "Preferences/ByHost",
             "LaunchAgents", "Cookies")
NAME_MATCH_DIRS = ("Application Support", "Caches", "Logs")
# Thành phần nằm trong bundle: đi cùng app, chỉ liệt kê. Bundle id của chúng mở rộng phạm vi tìm (Containers của extension).
BUNDLED_DIRS = ("Contents/PlugIns", "Contents/Library/LoginItems", "Contents/Library/LaunchServices",
                "Contents/Library/SystemExtensions", "Contents/Library/QuickLook", "Contents/Library/Spotlight")
# Ngoài thư mục người dùng: cần admin → chỉ liệt kê.
SYSTEM_DIRS = ("/Library/LaunchAgents", "/Library/LaunchDaemons", "/Library/PrivilegedHelperTools",
               "/Library/Application Support", "/Library/Preferences")
# Tên quá chung chung để khớp theo tên app.
GENERIC_NAMES = {"app", "application", "applications", "helper", "launcher", "main", "electron", "updater", "agent",
                 "service", "data", "cache", "caches", "logs", "support", "default", "user", "shared", "setup", "tool"}
SF_RESTRICTED = getattr(stat, "SF_RESTRICTED", 0x00080000)  # cờ SIP trên file
BID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-]*(\.[A-Za-z0-9\-_]+)+")


# ---------- lệnh ngoài ----------
def run_all(cmd, timeout=15):
    """stdout + stderr của lệnh (codesign in thông tin ra stderr); lỗi/quá giờ → chuỗi rỗng."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return ""


def team_id(path, run_err=run_all):
    m = re.search(r"^TeamIdentifier=(\S+)", run_err(["/usr/bin/codesign", "-dv", path]), re.M)
    return m.group(1) if m and m.group(1).lower() != "not" else ""


def pkg_ids(path, run=S.run):
    """Gói .pkg đã cài file này (pkgutil --file-info), [] nếu app được kéo thả."""
    return re.findall(r"^pkgid: (\S+)", run(["/usr/sbin/pkgutil", "--file-info", path], timeout=15), re.M)


def parse_mdls_raw(text, n):
    """Output `mdls -raw -name kMDItemLastUsedDate f1 f2 …` (giá trị cách nhau NUL) → [datetime | None] * n."""
    vals = text.split("\0") if text else []
    if len(vals) != n:
        return [None] * n
    out = []
    for v in vals:
        try:
            out.append(datetime.datetime.strptime(v.strip(), "%Y-%m-%d %H:%M:%S %z"))
        except ValueError:
            out.append(None)
    return out


def last_used(paths, run=S.run):
    out = []
    for i in range(0, len(paths), 100):
        chunk = paths[i:i + 100]
        out += parse_mdls_raw(run(["/usr/bin/mdls", "-raw", "-name", "kMDItemLastUsedDate", *chunk], timeout=30),
                              len(chunk))
    return out


def ps_lines(run=S.run):
    return [l.strip() for l in run(["/bin/ps", "-axo", "comm="], timeout=10).splitlines() if l.strip()]


# ---------- app ----------
def app_roots(home=None):
    return ["/Applications", "/Applications/Utilities", os.path.join(home or S.HOME, "Applications")]


def list_apps(roots):
    """Đường dẫn thật (realpath) của các .app ở roots (cấp 1 và 2), không lặp, bỏ app lồng trong app."""
    seen, out = set(), []
    for r in roots:
        base = glob.escape(r)
        for a in sorted(glob.glob(os.path.join(base, "*.app")) + glob.glob(os.path.join(base, "*", "*.app"))):
            real = os.path.realpath(a)
            if real in seen or os.path.dirname(real).endswith(".app") or not os.path.isdir(real):
                continue
            seen.add(real)
            out.append(real)
    return out


def _bundled(path):
    out = []
    for d in BUNDLED_DIRS:
        for c in _children(os.path.join(path, d)):
            info = S.plist(os.path.join(c, "Contents", "Info.plist")) if os.path.isdir(c) else {}
            out.append(dict(path=c, bundle_id=str(info.get("CFBundleIdentifier") or ""), kind=os.path.basename(d)))
    return out


def read_app(path):
    info = S.plist(os.path.join(path, "Contents", "Info.plist"))
    names = [n for n in dict.fromkeys([Path(path).stem, info.get("CFBundleName"), info.get("CFBundleDisplayName")])
             if isinstance(n, str) and n]
    return dict(path=path, name=names[0] if names else Path(path).stem, names=names,
                bundle_id=str(info.get("CFBundleIdentifier") or ""),
                version=str(info.get("CFBundleShortVersionString") or ""), build=str(info.get("CFBundleVersion") or ""),
                bundled=_bundled(path))


def bids_of(app):
    return [b for b in dict.fromkeys(x.lower() for x in [app["bundle_id"]] + [c["bundle_id"] for c in app["bundled"]]) if b]


def is_apple(app):
    if app["bundle_id"].lower().startswith("com.apple.") or app["path"].startswith("/System/"):
        return True
    try:
        return bool(os.lstat(app["path"]).st_flags & SF_RESTRICTED)
    except (OSError, AttributeError):
        return False


def is_security(app):
    return any(S.security_agent("/" + b) for b in bids_of(app)) or S.security_agent(app["path"].lower())


def _owner(path):
    return os.stat(path).st_uid


def _children(d):
    try:
        return [os.path.join(d, n) for n in sorted(os.listdir(d)) if n != ".DS_Store"]
    except OSError:
        return []


def find_apps(query, roots, run=S.run, spotlight=True):
    """Đường dẫn .app khớp truy vấn: đường dẫn .app, bundle id hoặc tên (không phân biệt hoa thường). Ưu tiên app ở
    roots; không thấy thì hỏi Spotlight."""
    q = query.strip()
    if q.lower().endswith(".app") and ("/" in q or q.startswith("~")):
        p = os.path.realpath(os.path.expanduser(q))
        return [p] if os.path.isfile(os.path.join(p, "Contents", "Info.plist")) else []
    low = q.lower()
    stem = low[:-4] if low.endswith(".app") else low
    hits = []
    for p in list_apps(roots):
        a = read_app(p)
        if a["bundle_id"].lower() == low or stem in (n.lower() for n in a["names"]):
            hits.append(p)
    if hits or not spotlight or not q or re.search(r"['\"\\*]", q):
        return hits
    exprs = [f"kMDItemContentType == 'com.apple.application-bundle' && kMDItemFSName == '{stem}.app'c"]
    if BID_RE.fullmatch(q):
        exprs.insert(0, f"kMDItemCFBundleIdentifier == '{q}'")
    trash = os.path.join(S.HOME, ".Trash") + "/"
    for e in exprs:
        found = [os.path.realpath(l) for l in run(["/usr/bin/mdfind", e], timeout=10).splitlines() if l.endswith(".app")]
        found = [p for p in dict.fromkeys(found) if os.path.isdir(p) and not p.startswith(trash)
                 and ".app/" not in p[:-4]]
        if found:
            return found
    return []


def spotlight_copies(bid, exclude, run=S.run):
    """Bản khác của app cùng bundle id mà Spotlight thấy (ngoài Thùng rác, ngoài chính app)."""
    if not bid or not BID_RE.fullmatch(bid):
        return []
    trash = os.path.join(S.HOME, ".Trash") + "/"
    out = []
    for l in run(["/usr/bin/mdfind", f"kMDItemCFBundleIdentifier == '{bid}'"], timeout=10).splitlines():
        p = os.path.realpath(l.strip())
        if p.endswith(".app") and p != exclude and not p.startswith((exclude + "/", trash)) and os.path.isdir(p):
            out.append(p)
    return out


# ---------- login item / Background Task Management ----------
def parse_btm(text):
    """Output `sfltool dumpbtm` → [{uid, name, type, identifier, url, exec, bundle_id, parent, team, disposition}]."""
    keys = {"Name": "name", "Type": "type", "Identifier": "identifier", "URL": "url", "Executable Path": "exec",
            "Bundle Identifier": "bundle_id", "Parent Identifier": "parent", "Team Identifier": "team",
            "Disposition": "disposition"}
    out, uid, rec = [], None, None
    for line in (text or "").splitlines():
        m = re.search(r"Records for UID (-?\d+)", line)
        if m:
            uid, rec = int(m.group(1)), None
            continue
        if re.match(r"^\s*#\d+:\s*$", line):
            rec = {"uid": uid}
            out.append(rec)
            continue
        m = re.match(r"^\s*([A-Za-z][A-Za-z ]*?):\s(.*)$", line)
        if rec is not None and m and m.group(1) in keys:
            v = m.group(2).strip()
            rec[keys[m.group(1)]] = None if v == "(null)" else v
    return out


def _url_path(u):
    if not u or not u.startswith("file://"):
        return None
    return unquote(urlparse(u).path).rstrip("/") or None


def btm_for(app, records):
    """Bản ghi login item / BTM thuộc app: trỏ vào trong bundle, hoặc identifier/bundle id/parent khớp bundle id."""
    bids = bids_of(app)
    root = app["path"]

    def mine(v):
        v = re.sub(r"^\d+\.", "", (v or "")).lower()
        return bool(v) and any(v == b or v.startswith(b + ".") for b in bids)
    out = []
    for r in records:
        if (r.get("type") or "").startswith("developer"):
            continue
        paths = [p for p in (_url_path(r.get("url")), r.get("exec")) if p]
        if any(p == root or p.startswith(root + "/") for p in paths) or any(
                mine(r.get(k)) for k in ("identifier", "bundle_id", "parent")):
            out.append(r)
    return out


# ---------- file liên quan ----------
class LibIndex:
    """listdir của các thư mục ~/Library, đọc một lần (collect dùng cho mọi app)."""

    def __init__(self, home):
        self.lib = os.path.join(home, "Library")
        self._c = {}

    def names(self, d):
        if d not in self._c:
            try:
                self._c[d] = sorted(n for n in os.listdir(os.path.join(self.lib, d)) if n != ".DS_Store")
            except OSError:
                self._c[d] = []
        return self._c[d]


def _bid_hit(n, bids):
    """Bundle id dài nhất mà tên n thuộc về (n == bid hoặc n bắt đầu bằng bid + '.')."""
    hits = [b for b in bids if n == b or n.startswith(b + ".")]
    return max(hits, key=len) if hits else None


def _agent_owned(path, app, bids):
    d = S.plist(path)
    label = str(d.get("Label") or "").lower()
    if _bid_hit(label, bids):
        return True
    prog = d.get("Program") or (d.get("ProgramArguments") or [None])[0]
    return isinstance(prog, str) and prog.startswith(app["path"] + "/")


def related(app, home, others=(), idx=None, team="", other_teams=lambda: set(), system=True):
    """File liên quan của app: [{path, where, reason, safety, delete_mode}] (chưa đo dung lượng).
    others: app KHÁC còn cài (dict của read_app) — tên thuộc về app khác (bundle id dài hơn, cùng tên) bị loại.
    other_teams(): team id của app khác còn cài — nhóm Group Containers của team còn dùng chung thì chỉ liệt kê."""
    idx = idx or LibIndex(home)
    bids = bids_of(app)
    if not bids:
        return []
    other_bids = {b for o in others for b in bids_of(o)}
    other_names = {n.lower() for o in others for n in o["names"]}
    names = {n.lower() for n in app["names"] if len(n) >= 3 and n.lower() not in GENERIC_NAMES} - other_names
    sensitive = any(w in (app["bundle_id"] + " " + app["name"]).lower() for w in S.SENSITIVE_WORDS)
    out = []

    def add(d, n, reason, safety=None, delete_mode="trash"):
        p = os.path.join(idx.lib, d, n)
        if safety is None:
            safety = "review" if d in DATA_DIRS or reason.startswith("trùng tên") else "safe"
        if sensitive and delete_mode == "trash":
            safety, delete_mode = "protected", "list-only"
            reason += " · dữ liệu mật khẩu/khoá/ví — không bao giờ xoá tự động"
        out.append(dict(path=p, where=d, reason=reason, safety=safety, delete_mode=delete_mode))

    for d in LIB_DIRS:
        for n in idx.names(d):
            nl = n.lower()
            if "com.apple." in nl or (d == "Preferences" and nl == "byhost"):
                continue
            if d == "Group Containers":
                gid = nl[len(team) + 1:] if team and nl.startswith(team.lower() + ".") else None
                inner = nl[6:] if nl.startswith("group.") else (gid or nl)
                hit = _bid_hit(inner, bids)
                if hit and _bid_hit(inner, other_bids | {hit}) != hit:
                    continue                                    # thuộc app khác có bundle id dài hơn
                if gid is not None and not hit:
                    if team in other_teams():
                        add(d, n, f"nhóm của team {team} — còn app khác cùng hãng dùng chung, giữ", "inventory",
                            "list-only")
                    else:
                        add(d, n, f"nhóm của team {team}, không còn app nào khác của hãng")
                elif hit:
                    add(d, n, f"nhóm theo bundle id {hit}")
                continue
            hit = _bid_hit(nl, bids)
            if hit:
                if _bid_hit(nl, other_bids | {hit}) == hit:
                    add(d, n, f"theo bundle id {hit}")
                continue
            if d in NAME_MATCH_DIRS and nl in names:
                add(d, n, "trùng tên app (không theo bundle id) — kiểm lại")
            elif d == "LaunchAgents" and nl.endswith(".plist") and _agent_owned(os.path.join(idx.lib, d, n), app, bids):
                add(d, n, "agent chạy chương trình của app / nhãn theo bundle id")
    if system:
        for d in SYSTEM_DIRS:
            for p in _children(d):
                nl = os.path.basename(p).lower()
                if "com.apple." in nl:
                    continue
                if _bid_hit(nl, bids) or (d == "/Library/Application Support" and nl in names):
                    out.append(dict(path=p, where=d, safety="inventory", delete_mode="list-only",
                                    reason="ngoài thư mục người dùng — cần quyền admin; dùng trình gỡ của hãng"))
    return out


# ---------- kế hoạch gỡ ----------
def block_reason(app, run=S.run, ps=None, running_ids=None):
    """(mức, lý do, hướng dẫn[]) hoặc None. Mức: deny (không bao giờ) · running · admin (cần quyền admin)."""
    p = app["path"]
    if is_apple(app):
        return "deny", "app của Apple / hệ thống (SIP) — không gỡ", []
    if is_security(app):
        return "deny", "phần mềm bảo mật / quản lý thiết bị — chỉ gỡ bằng trình gỡ của hãng (xoá có thể bị coi là can thiệp)", []
    deny = S.hard_deny(p)
    if deny:
        return "deny", deny, []
    ps = ps_lines(run) if ps is None else ps
    running_ids = S.running_bundle_ids() if running_ids is None else running_ids
    if any(b in running_ids for b in bids_of(app)) or any(l.startswith(p + "/") for l in ps):
        return "running", "app đang chạy — thoát app (cả biểu tượng ở thanh menu) rồi chạy lại", []
    pkgs = pkg_ids(p, run)
    if pkgs:
        return "admin", "app cài bằng gói .pkg — gỡ cần quyền admin", [
            "Xem file của gói: " + " · ".join(f"pkgutil --files {k}" for k in pkgs),
            "Dùng trình gỡ của hãng nếu có, hoặc kéo app vào Thùng rác bằng Finder (Finder hỏi mật khẩu admin).",
            "File gói cài ngoài thư mục người dùng (/Library…) cần admin; mac-cleaner không tự leo quyền."]
    try:
        root_owned = _owner(p) == 0
    except OSError:
        root_owned = False
    if root_owned or not os.access(p, os.W_OK) or not os.access(os.path.dirname(p), os.W_OK):
        return "admin", ("app thuộc root" if root_owned else "không có quyền ghi app / thư mục chứa app") + \
            " — gỡ cần quyền admin", [
            "Kéo app vào Thùng rác bằng Finder (Finder hỏi mật khẩu admin), hoặc dùng trình gỡ của hãng.",
            f"Sau đó chạy lại `uninstall {app['bundle_id'] or app['name']}` để dọn file liên quan trong thư mục người dùng."]
    return None


def _entry(path, safety, delete_mode, reason, blocked, **extra):
    if blocked and delete_mode != "list-only":
        safety, delete_mode = ("inventory" if safety != "protected" else safety), "list-only"
    return registry.entry(NAME, path, safety=safety, delete_mode=delete_mode, reason=reason, kind=KIND, **extra)


def plan(query, home=None, roots=None, run=S.run, run_err=run_all, spotlight=True, btm=True, system=True,
         ps=None, running_ids=None):
    """Kế hoạch gỡ (CHỈ ĐỌC): {query, app, error, candidates, blocked, guide, items[], bundled[], login_items[]}.
    items[0] là chính app; mục delete_mode 'trash' là thứ --yes sẽ chuyển vào Thùng rác."""
    home = home or S.HOME
    roots = roots or app_roots(home)
    res = dict(query=query, app=None, error=None, candidates=[], blocked=None, guide=[], items=[], bundled=[],
               login_items=[], notes=[])
    found = find_apps(query, roots, run=run, spotlight=spotlight)
    if not found:
        res["error"] = "không tìm thấy app khớp (thử tên trong /Applications, bundle id, hoặc đường dẫn .app)"
        return res
    if len(found) > 1:
        res["error"], res["candidates"] = "nhiều app khớp — chỉ rõ bằng đường dẫn .app", found
        return res
    app = read_app(found[0])
    team = team_id(app["path"], run_err)
    res["app"] = dict(path=app["path"], name=app["name"], bundle_id=app["bundle_id"], version=app["version"],
                      team_id=team)
    blk = block_reason(app, run=run, ps=ps, running_ids=running_ids)
    if blk:
        res["blocked"] = dict(level=blk[0], reason=blk[1])
        res["guide"] = blk[2]
    blocked = bool(blk)
    if blk and blk[0] == "deny":
        res["items"] = [_entry(app["path"], "protected", "list-only", blk[1], True, bundle_id=app["bundle_id"])]
        return res
    others = [read_app(p) for p in list_apps(roots) if p != app["path"]]
    copies = [o["path"] for o in others if app["bundle_id"] and o["bundle_id"].lower() == app["bundle_id"].lower()]
    if spotlight:
        copies += [p for p in spotlight_copies(app["bundle_id"], app["path"], run) if p not in copies]
    cache = {}

    def other_teams():
        if "t" not in cache:
            pool = [o["path"] for o in others if not is_apple(o)]
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
                cache["t"] = {t for t in ex.map(lambda p: team_id(p, run_err), pool) if t}
        return cache["t"]
    rel = related(app, home, others, team=team, other_teams=other_teams, system=system)
    if copies:
        res["notes"].append(f"còn {len(copies)} bản khác của app cùng bundle id — chỉ gỡ app này, giữ dữ liệu")
        for r in rel:
            if r["delete_mode"] == "trash":
                r.update(safety="inventory", delete_mode="list-only",
                         reason=r["reason"] + " · còn bản khác của app dùng dữ liệu này")
    sizes = S.du([app["path"]] + [r["path"] for r in rel])
    res["items"].append(_entry(app["path"], "review", "trash", "ứng dụng — vào Thùng rác, khôi phục được", blocked,
                               size=sizes.get(app["path"]), bundle_id=app["bundle_id"], where="app"))
    for r in rel:
        res["items"].append(_entry(r["path"], r["safety"], r["delete_mode"], r["reason"], blocked,
                                   size=sizes.get(r["path"]), where=r["where"]))
    for c in app["bundled"]:
        res["bundled"].append(_entry(c["path"], "inventory", "list-only",
                                     f"{c['kind']} nằm trong app — đi cùng app vào Thùng rác", True,
                                     bundle_id=c["bundle_id"]))
    if btm:
        recs = btm_for(app, parse_btm(run(["/usr/bin/sfltool", "dumpbtm"], timeout=30)))
        res["login_items"] = [dict(name=r.get("name"), type=r.get("type"), identifier=r.get("identifier"),
                                   path=_url_path(r.get("url")) or r.get("exec"), disposition=r.get("disposition"),
                                   uid=r.get("uid")) for r in recs]
        if recs:
            res["notes"].append("login item / mục chạy nền của app: macOS gỡ đăng ký khi app vào Thùng rác; "
                                "còn sót thì tắt ở Cài đặt › Chung › Mục đăng nhập & tiện ích mở rộng")
    return res


def _as_clean_item(e):
    return dict(id=e["id"], path=e["path"], size=e.get("size"), safety=e["safety"], action="trash", kind=KIND,
                category=NAME)


def _app_path_ok(p, roots, home):
    """Kiểm lại ngay trước khi xoá app (dùng --force của safety): phải là .app thật, ở thư mục app hay trong home."""
    if not p.endswith(".app") or os.path.islink(p) or not os.path.isdir(p) or S.hard_deny(p):
        return False
    real = [os.path.realpath(r) for r in roots] + [os.path.realpath(home)]
    return any(p.startswith(r + "/") for r in real)


def execute(pl, home=None, roots=None, log=print, run=S.run, ps=None, running_ids=None):
    """Thực thi kế hoạch: app trước (lỗi thì dừng, không đụng file liên quan), rồi file liên quan. Mỗi mục đi qua
    safety.validate lúc xoá. → {status, results[], freed, logs[]}"""
    home = home or S.HOME
    roots = roots or app_roots(home)
    if pl.get("error") or not pl.get("app"):
        return dict(status="error", reason=pl.get("error") or "không có app", results=[], freed=0, logs=[])
    if pl.get("blocked"):
        return dict(status="refused", reason=pl["blocked"]["reason"], results=[], freed=0, logs=[])
    app = read_app(pl["app"]["path"])
    blk = block_reason(app, run=run, ps=ps, running_ids=running_ids)       # kiểm lại: app có thể vừa được mở
    if blk:
        return dict(status="refused", reason=blk[1], results=[], freed=0, logs=[])
    if not _app_path_ok(app["path"], roots, home):
        return dict(status="refused", reason="đường dẫn app ngoài vùng cho phép", results=[], freed=0, logs=[])
    first = S.clean({}, [_as_clean_item(pl["items"][0])], yes=True, log=log, force=True)
    out = dict(status="ok", reason="", results=list(first["results"]), freed=first["freed"], logs=[first["log"]])
    if first["results"][0]["status"] != "ok":
        out.update(status="error", reason="không chuyển được app vào Thùng rác — giữ nguyên file liên quan")
        return out
    rest = [_as_clean_item(e) for e in pl["items"][1:] if e["delete_mode"] == "trash"]
    if rest:
        second = S.clean({}, rest, yes=True, log=log, force=False)
        out["results"] += second["results"]
        out["freed"] += second["freed"]
        if second["log"] not in out["logs"]:
            out["logs"].append(second["log"])
    if any(r["status"] == "error" for r in out["results"]):
        out["status"] = "partial"
    return out


# ---------- scan ----------
def _inspect(path, run, run_err):
    return team_id(path, run_err), pkg_ids(path, run)


def inventory(home, roots, run=S.run, run_err=run_all, du=S.du, now=None, ps=None, running_ids=None):
    """Danh sách app (không gồm app của Apple) kèm lần mở cuối, dung lượng app + file liên quan."""
    now = time.time() if now is None else now
    apps = [a for a in (read_app(p) for p in list_apps(roots)) if not is_apple(a)]
    paths = [a["path"] for a in apps]
    used = last_used(paths, run)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        info = list(ex.map(lambda p: _inspect(p, run, run_err), paths))
    teams = [t for t, _ in info]
    ps = ps_lines(run) if ps is None else ps
    running_ids = S.running_bundle_ids() if running_ids is None else running_ids
    idx = LibIndex(home)
    rels = []
    for i, a in enumerate(apps):
        others = apps[:i] + apps[i + 1:]
        ot = {t for j, t in enumerate(teams) if t and j != i}
        rels.append(related(a, home, others, idx, team=teams[i], other_teams=lambda ot=ot: ot, system=False))
    sizes = du(paths + [r["path"] for rel in rels for r in rel])
    out = []
    for a, lu, (team, pkgs), rel in zip(apps, used, info, rels):
        idle = int((now - lu.timestamp()) // 86400) if lu else None
        app_size = sizes.get(a["path"])
        rel_l = [dict(path=r["path"], size=sizes.get(r["path"]), safety=r["safety"], reason=r["reason"]) for r in rel]
        rel_size = sum(r["size"] or 0 for r in rel_l)
        why = None
        if is_security(a):
            why = "phần mềm bảo mật / quản lý thiết bị — chỉ gỡ bằng trình gỡ của hãng"
        elif any(b in running_ids for b in bids_of(a)) or any(l.startswith(a["path"] + "/") for l in ps):
            why = "đang chạy"
        elif pkgs:
            why = "cài bằng gói .pkg — gỡ cần quyền admin"
        stale = idle is not None and idle > STALE_DAYS
        if why:
            safety, mode, reason = ("protected" if why.startswith("phần mềm") else "inventory"), "list-only", why
        elif stale:
            safety, mode = "review", "trash"
            reason = f"lâu không mở ({idle} ngày) — gỡ bằng `uninstall {a['bundle_id'] or a['name']}`"
        else:
            safety, mode = "inventory", "list-only"
            reason = ("chưa có dữ liệu lần mở (Spotlight)" if lu is None else f"mở {idle} ngày trước")
        out.append(registry.entry(
            NAME, a["path"], name=a["name"], size=(app_size or 0) + rel_size if app_size is not None else None,
            safety=safety, delete_mode=mode, reason=reason, kind=KIND, bundle_id=a["bundle_id"], version=a["version"],
            team_id=team, lastUsed=lu.isoformat() if lu else None, idleDays=idle, stale=stale, appSize=app_size,
            relatedSize=rel_size, related=rel_l))
    return out


def collect(ctx):
    items = inventory(ctx.home, app_roots(ctx.home), run=ctx.run, du=ctx.du)
    stale = [e for e in items if e["stale"]]
    return {"summary": dict(apps=len(items), stale=len(stale), staleDays=STALE_DAYS,
                            staleBytes=sum(e["size"] or 0 for e in stale),
                            totalBytes=sum(e["size"] or 0 for e in items)),
            "items": items}


# ---------- CLI ----------
def _short(p, home=None):
    home = home or S.HOME
    return "~" + p[len(home):] if p.startswith(home + "/") else p


def print_plan(pl, log=print):
    if pl["error"]:
        log(f"✗ {pl['error']}")
        for c in pl["candidates"]:
            log(f"    {_short(c)}")
        return
    a = pl["app"]
    log(f"App: {a['name']} {a['version']} · {a['bundle_id'] or '—'} · team {a['team_id'] or '—'} · {_short(a['path'])}")
    if pl["blocked"]:
        log(f"⛔ Bị chặn ({pl['blocked']['level']}): {pl['blocked']['reason']}")
        for g in pl["guide"]:
            log(f"   → {g}")
    log(f"{'an toàn':9} {'dung lượng':>10}  đường dẫn — lý do")
    for e in pl["items"]:
        mark = "" if e["delete_mode"] == "trash" else " [chỉ liệt kê]"
        log(f"{e['safety']:9} {S.fmt(e.get('size')):>10}  {_short(e['path'])} — {e['reason']}{mark}")
    for e in pl["bundled"]:
        log(f"{'(trong app)':9} {'':>10}  {_short(e['path'])} — {e['reason']}")
    for li in pl["login_items"]:
        log(f"{'(login)':9} {'':>10}  {li.get('name') or li.get('identifier')} · {li.get('type')} · "
            f"{li.get('disposition') or ''}")
    for n in pl["notes"]:
        log(f"ℹ {n}")
    go = [e for e in pl["items"] if e["delete_mode"] == "trash"]
    log(f"Sẽ vào Thùng rác: {len(go)} mục, ~{S.fmt(sum(e.get('size') or 0 for e in go))}"
        + ("" if pl["blocked"] else " (thêm --yes để gỡ)"))


def cli(args):
    if args.list:
        items = inventory(S.HOME, app_roots(S.HOME))
        if args.json:
            print(json.dumps(items, ensure_ascii=False))
            return 0
        for e in sorted(items, key=lambda e: -(e["idleDays"] if e["idleDays"] is not None else -1)):
            idle = f"{e['idleDays']}d" if e["idleDays"] is not None else "—"
            print(f"{e['safety']:9} {idle:>6} {S.fmt(e['size']):>10}  {e['name']} ({e['bundle_id'] or '—'})")
        st = [e for e in items if e["stale"]]
        print(f"{len(items)} app · {len(st)} lâu không mở (> {STALE_DAYS} ngày, ~{S.fmt(sum(e['size'] or 0 for e in st))})")
        return 0
    if not args.query:
        print("cần tên app, đường dẫn .app hoặc bundle id (hoặc --list)")
        return 1
    log = (lambda m: None) if args.json else print
    pl = plan(" ".join(args.query), spotlight=not args.no_spotlight)
    print_plan(pl, log)
    res = None
    if args.yes and not pl["error"]:
        res = execute(pl, log=log)
        log({"ok": "✓ Đã gỡ.", "partial": "◐ Gỡ một phần (xem lỗi ở trên).",
             "refused": f"⛔ Từ chối: {res['reason']}", "error": f"✗ {res['reason']}"}[res["status"]]
            + (f" ~{S.fmt(res['freed'])} · log {', '.join(res['logs'])}" if res["status"] in ("ok", "partial") else ""))
    if args.json:
        print(json.dumps(dict(plan=pl, result=res), ensure_ascii=False))
    if pl["error"]:
        return 1
    if pl["blocked"] or (res and res["status"] == "refused"):
        return 2
    return 3 if res and res["status"] in ("error", "partial") else 0


def register(subparsers):
    p = subparsers.add_parser(NAME, help="gỡ app kèm file liên quan (mặc định chỉ xem trước) · --list: app lâu không mở")
    p.add_argument("query", nargs="*", help="tên app, đường dẫn .app hoặc bundle id")
    p.add_argument("--yes", action="store_true", help="thực thi: app + file liên quan vào Thùng rác")
    p.add_argument("--list", action="store_true", help="liệt kê app, lần mở cuối, dung lượng kèm file liên quan")
    p.add_argument("--no-spotlight", action="store_true", help="không hỏi Spotlight khi tìm app")
    p.add_argument("--json", action="store_true", help="in JSON")
    p.set_defaults(func=cli)
