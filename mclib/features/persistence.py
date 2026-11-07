"""Kiểm kê cơ chế bám trụ + chữ ký mã (`audit`). KHÔNG phải trình diệt virus: chỉ liệt kê và gắn cờ để bạn xem lại.

  python3 maccleaner.py audit [--gatekeeper] [--json]
  scan → report["features"]["audit"] = {"summary": {...}, "items": [...]}   (mọi mục list-only)

Nguồn: LaunchAgents/LaunchDaemons, login item + background item (từ `startup`), cron của user (`crontab -l`), file
thực thi ẩn trong ~/Library/Application Support/*/. Extension trình duyệt: đã có ở nhóm `extensions` của scan chính.
Chữ ký (mặc định offline): `codesign -dv --verbose=2`, vé notarize đính kèm (Contents/CodeResources), cờ quarantine
`xattr -p com.apple.quarantine`. `audit --gatekeeper` thêm `codesign --verify --test-requirement==notarized` và
`spctl -a -vv` — chậm, và Gatekeeper của macOS có thể tự tra notarization với Apple, nên phải bật tay; `scan` không
bật. Không xoá, không sửa; mã này không gửi gì ra mạng.
Cờ: unsigned · adhoc · not-notarized · gatekeeper-rejected · temp-location · hidden-location · hidden-executable ·
missing-program.
"""
import getpass
import json
import os
import re
import stat

from mclib import registry, safety as S
from mclib.features import startup

NAME = "audit"
CODESIGN = "/usr/bin/codesign"
SPCTL = "/usr/sbin/spctl"
XATTR = "/usr/bin/xattr"
CRONTAB = "/usr/bin/crontab"
CRON_TABS = "/private/var/at/tabs"   # nơi cron lưu crontab của từng user (chỉ root đọc được)
TEMP_PREFIXES = ("/tmp/", "/private/tmp/", "/var/tmp/", "/private/var/tmp/", "/var/folders/", "/private/var/folders/")
APPLE_TREES = ("/System/", "/usr/", "/bin/", "/sbin/", "/Library/Apple/")  # SIP: Apple ký, không cần kiểm
INTERPRETERS = {"sh", "bash", "zsh", "dash", "ksh", "csh", "tcsh", "fish", "python", "python3", "perl", "ruby",
                "node", "osascript", "php", "env"}
HIDDEN_SCAN_DEPTH = 2         # Application Support/<thư mục>/<…>/<file>
HIDDEN_SCAN_LIMIT = 5000      # số mục tối đa duyệt trong mỗi thư mục con của Application Support
ADVICE = ("Đây là kiểm kê, không phải quét virus: cờ chỉ là dấu hiệu để xem lại. macOS đã có Gatekeeper và XProtect; "
          "nghi ngờ thật thì dùng phần mềm bảo mật chuyên dụng. Không tự xoá mục ở đây.")

run_cmd = startup.run_cmd


# ---------- parse ----------
def parse_codesign(text):
    """stderr của `codesign -dv --verbose=2` → {signed, adhoc, team, authorities[], apple, error}."""
    t = text or ""
    out = dict(signed=None, adhoc=False, team=None, authorities=[], apple=False, error=None)
    if "code object is not signed at all" in t:
        out["signed"] = False
        return out
    if re.search(r"No such file|cannot read|not permitted|Operation not permitted", t):
        out["error"] = t.strip().splitlines()[-1][:160] if t.strip() else "lỗi"
        return out
    out["authorities"] = re.findall(r"^Authority=(.+)$", t, re.M)
    out["adhoc"] = bool(re.search(r"^Signature=adhoc|flags=0x[0-9a-f]*\(.*adhoc", t, re.M))
    team = re.search(r"^TeamIdentifier=(.+)$", t, re.M)
    out["team"] = None if not team or team.group(1).strip() == "not set" else team.group(1).strip()
    if out["adhoc"] or out["authorities"] or re.search(r"^CodeDirectory ", t, re.M):
        out["signed"] = True
    auth = " ".join(out["authorities"])
    out["apple"] = bool(re.search(r"^Platform identifier=", t, re.M)) or (
        "Apple" in auth and "Developer ID" not in auth and not out["team"])
    return out


def parse_spctl(rc, text):
    """`spctl -a -vv` → {accepted: True|False|None, source}."""
    t = text or ""
    src = re.search(r"^source=(.+)$", t, re.M)
    accepted = True if re.search(r": accepted\b", t) else False if re.search(r": rejected\b", t) else None
    if accepted is None and rc is not None and t.strip():
        accepted = rc == 0
    return dict(accepted=accepted, source=src.group(1).strip() if src else "")


def parse_crontab(text):
    """`crontab -l` → [{"schedule", "command", "program"}] (bỏ dòng chú thích, dòng gán biến)."""
    jobs = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*=", s):
            continue
        parts = s.split()
        n = 1 if parts[0].startswith("@") else 5
        if len(parts) <= n:
            continue
        cmd = parts[n:]
        prog = cmd[0] if cmd[0].startswith(("/", "~/")) else None
        if prog and os.path.basename(prog) in INTERPRETERS or cmd[0] in INTERPRETERS:
            prog = next((c for c in cmd[1:] if c.startswith(("/", "~/"))), prog)
        prog = os.path.expanduser(prog) if prog else None
        jobs.append(dict(schedule=" ".join(parts[:n]), command=" ".join(cmd), program=prog))
    return jobs


def script_target(program, args):
    """Chương trình là trình thông dịch (sh, python…) → đường dẫn script đầu tiên trong đối số; không thì program."""
    if program and os.path.basename(program) in INTERPRETERS:
        for a in args[1:]:
            if isinstance(a, str) and a.startswith(("/", "~/")):
                return os.path.expanduser(a)
    return program


def location_flags(path):
    flags = []
    if not path:
        return flags
    if path.startswith(TEMP_PREFIXES):
        flags.append("temp-location")
    parts = [p for p in path.split("/") if p]
    if any(p.startswith(".") and p not in (".", "..") for p in parts[:-1]):
        flags.append("hidden-location")
    return flags


# ---------- kiểm chữ ký ----------
def app_bundle(path):
    m = re.match(r"^(.*?\.app)(/|$)", path or "")
    return m.group(1) if m else None


def stapled(bundle):
    """App có vé notarize đính kèm (stapled) không — kiểm offline bằng file Contents/CodeResources."""
    return bool(bundle) and os.path.isfile(os.path.join(bundle, "Contents", "CodeResources"))


def check_code(path, run=run_cmd, gatekeeper=False):
    """Chữ ký + notarize + quarantine của một file; gatekeeper=True thêm `codesign --verify -R=notarized` và
    `spctl -a` (chậm; macOS có thể tự tra notarization với Apple). Không tồn tại → missing."""
    res = dict(signed=None, adhoc=False, notarized=None, gatekeeper=None, gatekeeper_source="", team=None,
               apple=False, quarantine=None, missing=False, error=None)
    if not path or not os.path.lexists(path):
        res["missing"] = True
        return res
    if path.startswith(APPLE_TREES) and not path.startswith("/usr/local/"):
        res.update(signed=True, apple=True)        # vùng SIP: hệ thống ký, không cần gọi codesign
        return res
    rc, out, err = run([CODESIGN, "-dv", "--verbose=2", path])
    cs = parse_codesign((err or "") + (out or ""))
    res.update(signed=cs["signed"], adhoc=cs["adhoc"], team=cs["team"], apple=cs["apple"], error=cs["error"])
    bundle = app_bundle(path)
    target = bundle or path
    if cs["signed"] and not cs["adhoc"] and not cs["apple"]:
        if stapled(bundle):
            res["notarized"] = True
        if gatekeeper:
            rc, _, _ = run([CODESIGN, "--verify", "--test-requirement==notarized", target])
            if rc is not None and not res["notarized"]:
                res["notarized"] = rc == 0
            rc, out, err = run([SPCTL, "-a", "-vv", "-t", "execute", target])
            sp = parse_spctl(rc, (err or "") + (out or ""))
            res["gatekeeper"], res["gatekeeper_source"] = sp["accepted"], sp["source"]
            src = sp["source"].lower()
            if "notarized" in src and "unnotarized" not in src:
                res["notarized"] = True
            if sp["source"] == "Mac App Store":
                res["notarized"] = None              # App Store: Apple duyệt, không dùng notarize
    rc, _, err = run([XATTR, "-p", "com.apple.quarantine", target])
    res["quarantine"] = True if rc == 0 else False if rc == 1 or "No such xattr" in (err or "") else None
    return res


def code_flags(c):
    flags = []
    if c["missing"]:
        return ["missing-program"]
    if c["signed"] is False:
        flags.append("unsigned")
    if c["adhoc"]:
        flags.append("adhoc")
    if c["signed"] and not c["adhoc"] and not c["apple"]:
        if c["notarized"] is False:
            flags.append("not-notarized")
        if c["gatekeeper"] is False:
            flags.append("gatekeeper-rejected")
    return flags


# ---------- nguồn bám trụ ----------
def hidden_executables(root, depth=HIDDEN_SCAN_DEPTH, limit=HIDDEN_SCAN_LIMIT):
    """File thực thi ẩn (tên hoặc thư mục cha bắt đầu bằng '.') trong root/*/ tới `depth` cấp. Không theo symlink.
    Bỏ qua thư mục của Apple. Trả (danh sách, số thư mục không đọc được)."""
    found, denied = [], 0
    try:
        tops = sorted(os.scandir(root), key=lambda e: e.name)
    except OSError:
        return found, 1
    for top in tops:
        if top.name.startswith("com.apple.") or not top.is_dir(follow_symlinks=False):
            continue
        stack, seen = [(top.path, 1, top.name.startswith("."))], 0
        while stack and seen < limit:
            d, lvl, hidden_parent = stack.pop()
            try:
                ents = list(os.scandir(d))
            except OSError:
                denied += 1
                continue
            for e in ents:
                seen += 1
                hidden = hidden_parent or e.name.startswith(".")
                try:
                    if e.is_dir(follow_symlinks=False):
                        if lvl < depth:
                            stack.append((e.path, lvl + 1, hidden))
                        continue
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                if hidden and stat.S_ISREG(st.st_mode) and st.st_mode & 0o111 and e.name != ".DS_Store":
                    found.append(e.path)
    return found, denied


def gather(home=None, run=run_cmd, startup_data=None, app_support=None, gatekeeper=False):
    """Kiểm kê (chỉ đọc). Trả {"records": [...], "errors": [...], "denied": n, "cron_jobs": n}."""
    home = home or S.HOME
    sd = startup_data if startup_data is not None else startup.gather(home=home, run=run)
    records, errors = [], list(sd.get("errors", []))
    for r in sd["records"]:
        target = script_target(r.get("program"), r.get("args") or [])
        src = f"{r['kind']}:{r['scope']}"
        records.append(dict(source=src, label=r.get("label") or r.get("name") or "", origin=r.get("plist") or r.get("path"),
                            program=target, owner=r.get("owner") or "", skip_code=bool(r.get("refusal"))
                            and (r.get("label") or "").lower().startswith(startup.APPLE_PREFIX)))
    rc, out, err = run([CRONTAB, "-l"])
    jobs = parse_crontab(out) if rc == 0 else []
    if rc not in (0, 1) and rc is not None:
        errors.append(f"crontab -l lỗi ({rc})")
    tab = os.path.join(CRON_TABS, getpass.getuser())
    for j in jobs:
        records.append(dict(source="cron:user", label=j["schedule"], origin=tab, program=j["program"], owner="",
                            skip_code=False, command=j["command"]))
    hidden, denied = hidden_executables(app_support or os.path.join(home, "Library", "Application Support"))
    for p in hidden:
        records.append(dict(source="hidden-executable", label=os.path.basename(p), origin=p, program=p, owner="",
                            skip_code=False, extra_flags=["hidden-executable"]))
    cache = {}
    for r in records:
        p = r["program"]
        if r["skip_code"] or not p:
            r["code"] = None
        else:
            if p not in cache:
                cache[p] = check_code(p, run, gatekeeper)
            r["code"] = cache[p]
        flags = list(r.get("extra_flags", []))
        flags += location_flags(p)
        if r["code"]:
            flags += code_flags(r["code"])
        r["flags"] = list(dict.fromkeys(flags))
    return dict(records=records, errors=errors, denied=denied, cron_jobs=len(jobs), gatekeeper=gatekeeper)


def _path(r):
    p = r["program"]
    return p if p and os.path.isabs(p) else r["origin"]


def to_entry(r):
    path = _path(r)
    c = r["code"] or {}
    flags = r["flags"]
    reason = ("cờ: " + ", ".join(flags) + " — xem lại; " if flags else "") + "chỉ liệt kê"
    return registry.entry(NAME, path, name=r["label"] or os.path.basename(path), safety="review" if flags else "inventory",
                          delete_mode="list-only", reason=reason, kind=r["source"], origin=r["origin"],
                          program=r["program"], owner=r["owner"], signed=c.get("signed"), adhoc=c.get("adhoc"),
                          notarized=c.get("notarized"), apple=c.get("apple"), gatekeeper=c.get("gatekeeper"),
                          gatekeeper_source=c.get("gatekeeper_source"), team=c.get("team"),
                          quarantine=c.get("quarantine"), flags=flags)


def entries(g):
    """Mục cho report. id ổn định theo (tính năng, path): cùng chương trình từ nhiều nguồn → một mục, gộp nguồn + cờ."""
    merged = {}
    for r in g["records"]:
        p = _path(r)
        if not p or not os.path.isabs(p):
            continue
        e = to_entry(r)
        m = merged.get(e["id"])
        if m is None:
            merged[e["id"]] = e
            continue
        m["kind"] = ",".join(dict.fromkeys(m["kind"].split(",") + [e["kind"]]))
        m["flags"] = list(dict.fromkeys(m["flags"] + e["flags"]))
        if m["flags"]:
            m["safety"] = "review"
            m["reason"] = "cờ: " + ", ".join(m["flags"]) + " — xem lại; chỉ liệt kê"
    return list(merged.values())


def summarize(g, items, browser_extensions=None):
    flags = {}
    for e in items:
        for f in e["flags"]:
            flags[f] = flags.get(f, 0) + 1
    kinds = {}
    for e in items:
        for k in e["kind"].split(","):
            kinds[k] = kinds.get(k, 0) + 1
    s = dict(total=len(items), flagged=sum(1 for e in items if e["flags"]), flags=flags, by_source=kinds,
             cron_jobs=g["cron_jobs"], unreadable_dirs=g["denied"], errors=g["errors"], advice=ADVICE,
             gatekeeper_checked=bool(g.get("gatekeeper")),
             notarized_unknown=sum(1 for e in items if e["signed"] and not e["adhoc"] and not e["apple"]
                                   and e["notarized"] is None))
    s["browser_extensions"] = ({"count": browser_extensions, "note": "xem nhóm 'extensions' (browser) của scan chính"}
                               if browser_extensions is not None else
                               {"count": None, "note": "chạy `scan` để kiểm kê extension trình duyệt"})
    return s


def collect(ctx):
    g = gather(home=ctx.home)
    items = entries(g)
    nb = sum(1 for i in ctx.items if i.get("category") == "extensions" and i.get("kind") == "browser")
    return {"summary": summarize(g, items, nb), "items": items}


def cli(args):
    g = gather(gatekeeper=args.gatekeeper)
    items = entries(g)
    s = summarize(g, items)
    if args.json:
        print(json.dumps({"summary": s, "items": items}, ensure_ascii=False))
        return 0
    for e in sorted(items, key=lambda e: (not e["flags"], e["kind"], e["name"])):
        mark = "!" if e["flags"] else " "
        print(f"{mark} [{e['kind']}] {e['name']} · {e['path']}" + (f"\n      cờ: {', '.join(e['flags'])}" if e["flags"] else ""))
    print(f"\nTổng {s['total']} mục · có cờ {s['flagged']} · " + ", ".join(f"{k}={v}" for k, v in sorted(s["flags"].items())))
    print(f"Extension trình duyệt: {s['browser_extensions']['note']}")
    if not s["gatekeeper_checked"] and s["notarized_unknown"]:
        print(f"{s['notarized_unknown']} mục đã ký chưa rõ notarize (không có vé đính kèm) — `audit --gatekeeper` để kiểm")
    for e in s["errors"]:
        print(f"  ! {e}")
    print("\n" + ADVICE)
    return 0


def register(subparsers):
    p = subparsers.add_parser(NAME, help="kiểm kê cơ chế bám trụ + chữ ký mã (chỉ liệt kê, không phải diệt virus)")
    p.add_argument("--gatekeeper", action="store_true",
                   help="kiểm thêm notarize/Gatekeeper bằng codesign -R + spctl (chậm; macOS có thể tự tra với Apple)")
    p.add_argument("--json", action="store_true", help="in JSON")
    p.set_defaults(func=cli)
