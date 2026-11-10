"""Self-update: look at this project's GitHub releases, update by hand or on a schedule the user consented to.

  python3 maccleaner.py self-update status [--json]          version, install kind, schedule (no network)
  python3 maccleaner.py self-update check [--json]           one HTTPS request to api.github.com
  python3 maccleaner.py self-update apply [--yes] [--json]   install the latest release (preview without --yes)
  python3 maccleaner.py self-update auto on [--every daily|weekly] [--at HH:MM] [--install|--no-install] [--yes]
  python3 maccleaner.py self-update auto off
  python3 maccleaner.py self-update run                      what the schedule runs: check, then notify or install

Nothing here opens a connection unless the user asked: `check`/`apply` are explicit, and the scheduled check exists only
after `auto on --yes` (consent is recorded in <data>/self-update.json; without --yes the command only explains).
The schedule is one user LaunchAgent (no sudo). `auto off` removes it.

How an update is installed depends on where this copy lives:
  source  (git clone + install.sh)  git fetch --tags, fast-forward to the release tag, re-run install.sh
  app     (inside MacCleaner.app)   download the release zip, check its .sha256, bundle id, version and code signature,
                                    move the old app to the Trash and put the new one in its place
  other                             not updated automatically; the release URL is printed
"""
import argparse
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

import mclib
from mclib import safety as S
from mclib.features.updates import newer

NAME = "self-update"
REPO = "KmSlime/mac-cleaner"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
LABEL = "local.maccleaner.selfupdate"   # LaunchAgent label
BUNDLE_ID = "local.maccleaner"
TAG_RE = re.compile(r"v?\d+\.\d+\.\d+")
USER_AGENT = "mac-cleaner-self-update"
TIMEOUT = 20
MAX_JSON = 1 << 20
MAX_ZIP = 200 << 20
EVERY = ("daily", "weekly")
DEFAULTS = {"auto": False, "every": "daily", "at": "09:00", "install": False}
GIT, DITTO, CODESIGN, LAUNCHCTL, OSASCRIPT = ("/usr/bin/git", "/usr/bin/ditto", "/usr/bin/codesign", "/bin/launchctl",
                                              "/usr/bin/osascript")
CONSENT = ("Bật tự kiểm tra nghĩa là bạn cho phép Mac Cleaner tự kết nối mạng theo lịch: mỗi lần một request HTTPS tới "
           "api.github.com để đọc số phiên bản mới nhất (không gửi dữ liệu nào của máy). Lịch chạy bằng một LaunchAgent "
           "của user (không sudo); tắt bất cứ lúc nào bằng `self-update auto off`.")


class UpdateError(Exception):
    """A failure the user should read (message is Vietnamese, shown as is)."""


def _call(cmd, timeout=60, cwd=None):
    """Run a command by absolute path → (exit code, stdout+stderr). Never raises."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return 1, f"{type(e).__name__}: {e}"


class Env:
    """Everything that touches the machine or the network; tests replace the parts."""

    def __init__(self, root=None, data=None, home=None, opener=None, call=None, trash=None, running=None):
        self.root = root or S.ROOT
        self.data = data or S.DATA
        self.home = home or S.HOME
        self.opener = opener or urllib.request.urlopen
        self.call = call or _call
        self.trash = trash or S.to_trash
        self.running = running or S.running_bundle_ids


def install_kind(root):
    if os.path.exists(os.path.join(root, ".git")):
        return "source"
    if root.rstrip("/").endswith(".app/Contents/Resources"):
        return "app"
    return "other"


# ---------- settings ----------
def config_path(env):
    return os.path.join(env.data, "self-update.json")


def load_config(env):
    try:
        with open(config_path(env), encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    return {**DEFAULTS, **(cfg if isinstance(cfg, dict) else {})}


def save_config(env, cfg):
    os.makedirs(env.data, exist_ok=True)
    tmp = config_path(env) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    os.replace(tmp, config_path(env))


def agent_path(env):
    return os.path.join(env.home, "Library", "LaunchAgents", LABEL + ".plist")


def status(env):
    cfg = load_config(env)
    return {"version": mclib.__version__, "kind": install_kind(env.root), "auto": bool(cfg["auto"]), "every": cfg["every"],
            "at": cfg["at"], "install": bool(cfg["install"]), "agent": os.path.exists(agent_path(env)),
            "last": cfg.get("last"), "consent": CONSENT}


# ---------- network ----------
def _host_ok(url):
    u = urllib.parse.urlparse(url)
    h = u.hostname or ""
    return u.scheme == "https" and (h in ("github.com", "api.github.com") or h.endswith(".githubusercontent.com"))


def _get(env, url, limit):
    """GET over HTTPS from GitHub only (also after redirects), at most `limit` bytes."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with env.opener(req, timeout=TIMEOUT) as r:
            if not _host_ok(r.geturl()):
                raise UpdateError(f"máy chủ lạ khi tải {url}")
            data = r.read(limit + 1)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise UpdateError(f"không tải được {url}: {e}") from None
    if len(data) > limit:
        raise UpdateError(f"dữ liệu từ {url} lớn bất thường (> {S.fmt(limit)})")
    return data


def fetch_latest(env):
    """Latest published release → {version, tag, url, assets: {file name: download URL}}."""
    try:
        rel = json.loads(_get(env, API, MAX_JSON))
    except ValueError:
        raise UpdateError("GitHub trả dữ liệu không đọc được") from None
    tag = str(rel.get("tag_name") or "") if isinstance(rel, dict) else ""
    if not TAG_RE.fullmatch(tag):  # the tag ends up in file names and a git argument
        raise UpdateError(f"tag release không đúng dạng vX.Y.Z: {tag[:40]!r}")
    assets = {str(a.get("name")): str(a.get("browser_download_url")) for a in rel.get("assets") or [] if isinstance(a, dict)}
    return {"version": tag.lstrip("v"), "tag": tag, "url": f"https://github.com/{REPO}/releases/tag/{tag}", "assets": assets}


def check(env, now=None):
    """Ask GitHub, remember the answer in the settings file → (last, release or None)."""
    last, rel = {"checked_at": int(now if now is not None else time.time()), "current": mclib.__version__}, None
    try:
        rel = fetch_latest(env)
        last.update(latest=rel["version"], update=newer(rel["version"], mclib.__version__), url=rel["url"])
    except UpdateError as e:
        last["error"] = str(e)
    cfg = load_config(env)
    cfg["last"] = last
    save_config(env, cfg)
    return last, rel


# ---------- install ----------
def _apply_source(env, rel, yes, log):
    root, tag = env.root, rel["tag"]
    steps = ["git fetch --tags origin", f"git merge --ff-only {tag}", "./install.sh"]
    rc, out = env.call([GIT, "-C", root, "status", "--porcelain", "--untracked-files=no"], timeout=30)
    if rc != 0:
        raise UpdateError(f"không đọc được trạng thái git của {root}: {out[-200:]}")
    if out.strip():
        raise UpdateError(f"{root} có thay đổi chưa commit — commit hoặc stash rồi chạy lại (không tự ghi đè)")
    if not yes:
        return {"status": "preview", "kind": "source", "version": rel["version"], "steps": steps, "restart": False}
    for cmd, timeout in (([GIT, "-C", root, "fetch", "--tags", "origin"], 180),
                         ([GIT, "-C", root, "merge", "--ff-only", f"refs/tags/{tag}"], 60),
                         (["/bin/bash", os.path.join(root, "install.sh")], 3600)):
        log("$ " + " ".join(cmd))
        rc, out = env.call(cmd, timeout=timeout, cwd=root)
        if rc != 0:
            raise UpdateError(f"lệnh `{' '.join(cmd[-3:])}` lỗi (mã {rc}): {out[-300:]}")
    return {"status": "ok", "kind": "source", "version": rel["version"], "steps": steps, "restart": True}


def _apply_app(env, rel, yes, log):
    app = env.root.rstrip("/")[:-len("/Contents/Resources")]
    name = f"MacCleaner-{rel['version']}-macos-universal.zip"
    urls = [rel["assets"].get(name), rel["assets"].get(name + ".sha256")]
    if not all(u and u.startswith(DOWNLOAD_PREFIX) for u in urls):
        raise UpdateError(f"release {rel['tag']} chưa có gói {name} kèm .sha256 — tải tay ở {rel['url']}")
    if not os.access(os.path.dirname(app), os.W_OK):
        raise UpdateError(f"không có quyền ghi vào {os.path.dirname(app)} — kéo bản mới vào đó bằng Finder ({rel['url']})")
    steps = [f"tải {name} + .sha256", "kiểm SHA-256, bundle id, phiên bản, chữ ký mã",
             f"chuyển {app} vào Thùng rác", "đặt bản mới vào chỗ cũ"]
    if not yes:
        return {"status": "preview", "kind": "app", "version": rel["version"], "steps": steps, "restart": False}
    tmp = tempfile.mkdtemp(prefix="maccleaner-update-")
    try:
        log(f"Tải {name}…")
        blob = _get(env, urls[0], MAX_ZIP)
        want = _get(env, urls[1], 4096).decode("ascii", "replace").split()
        if not want or hashlib.sha256(blob).hexdigest() != want[0].lower():
            raise UpdateError("SHA-256 của gói tải về không khớp file .sha256 — huỷ, app cũ còn nguyên")
        zpath = os.path.join(tmp, name)
        with open(zpath, "wb") as f:
            f.write(blob)
        rc, out = env.call([DITTO, "-x", "-k", zpath, os.path.join(tmp, "x")], timeout=120)
        new = os.path.join(tmp, "x", "MacCleaner.app")
        info = S.plist(os.path.join(new, "Contents", "Info.plist"))
        if rc != 0 or info.get("CFBundleIdentifier") != BUNDLE_ID or info.get("CFBundleShortVersionString") != rel["version"]:
            raise UpdateError(f"gói tải về không phải Mac Cleaner {rel['version']} — huỷ, app cũ còn nguyên")
        rc, out = env.call([CODESIGN, "--verify", "--deep", "--strict", new], timeout=60)
        if rc != 0:
            raise UpdateError(f"chữ ký mã của bản tải về không hợp lệ — huỷ, app cũ còn nguyên: {out[-200:]}")
        log(f"Chuyển bản cũ vào Thùng rác: {app}")
        err = env.trash(app)
        if err:
            raise UpdateError(f"không chuyển được app cũ vào Thùng rác ({err}) — huỷ")
        try:
            shutil.move(new, app)
        except OSError as e:
            raise UpdateError(f"không đặt được bản mới vào {app} ({e}) — bản cũ đang ở Thùng rác, dùng Đưa trở lại") from None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"status": "ok", "kind": "app", "version": rel["version"], "steps": steps, "restart": True}


def apply(env, rel, yes=False, log=print):
    kind = install_kind(env.root)
    if kind == "source":
        return _apply_source(env, rel, yes, log)
    if kind == "app":
        return _apply_app(env, rel, yes, log)
    raise UpdateError(f"bản cài này không phải git clone hay .app nên không tự cập nhật được — tải bản mới ở {rel['url']}")


# ---------- schedule ----------
def parse_at(text):
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", str(text or ""))
    if not m:
        raise UpdateError(f"giờ phải dạng HH:MM (00:00–23:59), nhận {text!r}")
    return int(m.group(1)), int(m.group(2))


def build_agent(env, every, at):
    """LaunchAgent plist (dict): run `self-update run` daily at HH:MM, or weekly on Monday at HH:MM."""
    if every not in EVERY:
        raise UpdateError(f"tần suất phải là một trong {EVERY}, nhận {every!r}")
    hour, minute = parse_at(at)
    when = {"Hour": hour, "Minute": minute}
    if every == "weekly":
        when["Weekday"] = 1
    log = os.path.join(env.data, "logs", "self-update.log")
    return {"Label": LABEL, "ProcessType": "Background", "StartCalendarInterval": when,
            "ProgramArguments": ["/usr/bin/env", "python3", os.path.join(env.root, "maccleaner.py"), NAME, "run"],
            "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                                     "MACCLEANER_DATA": env.data},
            "StandardOutPath": log, "StandardErrorPath": log}


def auto_on(env, every=None, at=None, install=None, yes=False):
    cfg = load_config(env)
    every, at = every or cfg["every"], at or cfg["at"]
    install = cfg["install"] if install is None else bool(install)
    agent, path = build_agent(env, every, at), agent_path(env)
    plan = {"every": every, "at": at, "install": install, "agent": path, "consent": CONSENT}
    if not yes:
        return {"status": "preview", **plan}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    os.makedirs(os.path.join(env.data, "logs"), exist_ok=True)
    with open(path, "wb") as f:
        plistlib.dump(agent, f)
    domain = f"gui/{os.getuid()}"
    env.call([LAUNCHCTL, "bootout", f"{domain}/{LABEL}"], timeout=30)  # reload when the schedule changed; absent → ignored
    rc, out = env.call([LAUNCHCTL, "bootstrap", domain, path], timeout=30)
    if rc != 0:
        os.remove(path)
        raise UpdateError(f"launchd không nhận lịch (mã {rc}): {out[-200:]}")
    cfg.update(auto=True, every=every, at=at, install=install, consent_at=int(time.time()))
    save_config(env, cfg)
    return {"status": "ok", **plan}


def auto_off(env):
    env.call([LAUNCHCTL, "bootout", f"gui/{os.getuid()}/{LABEL}"], timeout=30)
    path = agent_path(env)
    if os.path.exists(path):
        os.remove(path)
    cfg = load_config(env)
    cfg["auto"] = False
    cfg.pop("consent_at", None)
    save_config(env, cfg)
    return {"status": "ok", "agent": path}


def notify(env, text):
    env.call([OSASCRIPT, "-e", f"display notification {json.dumps(text, ensure_ascii=False)} with title \"Mac Cleaner\""],
             timeout=15)


def run(env, log=print):
    """Scheduled run. Does nothing (and no request) unless the user turned the schedule on."""
    cfg = load_config(env)
    if not cfg["auto"]:
        log("Tự kiểm tra đang tắt — không làm gì.")
        return 0
    last, rel = check(env)
    if last.get("error"):
        log(f"Kiểm tra lỗi: {last['error']}")
        return 1
    if not last["update"]:
        log(f"Đang ở bản mới nhất ({last['current']}).")
        return 0
    version = last["latest"]
    if not cfg["install"]:
        log(f"Có bản mới {version} — chỉ báo (chưa bật tự cài).")
        notify(env, f"Có bản mới {version}. Mở Mac Cleaner → Cập nhật, hoặc chạy: mac-cleaner self-update apply --yes")
        return 0
    if install_kind(env.root) == "app" and BUNDLE_ID in env.running():
        log(f"Có bản mới {version} nhưng app đang mở — để lần sau.")
        notify(env, f"Có bản mới {version}. Bấm Cập nhật trong Mac Cleaner, hoặc đóng app để lần kiểm tra tới tự cài.")
        return 0
    try:
        apply(env, rel, yes=True, log=log)
    except UpdateError as e:
        log(f"Cập nhật {version} lỗi: {e}")
        notify(env, f"Cập nhật {version} lỗi: {e}")
        return 1
    log(f"Đã cập nhật lên {version}.")
    notify(env, f"Đã cập nhật lên {version}.")
    return 0


# ---------- CLI ----------
def _print_status(st):
    kinds = {"source": "git clone (cập nhật bằng git + install.sh)", "app": "MacCleaner.app (cập nhật bằng gói release)",
             "other": "khác (không tự cập nhật)"}
    print(f"Phiên bản : {st['version']} · {kinds[st['kind']]}")
    sched = f"{'hằng ngày' if st['every'] == 'daily' else 'thứ Hai hằng tuần'} lúc {st['at']}"
    print(f"Tự kiểm tra: {'BẬT · ' + sched + (' · tự cài' if st['install'] else ' · chỉ báo') if st['auto'] else 'tắt'}")
    last = st.get("last") or {}
    if last:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(last.get("checked_at", 0)))
        print(f"Lần kiểm gần nhất: {when} · "
              + (f"lỗi: {last['error']}" if last.get("error") else
                 f"có bản mới {last['latest']} ({last['url']})" if last.get("update") else f"mới nhất là {last.get('latest')}"))


def cli(args, env=None):
    env = env or Env()
    as_json = getattr(args, "json", False)
    log = (lambda m: print(m, file=sys.stderr, flush=True)) if as_json else print
    try:
        if args.action == "status":
            res = status(env)
            if not as_json:
                _print_status(res)
        elif args.action == "check":
            res, _ = check(env)
            if res.get("error"):
                raise UpdateError(res["error"])
            if not as_json:
                print(f"Có bản mới {res['latest']} (đang dùng {res['current']}): {res['url']}\n"
                      f"Cập nhật: self-update apply --yes" if res["update"] else f"Đang ở bản mới nhất ({res['current']}).")
        elif args.action == "apply":
            last, rel = check(env)
            if last.get("error"):
                raise UpdateError(last["error"])
            if not last["update"]:
                res = {"status": "current", "version": last["current"], "restart": False}
                if not as_json:
                    print(f"Đang ở bản mới nhất ({last['current']}) — không có gì để cập nhật.")
            else:
                res = apply(env, rel, yes=args.yes, log=log)
                if not as_json:
                    head = f"Đã cập nhật lên {res['version']}" if args.yes else f"Xem trước cập nhật {last['current']} → {res['version']}"
                    print(head + ":\n" + "\n".join(f"  {i}. {s}" for i, s in enumerate(res["steps"], 1))
                          + ("" if args.yes else "\nThêm --yes để làm thật."))
        elif args.action == "auto" and args.state == "on":
            res = auto_on(env, args.every, args.at, args.install, yes=args.yes)
            if not as_json:
                print(CONSENT)
                sched = f"{'hằng ngày' if res['every'] == 'daily' else 'thứ Hai hằng tuần'} lúc {res['at']}"
                print(f"{'Đã bật' if args.yes else 'Sẽ bật'}: kiểm tra {sched}, {'tự cài bản mới' if res['install'] else 'chỉ báo khi có bản mới'}"
                      f"\nLaunchAgent: {res['agent']}" + ("" if args.yes else "\nChưa làm gì. Đồng ý thì chạy lại kèm --yes."))
        elif args.action == "auto":
            res = auto_off(env)
            if not as_json:
                print(f"Đã tắt tự kiểm tra và gỡ {res['agent']}.")
        else:
            return run(env, log=log)
    except UpdateError as e:
        print(f"Lỗi: {e}", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(res, ensure_ascii=False))
    return 0


def register(subparsers):
    p = subparsers.add_parser(NAME, help="cập nhật mac-cleaner: kiểm tra bản mới, cập nhật tay, tự kiểm tra theo lịch (cần đồng ý)")
    sp = p.add_subparsers(dest="action", required=True)
    for name, text in (("status", "phiên bản, kiểu cài, lịch tự kiểm tra (không dùng mạng)"),
                       ("check", "hỏi GitHub bản mới nhất"), ("run", "lần chạy theo lịch (LaunchAgent gọi)")):
        sp.add_parser(name, help=text).add_argument("--json", action="store_true")
    q = sp.add_parser("apply", help="cài bản mới nhất (mặc định chỉ xem trước)")
    q.add_argument("--yes", action="store_true", help="làm thật")
    q.add_argument("--json", action="store_true")
    q = sp.add_parser("auto", help="bật/tắt tự kiểm tra theo lịch")
    q.add_argument("state", choices=("on", "off"))
    q.add_argument("--every", choices=EVERY, help="daily (mặc định) hoặc weekly (thứ Hai)")
    q.add_argument("--at", help="giờ chạy HH:MM (mặc định 09:00)")
    q.add_argument("--install", action=argparse.BooleanOptionalAction,
                   help="tự cài khi có bản mới; --no-install: chỉ báo (mặc định: giữ lựa chọn cũ, ban đầu là chỉ báo)")
    q.add_argument("--yes", action="store_true", help="đồng ý cho tự kết nối mạng theo lịch (không có: chỉ giải thích)")
    q.add_argument("--json", action="store_true")
    p.set_defaults(func=cli)
