"""Dữ liệu riêng tư: vị trí + dung lượng dữ liệu trình duyệt, danh sách mục gần đây, quyền TCC của app.

  python3 maccleaner.py privacy list [--json]
  python3 maccleaner.py privacy reset <dịch vụ> <bundle id> [--yes]    → tccutil reset (không hoàn tác được)
  scan → report["features"]["privacy"] = {"summary": {...}, "items": [...], "permissions": [...]}

Chỉ báo vị trí + dung lượng, KHÔNG đọc nội dung lịch sử/cookie/phiên và KHÔNG bao giờ tự xoá cookie, lịch sử hay mật
khẩu. Đọc bị chặn (Full Disk Access) → `needsFDA`. TCC.db mở bằng sqlite3 read-only. `reset` chỉ cho app của người dùng,
từ chối Apple / phần mềm bảo mật / MDM, không sudo.
"""
import json
import os
import re
import sqlite3
import time
from urllib.parse import quote

from mclib import registry, safety as S
from mclib.features import startup

NAME = "privacy"
TCCUTIL = "/usr/bin/tccutil"
FDA_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
# Trình duyệt nhân Chromium: thư mục dữ liệu trong ~/Library/Application Support
CHROMIUM = {"Google Chrome": "Google/Chrome", "Microsoft Edge": "Microsoft Edge", "Brave": "BraveSoftware/Brave-Browser",
            "Arc": "Arc/User Data", "Chromium": "Chromium", "Vivaldi": "Vivaldi", "Opera": "com.operasoftware.Opera"}
CHROMIUM_FILES = {"history": ("History",), "cookies": ("Network/Cookies", "Cookies"),
                  "session": ("Sessions", "Current Session", "Last Session")}
FIREFOX_FILES = {"history": ("places.sqlite",), "cookies": ("cookies.sqlite",),
                 "session": ("sessionstore.jsonlz4", "sessionstore-backups")}
# Safari: đường dẫn tương đối theo home
SAFARI_FILES = {"history": ("Library/Safari/History.db",),
                "cookies": ("Library/Containers/com.apple.Safari/Data/Library/Cookies/Cookies.binarycookies",
                            "Library/Cookies/Cookies.binarycookies"),
                "session": ("Library/Safari/LastSession.plist",)}
DB_COMPANIONS = ("-wal", "-shm", "-journal")
KIND_LABEL = {"history": "lịch sử", "cookies": "cookie", "session": "phiên"}
AUTH = {0: "denied", 1: "unknown", 2: "allowed", 3: "limited"}
# Dịch vụ `tccutil reset` nhận (bỏ tiền tố kTCCService). "All" = mọi quyền của app đó.
TCC_SERVICES = ("All", "Accessibility", "AddressBook", "AppleEvents", "BluetoothAlways", "Calendar", "Camera",
                "ContactsFull", "ContactsLimited", "DeveloperTool", "FileProviderDomain", "FileProviderPresence",
                "FocusStatus", "ListenEvent", "MediaLibrary", "Microphone", "Motion", "Photos", "PhotosAdd", "PostEvent",
                "Reminders", "ScreenCapture", "SpeechRecognition", "SystemPolicyAllFiles", "SystemPolicyAppBundles",
                "SystemPolicyAppData", "SystemPolicyDesktopFolder", "SystemPolicyDeveloperFiles",
                "SystemPolicyDocumentsFolder", "SystemPolicyDownloadsFolder", "SystemPolicyNetworkVolumes",
                "SystemPolicyRemovableVolumes", "SystemPolicySysAdminFiles", "UserTracking")
BUNDLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-]*(\.[A-Za-z0-9\-]+)+$")

run_cmd = startup.run_cmd


# ---------- đo, không đọc nội dung ----------
def probe(path):
    """(tồn tại: True|False|None, dung lượng byte|None, needsFDA). Chỉ stat/liệt kê, không mở file."""
    try:
        st = os.stat(path)
    except PermissionError:
        return None, None, True
    except FileNotFoundError:
        try:
            os.listdir(os.path.dirname(path))
            return False, None, False
        except PermissionError:
            return None, None, True
        except OSError:
            return False, None, False
    except OSError:
        return None, None, False
    if os.path.isdir(path):
        total, denied = 0, False
        for root, dirs, files in os.walk(path, onerror=lambda e: None):
            for f in files:
                try:
                    total += os.lstat(os.path.join(root, f)).st_size
                except PermissionError:
                    denied = True
                except OSError:
                    pass
        try:
            os.listdir(path)
        except PermissionError:
            return True, None, True
        return True, total, denied
    size = st.st_size
    for suf in DB_COMPANIONS:
        try:
            size += os.stat(path + suf).st_size
        except OSError:
            pass
    return True, size, False


def _data_entry(browser, profile, kind, path, size, fda):
    reason = f"{KIND_LABEL[kind]} của {browser} — chỉ báo vị trí/dung lượng; mac-cleaner không tự xoá"
    if fda:
        reason += f"; cần Full Disk Access để đọc ({FDA_PANE})"
    return registry.entry(NAME, path, name=f"{browser} · {profile} · {KIND_LABEL[kind]}", size=size, safety="inventory",
                          delete_mode="list-only", reason=reason, kind=f"browser-{kind}", browser=browser,
                          profile=profile, needsFDA=fda)


def _profile_items(browser, profile, base, files):
    out = []
    for kind, names in files.items():
        for n in names:
            p = os.path.join(base, n)
            exists, size, fda = probe(p)
            if exists or fda:
                out.append(_data_entry(browser, profile, kind, p, size, fda))
                break
    return out


def browser_data(home=None):
    home = home or S.HOME
    sup = os.path.join(home, "Library", "Application Support")
    out = []
    for browser, rel in CHROMIUM.items():
        root = os.path.join(sup, rel)
        try:
            names = sorted(os.listdir(root))
        except FileNotFoundError:
            continue
        except PermissionError:
            out.append(_data_entry(browser, "?", "history", root, None, True))
            continue
        except OSError:
            continue
        for n in names:
            base = os.path.join(root, n)
            if os.path.isfile(os.path.join(base, "Preferences")) and n != "System Profile":
                out += _profile_items(browser, n, base, CHROMIUM_FILES)
    ff = os.path.join(sup, "Firefox", "Profiles")
    try:
        for n in sorted(os.listdir(ff)):
            if os.path.isdir(os.path.join(ff, n)):
                out += _profile_items("Firefox", n, os.path.join(ff, n), FIREFOX_FILES)
    except PermissionError:
        out.append(_data_entry("Firefox", "?", "history", ff, None, True))
    except OSError:
        pass
    if os.path.isdir(os.path.join(home, "Library", "Safari")) or os.path.isdir(
            os.path.join(home, "Library", "Containers", "com.apple.Safari")):
        out += _profile_items("Safari", "Default", home, SAFARI_FILES)
    return out


def recent_items(home=None):
    """Danh sách mục gần đây của hệ thống (com.apple.sharedfilelist): mỗi file/thư mục con một mục, chỉ đếm + đo."""
    root = os.path.join(home or S.HOME, "Library", "Application Support", "com.apple.sharedfilelist")
    try:
        names = sorted(os.listdir(root))
    except FileNotFoundError:
        return []
    except PermissionError:
        return [registry.entry(NAME, root, name="Mục gần đây", safety="inventory", delete_mode="list-only",
                               reason=f"cần Full Disk Access để đọc ({FDA_PANE})", kind="recent-items", needsFDA=True)]
    out = []
    for n in names:
        if n.startswith("."):
            continue
        p = os.path.join(root, n)
        exists, size, fda = probe(p)
        count = 1 if os.path.isfile(p) else 0
        if os.path.isdir(p):
            try:
                count = sum(1 for f in os.listdir(p) if ".sfl" in f)
            except OSError:
                count = None
        out.append(registry.entry(NAME, p, name=n, size=size, safety="inventory", delete_mode="list-only",
                                  reason="danh sách mục gần đây (macOS tự quản lý) — xoá trong menu Apple › Mục gần đây",
                                  kind="recent-items", lists=count, needsFDA=fda))
    return out


# ---------- TCC ----------
def tcc_path(home=None):
    return os.path.join(home or S.HOME, "Library", "Application Support", "com.apple.TCC", "TCC.db")


def protected_client(client):
    low = (client or "").lower()
    if low.startswith("com.apple.") or (low.startswith(("/system/", "/usr/", "/bin/", "/sbin/"))
                                        and not low.startswith("/usr/local/")):
        return "của Apple/macOS"
    if S.security_agent("/" + low) or S.security_agent(low):
        return "phần mềm bảo mật/quản lý thiết bị (EDR/MDM)"
    return None


def read_tcc(path):
    """(danh sách quyền, needsFDA, lỗi). sqlite3 chỉ đọc; không đọc được do quyền → needsFDA."""
    if not os.path.exists(path):
        try:
            os.listdir(os.path.dirname(path))
        except PermissionError:
            return [], True, None
        except OSError:
            pass
        return [], False, "không có TCC.db"
    con = None
    last = None
    for q in ("mode=ro", "mode=ro&immutable=1"):
        try:
            con = sqlite3.connect(f"file:{quote(path)}?{q}", uri=True, timeout=2)
            cols = {r[1] for r in con.execute("PRAGMA table_info(access)")}
            if not cols:
                raise sqlite3.DatabaseError("không có bảng access")
            break
        except (sqlite3.Error, OSError) as e:
            last = e
            if con is not None:
                con.close()
            con = None
    if con is None:
        msg = str(last)
        fda = isinstance(last, PermissionError) or bool(re.search(r"authori[sz]ation denied|unable to open|not permitted",
                                                                  msg, re.I))
        return [], fda, None if fda else msg[:160]
    try:
        auth_col = "auth_value" if "auth_value" in cols else "allowed" if "allowed" in cols else None
        sel = ["service", "client", "client_type" if "client_type" in cols else "0",
               auth_col or "NULL", "last_modified" if "last_modified" in cols else "NULL"]
        rows = con.execute(f"SELECT {', '.join(sel)} FROM access").fetchall()
    except sqlite3.Error as e:
        return [], False, str(e)[:160]
    finally:
        con.close()
    out = []
    for service, client, ctype, auth, mod in rows:
        svc = re.sub(r"^kTCCService", "", service or "")
        if auth_col == "allowed":
            auth = 2 if auth else 0
        why = protected_client(client)
        out.append(dict(service=svc, client=client, client_type="bundle" if ctype == 0 else "path",
                        auth=AUTH.get(auth, f"other:{auth}"),
                        last_modified=time.strftime("%Y-%m-%d", time.localtime(mod)) if isinstance(mod, int) and mod else None,
                        resettable=ctype == 0 and not why and svc in TCC_SERVICES, protected=why))
    return out, False, None


def gather(home=None):
    items = browser_data(home) + recent_items(home)
    tp = tcc_path(home)
    perms, fda, err = read_tcc(tp)
    items.append(registry.entry(NAME, tp, name="Quyền của app (TCC.db, của bạn)", safety="protected",
                                delete_mode="list-only", kind="tcc-db", needsFDA=fda,
                                reason="CSDL quyền của macOS — chỉ đọc; thu hồi từng quyền bằng `privacy reset`"
                                       + (f"; cần Full Disk Access ({FDA_PANE})" if fda else "")))
    return dict(items=items, permissions=perms, tcc_needsFDA=fda, tcc_error=err)


def summarize(g):
    items, perms = g["items"], g["permissions"]
    kinds = {}
    for e in items:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    by_auth = {}
    for p in perms:
        by_auth[p["auth"]] = by_auth.get(p["auth"], 0) + 1
    return dict(items=len(items), by_kind=kinds, needsFDA=sum(1 for e in items if e.get("needsFDA")),
                browser_bytes=sum(e["size"] or 0 for e in items if e["kind"].startswith("browser-")),
                browsers=sorted({e["browser"] for e in items if e.get("browser")}),
                permissions=len(perms), permissions_by_auth=by_auth,
                services=len({p["service"] for p in perms}), clients=len({p["client"] for p in perms}),
                resettable=sum(1 for p in perms if p["resettable"]), tcc_needsFDA=g["tcc_needsFDA"],
                tcc_error=g["tcc_error"])


def collect(ctx):
    g = gather(ctx.home)
    return {"summary": summarize(g), "items": g["items"], "permissions": g["permissions"]}


# ---------- reset ----------
def normalize_service(s):
    s = re.sub(r"^kTCCService", "", (s or "").strip())
    for k in TCC_SERVICES:
        if k.lower() == s.lower():
            return k
    return None


def reset(service, bundle_id, yes=False, run=run_cmd, data=None, out=print):
    """tccutil reset <dịch vụ> <bundle id>. Không yes: chỉ xem trước. Mã thoát: 0 ok/xem trước, 1 lỗi, 2 từ chối."""
    svc = normalize_service(service)
    if not svc:
        out(f"Từ chối: dịch vụ '{service}' không nằm trong danh sách: {', '.join(TCC_SERVICES)}")
        return 2
    bid = (bundle_id or "").strip()
    if not BUNDLE_RE.match(bid):
        out(f"Từ chối: '{bundle_id}' không phải bundle id (vd com.example.app) — chỉ reset theo bundle id của app.")
        return 2
    why = protected_client(bid)
    if why:
        out(f"Từ chối '{bid}': {why} — không đụng quyền của mục này.")
        return 2
    cmd = [TCCUTIL, "reset", svc, bid]
    if not yes:
        out(f"Xem trước: {' '.join(cmd)}\n  → app sẽ phải xin lại quyền {svc}; KHÔNG hoàn tác được. Thêm --yes để chạy.")
        return 0
    rc, o, e = run(cmd)
    msg = (o or e or "").strip()[:200]
    path = os.path.join(data or S.DATA, "logs", "privacy-" + time.strftime("%Y%m%d-%H%M%S") + ".jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(dict(ts=time.strftime("%Y-%m-%dT%H:%M:%S"), action="tccutil-reset", service=svc,
                                bundle_id=bid, rc=rc, output=msg), ensure_ascii=False) + "\n")
    if rc != 0:
        out(f"Lỗi: tccutil thoát mã {rc}: {msg} · log {path}")
        return 1
    out(f"Đã reset quyền {svc} của {bid} · log {path}")
    return 0


# ---------- CLI ----------
def cli(args):
    if args.action == "reset":
        if not (args.service and args.bundle_id):
            print("cần: privacy reset <dịch vụ> <bundle id> [--yes]")
            return 1
        return reset(args.service, args.bundle_id, yes=args.yes)
    g = gather()
    s = summarize(g)
    if args.json:
        print(json.dumps({"summary": s, "items": g["items"], "permissions": g["permissions"]}, ensure_ascii=False))
        return 0
    print("== Dữ liệu trình duyệt (chỉ vị trí + dung lượng, không tự xoá)")
    for e in g["items"]:
        if e["kind"].startswith("browser-"):
            print(f"  {e['name']:<48} {'cần FDA' if e['needsFDA'] else S.fmt(e['size']):>9}  {e['path']}")
    print("== Mục gần đây")
    for e in g["items"]:
        if e["kind"] == "recent-items":
            print(f"  {e['name']:<48} {'cần FDA' if e['needsFDA'] else S.fmt(e['size']):>9}")
    print(f"== Quyền của app (TCC): {s['permissions']} mục")
    if g["tcc_needsFDA"]:
        print(f"  cần Full Disk Access để đọc TCC.db — mở {FDA_PANE}")
    elif g["tcc_error"]:
        print(f"  ! {g['tcc_error']}")
    for p in sorted(g["permissions"], key=lambda p: (p["service"], p["client"] or "")):
        print(f"  {p['service']:<28} {p['auth']:<8} {p['client']}" + ("" if p["resettable"] else "  [không reset]"))
    return 0


def register(subparsers):
    p = subparsers.add_parser(NAME, help="dữ liệu riêng tư: trình duyệt, mục gần đây, quyền app (chỉ liệt kê; reset quyền)")
    p.add_argument("action", nargs="?", default="list", choices=("list", "reset"))
    p.add_argument("service", nargs="?", help="dịch vụ TCC (vd Camera, Microphone, ScreenCapture)")
    p.add_argument("bundle_id", nargs="?", help="bundle id của app")
    p.add_argument("--yes", action="store_true", help="thực hiện (không có: chỉ xem trước)")
    p.add_argument("--json", action="store_true", help="in JSON (với list)")
    p.set_defaults(func=cli)
