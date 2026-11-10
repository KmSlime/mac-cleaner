"""Mục khởi động và chạy nền: LaunchAgents/LaunchDaemons, login item + background item (BTM), agent hỏng.

  python3 maccleaner.py startup list [--json]
  python3 maccleaner.py startup disable <label> [--yes]   chỉ agent CẤP USER: bootout + đổi tên plist → *.plist.disabled
  python3 maccleaner.py startup enable <label> [--yes]    đổi tên lại *.plist + bootstrap
  scan → report["features"]["startup"] = {"summary": {...}, "items": [...]}

Không sudo. Mục hệ thống (/Library/...) chỉ liệt kê. Không xoá gì: tắt = đổi tên (hoàn tác bằng `enable`).
Mục của Apple, phần mềm bảo mật hay quản lý thiết bị (EDR/MDM) luôn bị từ chối.
"""
import json
import os
import re
import subprocess
import time
from urllib.parse import unquote

from mclib import registry, safety as S
from mclib.features import admin

NAME = "startup"
LAUNCHCTL = "/bin/launchctl"
SFLTOOL = "/usr/bin/sfltool"
SUDO = "/usr/bin/sudo"
DISABLED_SUFFIX = ".disabled"
SYSTEM_DIRS = (("/Library/LaunchAgents", "agent"), ("/Library/LaunchDaemons", "daemon"))
# Loại mục BTM không phải thứ chạy lúc khởi động (nhóm theo hãng, plug-in Quick Look/Spotlight, dock tile).
BTM_SKIP_TYPES = ("developer", "quicklook", "spotlight", "dock tile")
APPLE_PREFIX = "com.apple."
APPLE_TREES = ("/System/", "/usr/", "/bin/", "/sbin/", "/Library/Apple/")
# bootout khi agent chưa nạp: không phải lỗi (launchctl trả 3 "No such process", 5 "Input/output error", 113 "not found").
NOT_LOADED_HINTS = ("could not find", "no such process", "not loaded", "service is disabled")
NOT_LOADED_CODES = (3, 5, 113)


def run_cmd(cmd, timeout=30):
    """(mã thoát, stdout, stderr). Lỗi chạy lệnh → (None, "", lý do)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except (OSError, subprocess.SubprocessError) as e:
        return None, "", str(e)


# ---------- sfltool dumpbtm (hỏi mật khẩu một lần) ----------
# Hết hỏi hẳn: `admin grant btm-read` (mclib/features/admin.py) → chạy qua `sudo -n`, không hộp thoại.
# `sfltool dumpbtm` bắt nhập mật khẩu admin MỖI lần chạy (hộp thoại "sfltool wants to make changes"), mà scan chạy định kỳ
# (autoclean, app, lệnh) → nhớ kết quả theo chữ ký file CSDL BTM: chỉ hỏi lại khi login item / mục chạy nền thực sự đổi.
# Cache để ở Application Support (thứ mac-cleaner không dọn), dùng chung cho mọi nơi gọi, bất kể MACCLEANER_DATA.
BTM_DB_DIR = "/private/var/db/com.apple.backgroundtaskmanagement"
BTM_CACHE = os.path.join(S.HOME, "Library", "Application Support", "MacCleaner", "dumpbtm-cache.json")


def btm_signature(db_dir=BTM_DB_DIR):
    """Chữ ký (tên:mtime_ns:size của mọi BackgroundItems-v*.btm) — đổi khi macOS ghi lại CSDL BTM. None nếu không đọc được."""
    try:
        names = sorted(n for n in os.listdir(db_dir) if n.startswith("BackgroundItems-") and n.endswith(".btm"))
        parts = []
        for n in names:
            st = os.stat(os.path.join(db_dir, n))
            parts.append(f"{n}:{st.st_mtime_ns}:{st.st_size}")
    except OSError:
        return None
    return "|".join(parts) or None


def dumpbtm(run=run_cmd, cache=None, db_dir=BTM_DB_DIR, timeout=60, sudo=False):
    """(mã thoát, stdout, stderr) của `sfltool dumpbtm`. cache=đường dẫn file → dùng lại kết quả khi chữ ký CSDL BTM chưa đổi
    (kể cả lần bị từ chối: không hỏi lại tới khi BTM đổi hoặc xoá file cache). Không cache khi lệnh không chạy được/quá giờ.
    sudo=True (the user ran `admin grant btm-read`) → `sudo -n` first, no dialog at all; a refusal remembered from
    before the grant is then ignored."""
    sig = btm_signature(db_dir) if cache else None
    if sig:
        try:
            with open(cache, encoding="utf-8") as f:
                c = json.load(f)
            if c.get("sig") == sig and (c.get("rc") == 0 or not sudo):
                err = c.get("err") or ""
                if c.get("rc") != 0:
                    err = (err.strip() + f" (kết quả nhớ từ lần trước; xoá {cache} để hỏi lại)").strip()
                return c.get("rc"), c.get("out") or "", err
        except (OSError, ValueError, AttributeError):
            pass
    rc = None
    if sudo:
        rc, out, err = run([SUDO, "-n", SFLTOOL, "dumpbtm"], timeout=timeout)
    if rc != 0:  # no grant for this command (or sudo refused) → the plain call, with macOS's own password dialog
        rc, out, err = run([SFLTOOL, "dumpbtm"], timeout=timeout)
    if sig and rc is not None:
        try:
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            tmp = cache + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(dict(sig=sig, rc=rc, out=out, err=err, at=time.strftime("%Y-%m-%d %H:%M:%S")), f)
            os.replace(tmp, cache)
        except OSError:
            pass
    return rc, out, err


# ---------- parse ----------
def parse_launchctl_list(text):
    """`launchctl list` → {label: {"pid": int|None, "status": str}}."""
    out = {}
    for line in (text or "").splitlines():
        cols = line.split("\t")
        if len(cols) != 3 or cols[2] == "Label":
            continue
        pid, status, label = (c.strip() for c in cols)
        out[label] = dict(pid=int(pid) if pid.isdigit() else None, status=status)
    return out


def parse_print_disabled(text):
    """`launchctl print-disabled gui/<uid>` → {label: True nếu bị tắt bằng `launchctl disable`}."""
    out = {}
    for m in re.finditer(r'"([^"]+)"\s*=>\s*(\w+)', text or ""):
        out[m.group(1)] = m.group(2).lower() in ("disabled", "true")
    return out


def _file_url_path(url):
    if not url or not url.startswith("file://"):
        return None
    p = unquote(url[len("file://"):])
    return p.rstrip("/") or "/"


def parse_btm(text):
    """Output `sfltool dumpbtm` → danh sách mục: uid, name, developer, team, type, disposition[], enabled, allowed,
    identifier, url, path (từ URL file://), executable, bundle_id, parent, embedded[]."""
    items, cur, uid, in_embedded = [], None, None, False
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        m = re.search(r"Records for UID (-?\d+)", line)
        if m:
            uid, cur = int(m.group(1)), None
            continue
        if re.match(r"^\s*#\d+:\s*$", line):
            cur = dict(uid=uid, name="", developer="", team="", type="", type_code="", disposition=[], enabled=None,
                       allowed=None, identifier="", url=None, path=None, executable=None, bundle_id="", parent="",
                       embedded=[])
            items.append(cur)
            in_embedded = False
            continue
        if cur is None:
            continue
        m = re.match(r"^\s*#\d+:\s+(\S.*)$", line)
        if in_embedded and m:
            cur["embedded"].append(m.group(1).strip())
            continue
        m = re.match(r"^\s*([A-Za-z][A-Za-z ]*?):\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1).strip(), m.group(2).strip()
        in_embedded = key == "Embedded Item Identifiers"
        if val == "(null)":
            val = ""
        if key == "Name":
            cur["name"] = val
        elif key == "Developer Name":
            cur["developer"] = val
        elif key == "Team Identifier":
            cur["team"] = val
        elif key == "Type":
            t = re.match(r"^(.*?)\s*\((0x[0-9a-fA-F]+)\)\s*$", val)
            cur["type"], cur["type_code"] = (t.group(1), t.group(2)) if t else (val, "")
        elif key == "Disposition":
            cur["disposition"] = [w.strip() for w in re.sub(r"\(.*?\)", "", val).strip(" []").split(",") if w.strip()]
            cur["enabled"] = "enabled" in cur["disposition"] if cur["disposition"] else None
            cur["allowed"] = "allowed" in cur["disposition"] if cur["disposition"] else None
        elif key == "Identifier":
            cur["identifier"] = val
        elif key == "URL":
            cur["url"] = val or None
            cur["path"] = _file_url_path(val)
        elif key == "Executable Path":
            cur["executable"] = val or None
        elif key == "Bundle Identifier":
            cur["bundle_id"] = val
        elif key == "Parent Identifier":
            cur["parent"] = val
    return items


# ---------- đọc plist ----------
def plist_program(d):
    """(chương trình, đối số) mà plist launchd chạy. Chương trình không phải đường dẫn tuyệt đối → None."""
    args = d.get("ProgramArguments") if isinstance(d.get("ProgramArguments"), list) else []
    prog = d.get("Program") or (args[0] if args else None)
    if not isinstance(prog, str):
        return None, [str(a) for a in args]
    prog = os.path.expanduser(prog)
    return (prog if prog.startswith("/") else None), [str(a) for a in args]


def app_of(path):
    """Tên app (.app) chứa đường dẫn, hoặc ""."""
    m = re.search(r"([^/]+)\.app(/|$)", path or "")
    return m.group(1) if m else ""


def refusal(rec):
    """Lý do KHÔNG được tắt/bật mục này (Apple, bảo mật, MDM), hoặc None."""
    label = (rec.get("label") or "").lower()
    prog = rec.get("program") or ""
    ids = [label] + [b.lower() for b in rec.get("bundle_ids", []) if isinstance(b, str)]
    # Theo label/bundle id, KHÔNG theo chương trình: agent của bạn chạy /usr/bin/python3 vẫn là agent của bạn.
    if any(i.startswith(APPLE_PREFIX) for i in ids) or (rec.get("plist") or "").startswith(APPLE_TREES):
        return "mục của Apple/macOS — không đụng"
    if any(S.security_agent("/" + i) for i in ids if i) or any(
            S.security_agent(p.lower()) for p in (rec.get("plist") or "", prog) if p):
        return "thuộc phần mềm bảo mật/quản lý thiết bị (EDR/MDM) — tắt có thể bị coi là can thiệp; dùng công cụ của hãng"
    return None


def read_agent(path, kind, scope):
    d = S.plist(path)
    prog, args = plist_program(d)
    bids = d.get("AssociatedBundleIdentifiers") or []
    if isinstance(bids, str):
        bids = [bids]
    rec = dict(kind=kind, scope=scope, plist=path, label=d.get("Label") if isinstance(d.get("Label"), str) else "",
               program=prog, args=args, bundle_ids=[b for b in bids if isinstance(b, str)],
               program_exists=os.path.exists(prog) if prog else None,
               plist_disabled=bool(d.get("Disabled")), renamed_disabled=path.endswith(DISABLED_SUFFIX),
               readable=bool(d), owner=app_of(prog) or (bids[0] if bids and isinstance(bids[0], str) else ""),
               loaded=None, pid=None, launchctl_disabled=None, btm=None)
    rec["broken"] = rec["program_exists"] is False
    return rec


def list_plists(d):
    """*.plist và *.plist.disabled trong thư mục (không đi sâu, không theo symlink thư mục)."""
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return None
    return [os.path.join(d, n) for n in names if n.endswith((".plist", ".plist" + DISABLED_SUFFIX))
            and not n.startswith(".") and os.path.isfile(os.path.join(d, n))]


def user_agents_dir(home=None):
    return os.path.join(home or S.HOME, "Library", "LaunchAgents")


def gather(home=None, run=run_cmd, uid=None, system_dirs=SYSTEM_DIRS):
    """Kiểm kê (chỉ đọc). Trả {"records": [...], "errors": [...], "btm_skipped": n}."""
    uid = os.getuid() if uid is None else uid
    records, errors = [], []
    udir = user_agents_dir(home)
    found = list_plists(udir)
    for p in found or []:
        records.append(read_agent(p, "agent", "user"))
    for d, kind in system_dirs:
        found = list_plists(d)
        if found is None:
            if os.path.isdir(d):
                errors.append(f"không đọc được {d}")
            continue
        for p in found:
            records.append(read_agent(p, kind, "system"))

    rc, out, err = run([LAUNCHCTL, "list"])
    loaded = parse_launchctl_list(out) if rc == 0 else None
    if loaded is None:
        errors.append(f"launchctl list lỗi: {(err or '').strip()[:120] or rc}")
    rc, out, _ = run([LAUNCHCTL, "print-disabled", f"gui/{uid}"])
    overrides = parse_print_disabled(out) if rc == 0 else {}
    for r in records:
        if not r["label"] or r["renamed_disabled"]:
            r["loaded"] = False if r["renamed_disabled"] else None
            continue
        if r["kind"] == "daemon":
            rc, _, _ = run([LAUNCHCTL, "print", f"system/{r['label']}"])
            r["loaded"] = None if rc is None else rc == 0
        elif loaded is not None:
            info = loaded.get(r["label"])
            r["loaded"], r["pid"] = (info is not None), (info or {}).get("pid")
        if r["label"] in overrides:
            r["launchctl_disabled"] = overrides[r["label"]]

    real = run is run_cmd  # chỉ cache và dùng quyền đã cấp với lệnh thật, không với fake của test
    rc, out, err = dumpbtm(run, cache=BTM_CACHE if real else None, sudo=real and admin.installed())
    btm = parse_btm(out) if rc == 0 else []
    if rc != 0 or not btm:
        errors.append("sfltool dumpbtm không có dữ liệu" + (f": {(err or '').strip()[:120]}" if err else ""))
    by_id = {b["identifier"]: b for b in btm if b["identifier"]}
    by_plist = {r["plist"]: r for r in records}
    skipped = 0
    for b in btm:
        parent = by_id.get(b["parent"])
        b["owner"] = (parent or {}).get("name") or b["developer"] or b["name"]
        if b["type"] in BTM_SKIP_TYPES:
            skipped += 1
            continue
        rec = by_plist.get(b["path"] or "")
        if rec is not None:          # mục BTM ứng với plist đã liệt kê → gộp thông tin, không thành mục riêng
            rec["btm"] = dict(type=b["type"], enabled=b["enabled"], allowed=b["allowed"])
            rec["owner"] = rec["owner"] or b["owner"]
            continue
        exe = b["executable"]
        if exe and not exe.startswith("/") and (parent or {}).get("path"):
            exe = os.path.join(parent["path"], exe)   # SMAppService: đường dẫn tương đối theo bundle của app cha
        path = b["path"] or exe
        if not path:
            skipped += 1
            continue
        kind = "login-item" if b["type"] in ("app", "login item") else "btm"
        records.append(dict(kind=kind, scope="user" if b["uid"] == uid else "system", plist=None, path=path,
                            label=b["bundle_id"] or re.sub(r"^\d+\.", "", b["identifier"]), name=b["name"], program=exe, args=[],
                            bundle_ids=[b["bundle_id"]] if b["bundle_id"] else [],
                            program_exists=os.path.exists(exe) if exe and exe.startswith("/") else None,
                            plist_disabled=False, renamed_disabled=False, readable=True, owner=b["owner"],
                            loaded=None, pid=None, launchctl_disabled=None,
                            btm=dict(type=b["type"], enabled=b["enabled"], allowed=b["allowed"])))
        records[-1]["broken"] = records[-1]["program_exists"] is False
    for r in records:
        r["refusal"] = refusal(r)
        r["can_disable"] = (r["scope"] == "user" and r["kind"] == "agent" and not r["renamed_disabled"]
                            and not r["refusal"] and bool(r["label"]))
    return dict(records=records, errors=errors, btm_skipped=skipped)


def state_of(r):
    if r.get("plist") and not r["label"]:
        return "plist không đọc được hoặc thiếu Label — launchd không nạp"
    if r["renamed_disabled"]:
        return "đã tắt (đổi tên .disabled)"
    if r.get("launchctl_disabled") or r["plist_disabled"]:
        return "bị tắt (launchctl disable / khoá Disabled)"
    if r.get("btm") and r["btm"].get("enabled") is False:
        return "tắt trong Cài đặt (mục nền)"
    if r["loaded"]:
        return f"đang chạy (pid {r['pid']})" if r.get("pid") else "đã nạp"
    if r["loaded"] is None and r.get("btm") and r["btm"].get("enabled"):
        return "bật (mục đăng nhập/chạy nền)"
    return "chưa nạp" if r["loaded"] is False else "không rõ"


def to_entry(r):
    path = r.get("plist") or r.get("path")
    reasons = []
    safety, mode = "inventory", "list-only"
    if r["refusal"]:
        safety = "protected"
        reasons.append(r["refusal"])
    elif r["broken"]:
        safety = "review"
        reasons.append("agent hỏng: chương trình không còn — đề xuất chuyển plist vào Thùng rác "
                       "(mục 'agent-leftover' của scan → clean)" if r["scope"] == "user" and r["kind"] == "agent"
                       else "chương trình không còn")
    if r["scope"] == "system":
        reasons.append("mục hệ thống — sửa cần quyền admin, chỉ liệt kê")
    elif r["can_disable"]:
        reasons.append("tắt được: `startup disable <label>` (đổi tên, hoàn tác bằng `enable`)")
    elif r["renamed_disabled"]:
        reasons.append("bật lại: `startup enable <label>`")
    return registry.entry(NAME, path, name=r.get("label") or r.get("name") or "", safety=safety, delete_mode=mode,
                          reason="; ".join(reasons), kind=r["kind"], scope=r["scope"], label=r.get("label") or "",
                          program=r.get("program"), owner=r.get("owner") or "", loaded=r["loaded"],
                          state=state_of(r), broken=r["broken"], can_disable=r["can_disable"])


def summarize(g):
    recs = g["records"]
    count = lambda f: sum(1 for r in recs if f(r))  # noqa: E731
    return dict(total=len(recs), user_agents=count(lambda r: r["kind"] == "agent" and r["scope"] == "user"),
                system_agents=count(lambda r: r["kind"] == "agent" and r["scope"] == "system"),
                system_daemons=count(lambda r: r["kind"] == "daemon"),
                login_items=count(lambda r: r["kind"] == "login-item"), background_items=count(lambda r: r["kind"] == "btm"),
                loaded=count(lambda r: r["loaded"]), broken=count(lambda r: r["broken"]),
                disabled=count(lambda r: r["renamed_disabled"]), protected=count(lambda r: r["refusal"]),
                btm_skipped=g["btm_skipped"], errors=g["errors"])


def entries(g):
    out, seen = [], set()
    for r in g["records"]:
        p = r.get("plist") or r.get("path")
        if not p or not os.path.isabs(p) or p in seen:
            continue
        seen.add(p)
        out.append(to_entry(r))
    return out


def collect(ctx):
    g = gather(home=ctx.home)
    return {"summary": summarize(g), "items": entries(g)}


# ---------- tắt / bật ----------
def _log(data, rec):
    path = os.path.join(data or S.DATA, "logs", "startup-" + time.strftime("%Y%m%d-%H%M%S") + ".jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return path


def _find(label, home, system_dirs):
    """(mục user khớp label, mục hệ thống khớp label)."""
    user = [read_agent(p, "agent", "user") for p in list_plists(user_agents_dir(home)) or []]
    system = []
    for d, kind in system_dirs:
        system += [read_agent(p, kind, "system") for p in list_plists(d) or []]
    return [r for r in user if r["label"] == label], [r for r in system if r["label"] == label]


def change(action, label, yes=False, home=None, run=run_cmd, uid=None, system_dirs=SYSTEM_DIRS, data=None, out=print):
    """action = "disable" | "enable". Không yes: chỉ xem trước. Trả mã thoát (0 ok/xem trước, 1 lỗi, 2 từ chối)."""
    uid = os.getuid() if uid is None else uid
    user, system = _find(label, home, system_dirs)
    want_renamed = action == "enable"
    match = [r for r in user if r["renamed_disabled"] == want_renamed]
    if not match:
        if [r for r in user if r["renamed_disabled"] != want_renamed]:
            out(f"'{label}' đã {'bật' if want_renamed else 'tắt'} sẵn — không làm gì.")
            return 0
        if system:
            out(f"Từ chối: '{label}' là mục hệ thống ({os.path.dirname(system[0]['plist'])}) — cần quyền admin, chỉ liệt kê.")
            return 2
        out(f"Không tìm thấy agent cấp user có label '{label}' trong {user_agents_dir(home)}.")
        return 1
    if len(match) > 1:
        out(f"Từ chối: {len(match)} plist cùng label '{label}' — xử lý tay cho rõ.")
        return 2
    r = match[0]
    why = refusal(r)
    if why:
        out(f"Từ chối '{label}': {why}.")
        return 2
    src = r["plist"]
    dst = src + DISABLED_SUFFIX if action == "disable" else src[:-len(DISABLED_SUFFIX)]
    if os.path.lexists(dst):
        out(f"Từ chối: đã có {dst} — không ghi đè.")
        return 2
    if r["broken"]:
        out(f"Lưu ý: agent hỏng (chương trình không còn: {r['program']}) — nên chuyển plist vào Thùng rác "
            "(scan → clean mục 'agent-leftover').")
    domain = f"gui/{uid}"
    if action == "disable":
        steps = [f"launchctl bootout {domain} {src}", f"đổi tên {os.path.basename(src)} → {os.path.basename(dst)}"]
    else:
        steps = [f"đổi tên {os.path.basename(src)} → {os.path.basename(dst)}", f"launchctl bootstrap {domain} {dst}"]
    if not yes:
        out(f"Xem trước ({action} '{label}'):\n  " + "\n  ".join(steps) + "\nThêm --yes để thực hiện.")
        return 0
    note = ""
    if action == "disable":
        rc, o, e = run([LAUNCHCTL, "bootout", domain, src])
        msg = (e or o or "").strip()
        if rc != 0 and rc not in NOT_LOADED_CODES and not any(h in msg.lower() for h in NOT_LOADED_HINTS):
            out(f"Lỗi: launchctl bootout thất bại ({rc}): {msg[:200]} — chưa đổi tên gì.")
            return 1
        note = "" if rc == 0 else f"agent chưa nạp ({msg[:80]})"
        try:
            os.rename(src, dst)
        except OSError as e:
            out(f"Lỗi: đã bootout nhưng không đổi tên được plist ({e}) — agent sẽ nạp lại ở lần đăng nhập sau.")
            return 1
    else:
        try:
            os.rename(src, dst)
        except OSError as e:
            out(f"Lỗi: không đổi tên được plist ({e}) — chưa bật gì.")
            return 1
        rc, o, e = run([LAUNCHCTL, "bootstrap", domain, dst])
        if rc != 0:
            note = f"đã đổi tên lại nhưng bootstrap lỗi ({rc}): {(e or o or '').strip()[:160]} — sẽ nạp ở lần đăng nhập sau"
    log = _log(data, dict(ts=time.strftime("%Y-%m-%dT%H:%M:%S"), action=action, label=label, src=src, dst=dst,
                          note=note))
    out(f"Đã {'tắt' if action == 'disable' else 'bật'} '{label}'" + (f" · {note}" if note else "") + f" · log {log}")
    return 0


# ---------- CLI ----------
def cli(args):
    if args.action in ("disable", "enable"):
        if not args.label:
            print(f"cần label: startup {args.action} <label> [--yes]")
            return 1
        return change(args.action, args.label, yes=args.yes)
    g = gather()
    if args.json:
        print(json.dumps({"summary": summarize(g), "items": entries(g)}, ensure_ascii=False))
        return 0
    titles = {("agent", "user"): "LaunchAgents của bạn", ("agent", "system"): "LaunchAgents hệ thống (chỉ liệt kê)",
              ("daemon", "system"): "LaunchDaemons hệ thống (chỉ liệt kê)", ("login-item", "user"): "Login item",
              ("btm", "user"): "Mục chạy nền (BTM)"}
    groups = {}
    for r in g["records"]:
        groups.setdefault((r["kind"], r["scope"]), []).append(r)
    for key, recs in groups.items():
        print(f"\n== {titles.get(key, ' / '.join(key))} ({len(recs)})")
        for r in recs:
            tag = " [HỎNG]" if r["broken"] else ""
            tag += " [không đụng]" if r["refusal"] else ""
            print(f"  {r.get('label') or r.get('name') or '?'}{tag} · {state_of(r)}"
                  + (f" · {r['owner']}" if r.get("owner") else ""))
            if r.get("program"):
                print(f"      {r['program']}")
    s = summarize(g)
    print(f"\nTổng {s['total']} mục · đã nạp {s['loaded']} · hỏng {s['broken']} · đã tắt {s['disabled']}")
    for e in g["errors"]:
        print(f"  ! {e}")
    return 0


def register(subparsers):
    p = subparsers.add_parser(NAME, help="mục khởi động/chạy nền: liệt kê, tắt/bật agent cấp user (hoàn tác được)")
    p.add_argument("action", nargs="?", default="list", choices=("list", "disable", "enable"))
    p.add_argument("label", nargs="?", help="label của LaunchAgent (với disable/enable)")
    p.add_argument("--yes", action="store_true", help="thực hiện (không có: chỉ xem trước)")
    p.add_argument("--json", action="store_true", help="in JSON (với list)")
    p.set_defaults(func=cli)
