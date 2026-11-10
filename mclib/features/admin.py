"""Admin rights the user grants once and can take back: a short allowlist of fixed root commands in /etc/sudoers.d.

  python3 maccleaner.py admin list [--json]
  python3 maccleaner.py admin grant <id>…|all [--yes] [--json]     one macOS admin dialog (preview without --yes)
  python3 maccleaner.py admin revoke <id>…|all [--yes] [--json]    one macOS admin dialog

Why: `sfltool dumpbtm` (login items) shows a password dialog on every run, and a few maintenance tasks need root.
After a grant, mac-cleaner runs exactly that command through `sudo -n`: no dialog, no password typed again.

What a grant is: one line per command in /etc/sudoers.d/mac-cleaner (root:wheel 0440, checked by visudo before it is
installed), for the current user only, NOPASSWD, with the whole argument list fixed. Only the commands in GRANTS can be
granted: Apple binaries protected by SIP, no argument the caller can choose, read-only or harmless to repeat.
What it is not: no password is stored anywhere. An admin password kept in the Keychain with "Always Allow" can be read
by every process of this user, which is root for everything instead of root for five fixed commands.
Not grantable on purpose: `tmutil deletelocalsnapshots <date>` (free argument, cannot be undone) and `periodic`
(runs scripts from directories a user may own).
"""
import json
import os
import pwd
import re
import shlex
import subprocess
import sys

NAME = "admin"
SUDO, OSASCRIPT, VISUDO = "/usr/bin/sudo", "/usr/bin/osascript", "/usr/sbin/visudo"
SUDOERS = "/private/etc/sudoers.d/mac-cleaner"
HEADER = "# mac-cleaner: commands this user may run as root without a password. Managed by `mac-cleaner admin grant|revoke`."
PROMPT = "Mac Cleaner cần quyền quản trị MỘT lần để cập nhật danh sách lệnh được chạy không hỏi mật khẩu."
EXPLAIN = ("Cấp quyền = ghi một dòng cho mỗi lệnh vào /etc/sudoers.d/mac-cleaner (chỉ user này, đúng lệnh và tham số bên dưới, "
           "không lệnh nào khác). Không lưu mật khẩu ở đâu cả. Thu hồi bất cứ lúc nào bằng `admin revoke`.")
# id of a maintenance grant = its task name in maintain.TASKS (tests keep the two command lists equal).
GRANTS = (
    dict(id="btm-read", title="Đọc login item và mục chạy nền mà không hỏi mật khẩu mỗi lần quét", effect="chỉ đọc",
         commands=[["/usr/bin/sfltool", "dumpbtm"]]),
    dict(id="dns-flush", title="Xoá cache DNS", effect="vô hại, tự tạo lại",
         commands=[["/usr/bin/dscacheutil", "-flushcache"], ["/usr/bin/killall", "-HUP", "mDNSResponder"]]),
    dict(id="purge-ram", title="Giải phóng bộ nhớ đệm đĩa trong RAM", effect="vô hại", commands=[["/usr/sbin/purge"]]),
    dict(id="spotlight-reindex", title="Đánh chỉ mục lại Spotlight cho ổ hệ thống", effect="Spotlight chậm vài giờ",
         commands=[["/usr/bin/mdutil", "-E", "/"]]),
    dict(id="font-cache-system", title="Xoá cache font của hệ thống và user", effect="cần khởi động lại máy",
         commands=[["/usr/bin/atsutil", "databases", "-remove"]]),
)
IDS = tuple(g["id"] for g in GRANTS)


class AdminError(Exception):
    """A failure the user should read (message is Vietnamese, shown as is)."""


def _call(cmd, timeout=60):
    """Run a command by absolute path → (exit code, stdout+stderr). Never raises."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return 1, f"{type(e).__name__}: {e}"


def rule(cmd):
    """One command as sudoers writes it. `""` after a bare path means "no arguments at all"."""
    return " ".join(cmd) if len(cmd) > 1 else cmd[0] + ' ""'


def user_name():
    name = pwd.getpwuid(os.getuid()).pw_name
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", name):  # the name is written into sudoers
        raise AdminError(f"tên user {name!r} có ký tự không ghi được vào sudoers")
    return name


def sudoers_lines(ids, user):
    return [HEADER] + [f"{user} ALL=(root) NOPASSWD: {rule(c)}" for g in GRANTS if g["id"] in ids for c in g["commands"]]


def installed(path=SUDOERS):
    """True once any grant exists. Callers use it so a machine without grants never even starts sudo."""
    return os.path.exists(path)


def granted(call=_call, path=SUDOERS):
    """Ids sudo itself lists as NOPASSWD for this user (the sudoers file is root-only, sudo is the source of truth)."""
    if not installed(path):
        return set()
    rc, out = call([SUDO, "-n", "-l"], timeout=15)
    if rc != 0:
        return set()
    rules = set()
    for line in out.splitlines():
        if "NOPASSWD:" in line:
            rules.update(" ".join(r.split()) for r in line.split("NOPASSWD:", 1)[1].split(","))
    return {g["id"] for g in GRANTS if all(rule(c) in rules for c in g["commands"])}


def listing(call=_call, path=SUDOERS):
    have = granted(call, path)
    return [dict(id=g["id"], title=g["title"], effect=g["effect"], commands=[" ".join(c) for c in g["commands"]],
                 granted=g["id"] in have) for g in GRANTS]


def root_script(lines, path=SUDOERS):
    """The only thing that runs as root: write the lines to a root-owned temp file, let visudo check it, install it.
    No grant left → remove the file. The text never passes through a file this user could swap."""
    if len(lines) < 2:
        return f"/bin/rm -f {shlex.quote(path)}"
    body = " ".join(shlex.quote(line) for line in lines)
    return (f"t=$(/usr/bin/mktemp /private/tmp/mac-cleaner-sudoers.XXXXXX) || exit 1; printf '%s\\n' {body} > \"$t\" "
            f"&& {VISUDO} -cf \"$t\" >/dev/null && /usr/bin/install -m 0440 -o root -g wheel \"$t\" {shlex.quote(path)}; "
            f"rc=$?; /bin/rm -f \"$t\"; exit $rc")


def change(action, ids, yes=False, call=_call, path=SUDOERS):
    """grant/revoke → new state. Without yes: only the plan. With yes: one macOS admin dialog (osascript)."""
    ids = set(IDS) if "all" in ids else set(ids)
    unknown = sorted(ids - set(IDS))
    if unknown or not ids:
        raise AdminError(f"không có quyền {', '.join(unknown) or '(trống)'} — chỉ: {', '.join(IDS)}, all")
    now = granted(call, path)
    want = now | ids if action == "grant" else now - ids
    lines = sudoers_lines(want, user_name())
    plan = dict(action=action, ids=sorted(ids), granted=sorted(want), file=path, lines=lines[1:], explain=EXPLAIN)
    if not yes:
        return dict(status="preview", **plan)
    if want != now:
        rc, out = call([OSASCRIPT, "-e", f"do shell script {json.dumps(root_script(lines, path))} "
                                         f"with prompt {json.dumps(PROMPT, ensure_ascii=False)} with administrator privileges"],
                       timeout=300)
        if rc != 0:
            raise AdminError("đã huỷ ở hộp thoại quản trị — chưa đổi gì" if "-128" in out
                             else f"không ghi được {path}: {out[-300:]}")
        after = granted(call, path)
        if after != want:
            raise AdminError(f"đã ghi {path} nhưng `sudo -n -l` chưa liệt kê đúng ({', '.join(sorted(after)) or 'không có quyền nào'}) "
                             f"— kiểm bằng `sudo -l`")
    return dict(status="ok", **plan)


def cli(args):
    try:
        if args.action == "list":
            res = listing()
            if not args.json:
                print(EXPLAIN + "\n")
                for g in res:
                    print(f"{g['id']:18} [{'ĐÃ CẤP' if g['granted'] else 'chưa cấp'}] {g['title']} ({g['effect']})")
                    for c in g["commands"]:
                        print(f"{'':18}   sudo {c}")
                print("\nCấp: python3 maccleaner.py admin grant <id>… --yes   ·   Thu hồi: admin revoke <id>…|all --yes")
        else:
            res = change(args.action, args.ids, yes=args.yes)
            if not args.json:
                verb = {"grant": "cấp", "revoke": "thu hồi"}[args.action]
                print(EXPLAIN + "\n")
                print(f"{'Đã' if args.yes else 'Sẽ'} {verb}: {', '.join(res['ids'])}\n{res['file']} "
                      + ("gồm:\n" + "\n".join("  " + line for line in res["lines"]) if res["lines"] else "bị xoá (không còn quyền nào)."))
                if not args.yes:
                    print("Chưa làm gì. Đồng ý thì chạy lại kèm --yes (macOS hỏi mật khẩu quản trị một lần).")
    except AdminError as e:
        print(f"Lỗi: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(res, ensure_ascii=False))
    return 0


def register(subparsers):
    p = subparsers.add_parser(NAME, help="quyền quản trị cấp một lần cho vài lệnh cố định (sfltool, bảo trì) — xem, cấp, thu hồi")
    sp = p.add_subparsers(dest="action", required=True)
    sp.add_parser("list", help="các quyền cấp được và trạng thái").add_argument("--json", action="store_true")
    for name, text in (("grant", "cấp quyền (mặc định chỉ xem trước)"), ("revoke", "thu hồi quyền")):
        q = sp.add_parser(name, help=text)
        q.add_argument("ids", nargs="+", metavar="id", help=f"{', '.join(IDS)} hoặc all")
        q.add_argument("--yes", action="store_true", help="làm thật: macOS hỏi mật khẩu quản trị một lần")
        q.add_argument("--json", action="store_true")
    p.set_defaults(func=cli)
