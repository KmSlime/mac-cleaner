"""Tác vụ bảo trì cấp user (thay `mo optimize`). Không bao giờ gọi sudo.

  python3 maccleaner.py maintain list [--json]
  python3 maccleaner.py maintain run <task> [--yes]      không có --yes: chỉ in lệnh sẽ chạy
  scan → report["features"]["maintain"] = {"summary": {"maintenance": [{task, needsRoot, runnable, available, command…}]}}

Mỗi tác vụ:
  needsRoot  cần quyền admin → mac-cleaner chỉ in lệnh `sudo …` để người dùng tự chạy;
  runnable   không cần root VÀ vô hại (tạo lại được, không mất dữ liệu) → `run --yes` chạy được;
             tác vụ không cần root nhưng có hệ quả (font, Homebrew) chỉ in lệnh;
  available  lệnh và đúng tuỳ chọn có trên máy này (dò bằng help của chính lệnh — macOS đổi tuỳ chọn giữa các bản).
"""
import json
import os
import shlex
import subprocess


NAME = "maintain"
LSREGISTER = ("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework"
              "/Support/lsregister")

# probe: (lệnh in help, chuỗi phải có trong output) — None: chỉ cần file lệnh tồn tại
TASKS = (
    dict(task="quicklook-cache", title="Làm mới cache hình thu nhỏ Quick Look", needsRoot=False, runnable=True,
         commands=[["/usr/bin/qlmanage", "-r", "cache"], ["/usr/bin/qlmanage", "-r"]],
         probe=(["/usr/bin/qlmanage", "-h"], "-r cache"),
         note="xoá cache thumbnail và nạp lại trình tạo Quick Look; Finder tự tạo lại thumbnail"),
    dict(task="launchservices", title="Dựng lại cơ sở LaunchServices của user (menu Mở bằng, biểu tượng trùng)",
         needsRoot=False, runnable=True,
         commands=[[LSREGISTER, "-gc"], [LSREGISTER, "-f", "-all", "u,s,l"]],
         probe=([LSREGISTER, "-h"], "-gc"),
         note="dọn dữ liệu cũ rồi đăng ký lại app; chạy vài phút, không xoá cơ sở dữ liệu (không dùng -delete)"),
    dict(task="tm-snapshots", title="Liệt kê snapshot Time Machine local (dung lượng purgeable)", needsRoot=False,
         runnable=True, commands=[["/usr/bin/tmutil", "listlocalsnapshots", "/"]],
         probe=(["/usr/bin/tmutil", "help"], "listlocalsnapshots"), note="chỉ đọc"),
    dict(task="font-cache-user", title="Xoá cache font của user", needsRoot=False, runnable=False,
         commands=[["/usr/bin/atsutil", "databases", "-removeUser"]],
         probe=(["/usr/bin/atsutil", "databases"], "-removeUser"),
         note="cần đăng xuất hoặc khởi động lại; trước đó app đang mở có thể hiển thị font sai — tự chạy khi cần"),
    dict(task="brew-cleanup", title="Dọn bản cũ và cache tải về của Homebrew", needsRoot=False, runnable=False,
         commands=[["brew", "cleanup", "--dry-run"], ["brew", "cleanup"]], probe=None, binary="brew",
         note="xoá hẳn (không qua Thùng rác) — xem --dry-run trước rồi tự chạy"),
    dict(task="tm-snapshots-delete", title="Xoá snapshot Time Machine local", needsRoot=True, runnable=False,
         commands=[["sudo", "/usr/bin/tmutil", "deletelocalsnapshots", "<ngày>"]], probe=None,
         binary="/usr/bin/tmutil",
         note="không hoàn tác được; lấy <ngày> từ `tm-snapshots`. Có thể chạy được không sudo trên vài bản macOS (chưa kiểm)"),
    dict(task="dns-flush", title="Xoá cache DNS", needsRoot=True, runnable=False,
         commands=[["sudo", "/usr/bin/dscacheutil", "-flushcache"], ["sudo", "/usr/bin/killall", "-HUP", "mDNSResponder"]],
         probe=None, binary="/usr/bin/dscacheutil", note=""),
    dict(task="purge-ram", title="Giải phóng bộ nhớ đệm đĩa trong RAM", needsRoot=True, runnable=False,
         commands=[["sudo", "/usr/sbin/purge"]], probe=None, binary="/usr/sbin/purge",
         note="lợi ích ngắn hạn; macOS tự quản lý bộ nhớ"),
    dict(task="spotlight-reindex", title="Đánh chỉ mục lại Spotlight cho ổ hệ thống", needsRoot=True, runnable=False,
         commands=[["sudo", "/usr/bin/mdutil", "-E", "/"]], probe=None, binary="/usr/bin/mdutil",
         note="Spotlight chậm vài giờ trong lúc index lại"),
    dict(task="font-cache-system", title="Xoá cache font của hệ thống và user", needsRoot=True, runnable=False,
         commands=[["sudo", "/usr/bin/atsutil", "databases", "-remove"]], probe=None, binary="/usr/bin/atsutil",
         note="khởi động lại máy sau đó"),
    dict(task="periodic", title="Chạy script bảo trì định kỳ", needsRoot=True, runnable=False,
         commands=[["sudo", "/usr/sbin/periodic", "daily", "weekly", "monthly"]], probe=None,
         binary="/usr/sbin/periodic", note="đã bị bỏ trên các bản macOS mới"),
)


def _which(name):
    if os.path.isabs(name):
        return name if os.access(name, os.X_OK) else None
    for d in ("/opt/homebrew/bin", "/usr/local/bin"):
        p = os.path.join(d, name)
        if os.access(p, os.X_OK):
            return p
    return None


def _binary(t):
    return t.get("binary") or t["commands"][0][0]


def _help(cmd, timeout=20):
    """stdout + stderr của lệnh help (một số lệnh in help ra stderr, thoát mã khác 0)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return ""


def available(t, run=_help, which=_which):
    """(có dùng được, lý do nếu không). Dò help của chính lệnh để biết tuỳ chọn còn tồn tại không."""
    if not which(_binary(t)):
        return False, f"không có {_binary(t)} trên máy này"
    if t.get("probe"):
        cmd, needle = t["probe"]
        if needle not in run(cmd, timeout=20):
            return False, f"`{' '.join(cmd)}` không còn tuỳ chọn {needle!r} trên bản macOS này"
    return True, ""


def shell(t):
    return " && ".join(shlex.join(c) for c in t["commands"])


def tasks(run=_help, which=_which):
    out = []
    for t in TASKS:
        ok, why = available(t, run, which)
        out.append(dict(task=t["task"], title=t["title"], needsRoot=t["needsRoot"],
                        runnable=bool(t["runnable"] and ok), available=ok, command=shell(t), note=t["note"],
                        **({"unavailable": why} if why else {})))
    return out


def find(task):
    return next((t for t in TASKS if t["task"] == task), None)


def run_task(name, yes=False, log=print, runner=subprocess.run, run=_help, which=_which):
    """Chạy một tác vụ runnable. yes=False: chỉ in lệnh. Trả mã thoát (0 ok)."""
    t = find(name)
    if not t:
        log(f"không có tác vụ {name!r} — xem `maintain list`")
        return 2
    if t["needsRoot"]:
        log(f"{t['title']}: cần quyền admin — mac-cleaner không chạy sudo. Tự chạy trong Terminal:\n  {shell(t)}")
        return 1
    ok, why = available(t, run, which)
    if not ok:
        log(f"{t['title']}: {why}")
        return 1
    if not t["runnable"]:
        log(f"{t['title']}: {t['note']}. Tự chạy khi đã hiểu hệ quả:\n  {shell(t)}")
        return 1
    if not yes:
        log(f"Xem trước — sẽ chạy ({t['note']}):\n  {shell(t)}\nThêm --yes để chạy.")
        return 0
    rc = 0
    for cmd in t["commands"]:
        log(f"$ {shlex.join(cmd)}")
        try:
            r = runner(cmd, capture_output=True, text=True, timeout=900)
        except (OSError, subprocess.SubprocessError) as e:
            log(f"  lỗi: {e}")
            return 1
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        if out:
            log("  " + out[-2000:].replace("\n", "\n  "))
        if r.returncode:
            log(f"  thoát mã {r.returncode}")
            rc = r.returncode
    return rc


def collect(ctx):
    return {"summary": {"maintenance": tasks()}, "items": []}


def cli(args):
    if args.action == "run":
        if not args.task:
            print("thiếu tên tác vụ: maintain run <task> [--yes]")
            return 2
        return run_task(args.task, args.yes)
    lst = tasks()
    if args.json:
        print(json.dumps(lst, ensure_ascii=False))
        return 0
    for t in lst:
        mark = "chạy được" if t["runnable"] else "cần sudo" if t["needsRoot"] else "tự chạy" if t["available"] else "không có"
        print(f"{t['task']:20} [{mark}] {t['title']}")
        print(f"{'':20}   {t['command']}" + (f"  — {t['unavailable']}" if t.get("unavailable") else ""))
    print("\nChạy: python3 maccleaner.py maintain run <task> --yes  (chỉ tác vụ 'chạy được'; tác vụ cần sudo thì tự chạy lệnh in ra)")
    return 0


def register(subparsers):
    p = subparsers.add_parser(NAME, help="tác vụ bảo trì cấp user (QuickLook, LaunchServices…); tác vụ cần sudo chỉ in lệnh")
    p.add_argument("action", choices=("list", "run"), nargs="?", default="list")
    p.add_argument("task", nargs="?")
    p.add_argument("--yes", action="store_true", help="thực thi (không có: chỉ in lệnh)")
    p.add_argument("--json", action="store_true", help="in JSON (list)")
    p.set_defaults(func=cli)
