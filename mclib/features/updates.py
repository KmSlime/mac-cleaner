"""Liệt kê bản cập nhật (CHỈ LIỆT KÊ, không cài): app dùng Sparkle, Homebrew cask, macOS.

  python3 maccleaner.py updates [--offline] [--timeout GIÂY] [--json]
  scan → report["features"]["updates"] = {"summary", "updates": [{app, current, latest, source, url, status, error}],
                                          "items": [mục list-only cho app có bản mới]}

Sparkle: đọc SUFeedURL trong Info.plist → tải appcast (urllib, timeout ngắn) → so CFBundleShortVersionString /
CFBundleVersion. Homebrew: `brew outdated --cask --json=v2` (dữ liệu cục bộ, tắt auto-update). macOS: `softwareupdate -l`.
--offline: không mở kết nối mạng nào (không tải appcast, không chạy softwareupdate). `scan` luôn chạy offline.
Lỗi mạng / feed hỏng → ghi `error` cho riêng app đó, không làm hỏng lệnh.
"""
import concurrent.futures
import glob
import json
import os
import platform
import re
import subprocess
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from mclib import registry, safety as S

NAME = "updates"
SPARKLE_NS = "{http://www.andymatuschak.org/xml-namespaces/sparkle}"
TIMEOUT = 5            # giây cho mỗi appcast
SWU_TIMEOUT = 60       # softwareupdate -l hỏi máy chủ Apple: 15–60 giây (đã đo); quá giờ → ghi lỗi
BREW_TIMEOUT = 60
MAX_FEED_BYTES = 4 * 1024 * 1024
WORKERS = 8
BREW_PATHS = ("/opt/homebrew/bin/brew", "/usr/local/bin/brew")
USER_AGENT = "mac-cleaner-updates (+list-only)"


# ---------- app ----------
def app_roots(home=None):
    return ["/Applications", "/Applications/Utilities", os.path.join(home or S.HOME, "Applications")]


def list_apps(roots):
    """realpath các .app ở roots (cấp 1 và 2), không lặp."""
    seen, out = set(), []
    for r in roots:
        base = glob.escape(r)
        for a in sorted(glob.glob(os.path.join(base, "*.app")) + glob.glob(os.path.join(base, "*", "*.app"))):
            real = os.path.realpath(a)
            if real not in seen and not os.path.dirname(real).endswith(".app") and os.path.isdir(real):
                seen.add(real)
                out.append(real)
    return out


# ---------- so phiên bản ----------
def vkey(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v or "")))


def newer(latest, current):
    """latest mới hơn current? So theo các nhóm số; thiếu số ở một bên → không kết luận (False)."""
    a, b = vkey(latest), vkey(current)
    if not a or not b:
        return False
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


# ---------- Sparkle ----------
def parse_appcast(text):
    """Appcast (RSS + namespace Sparkle) → bản ổn định mới nhất cho macOS {version, build, url, title} hoặc None.
    Bỏ mục có <sparkle:channel> (beta…) và enclosure cho hệ khác. Từ chối DTD/ENTITY (XML độc)."""
    if re.search(r"<!(DOCTYPE|ENTITY)", text or "", re.I):
        raise ValueError("appcast có DTD/ENTITY — bỏ qua")
    root = ET.fromstring(text)
    best, best_key = None, None
    for it in root.iter("item"):
        ch = it.find(SPARKLE_NS + "channel")
        if ch is not None and (ch.text or "").strip():
            continue
        enc = it.find("enclosure")
        attr = (lambda k: enc.get(SPARKLE_NS + k)) if enc is not None else (lambda k: None)
        if (attr("os") or "macos").lower() not in ("macos", "osx", "mac"):
            continue
        short = (it.findtext(SPARKLE_NS + "shortVersionString") or attr("shortVersionString") or "").strip()
        build = (it.findtext(SPARKLE_NS + "version") or attr("version") or "").strip()
        if not short and not build:
            continue
        key = (vkey(build) if build else vkey(short), vkey(short))
        if best_key is None or key > best_key:
            url = (enc.get("url") if enc is not None else None) or (it.findtext("link") or "").strip() or None
            best_key = key
            best = dict(version=short or build, build=build, url=url, title=(it.findtext("title") or "").strip())
    return best


def fetch(url, timeout=TIMEOUT):
    """Tải appcast qua http(s), tối đa MAX_FEED_BYTES. Lỗi → ném lỗi (người gọi ghi `error`)."""
    if not re.match(r"^https?://", url or "", re.I):
        raise ValueError("feed không phải http(s)")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — đã chặn scheme ở trên
        data = r.read(MAX_FEED_BYTES + 1)
    if len(data) > MAX_FEED_BYTES:
        raise ValueError("appcast quá lớn")
    return data.decode("utf-8", "replace")


def sparkle_apps(roots):
    """App có SUFeedURL http(s): [{app, path, bundle_id, current, build, feed}] (bỏ app của Apple)."""
    out = []
    for p in list_apps(roots):
        info = S.plist(os.path.join(p, "Contents", "Info.plist"))
        feed = info.get("SUFeedURL")
        bid = str(info.get("CFBundleIdentifier") or "")
        if not isinstance(feed, str) or not re.match(r"^https?://", feed.strip(), re.I) or bid.startswith("com.apple."):
            continue
        out.append(dict(app=str(info.get("CFBundleDisplayName") or info.get("CFBundleName") or os.path.basename(p)[:-4]),
                        path=p, bundle_id=bid, current=str(info.get("CFBundleShortVersionString") or ""),
                        build=str(info.get("CFBundleVersion") or ""), feed=feed.strip()))
    return out


def check_sparkle(app, fetch=fetch, timeout=TIMEOUT, offline=False):
    row = dict(app=app["app"], path=app["path"], bundle_id=app["bundle_id"], current=app["current"], latest=None,
               source="sparkle", url=None, feed=app["feed"], status="offline" if offline else "current", error=None)
    if offline:
        return row
    try:
        best = parse_appcast(fetch(app["feed"], timeout))
    except (urllib.error.URLError, OSError, ValueError, ET.ParseError) as e:
        reason = getattr(e, "reason", None) or e
        row.update(status="error", error=f"{type(e).__name__}: {str(reason)[:160]}")
        return row
    if not best:
        row.update(status="error", error="appcast không có bản nào cho macOS")
        return row
    row.update(latest=best["version"], url=best["url"])
    if best["build"] and app["build"] and vkey(best["build"]) and vkey(app["build"]):
        up = newer(best["build"], app["build"])
    else:
        up = newer(best["version"], app["current"])
    row["status"] = "update" if up else "current"
    return row


# ---------- Homebrew ----------
def _run(cmd, timeout, env=None):
    """(mã thoát | None khi lỗi/quá giờ, stdout, stderr)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return r.returncode, r.stdout or "", r.stderr or ""
    except subprocess.TimeoutExpired:
        return None, "", f"quá {timeout}s"
    except (OSError, subprocess.SubprocessError) as e:
        return None, "", str(e)[:160]


def brew_path():
    return next((p for p in BREW_PATHS if os.access(p, os.X_OK)), None)


def parse_brew(text):
    """JSON của `brew outdated --cask --json=v2` → [{app, current, latest, source, ...}]."""
    data = json.loads(text or "{}")
    out = []
    for c in data.get("casks", []):
        cur = c.get("installed_versions") or []
        out.append(dict(app=c.get("name") or c.get("token") or "?", path=None, bundle_id="",
                        current=", ".join(cur) if isinstance(cur, list) else str(cur),
                        latest=str(c.get("current_version") or ""), source="brew", url=None, status="update",
                        error=None))
    return out


def brew_outdated(run=_run, brew=None):
    """(hàng cập nhật, lỗi | None). Không có brew → ([], None)."""
    brew = brew or brew_path()
    if not brew:
        return [], None
    env = dict(os.environ, HOMEBREW_NO_AUTO_UPDATE="1", HOMEBREW_NO_ANALYTICS="1", HOMEBREW_NO_ENV_HINTS="1")
    rc, out, err = run([brew, "outdated", "--cask", "--json=v2"], BREW_TIMEOUT, env)
    if rc is None or (rc != 0 and not out.strip()):
        return [], f"brew outdated lỗi: {(err or f'mã {rc}').strip()[:160]}"
    try:
        return parse_brew(out), None
    except (ValueError, AttributeError) as e:
        return [], f"không đọc được JSON của brew: {e}"


# ---------- macOS ----------
def parse_softwareupdate(text, current=""):
    """Output `softwareupdate -l` (định dạng mới `* Label:` + `Title: …, Version: …` và định dạng cũ)."""
    out, label = [], None
    for line in (text or "").splitlines():
        s = line.strip()
        m = re.match(r"^\*\s+Label:\s*(.+)$", s)
        if m:
            label = m.group(1).strip()
            continue
        m = re.match(r"^\*\s+(.+)$", s)
        if m:                                  # định dạng cũ: "* <label>" rồi dòng "<title> (<version>), …"
            label = m.group(1).strip()
            continue
        if not label:
            continue
        m = re.match(r"^Title:\s*(.*?),\s*Version:\s*([^,]+)", s)
        old = re.match(r"^(.*?)\s*\(([^)]+)\),", s)
        if m or old:
            title, ver = (m or old).group(1).strip(), (m or old).group(2).strip()
            out.append(dict(app=title, path=None, bundle_id="", latest=ver, source="macos",
                            current=current if title.lower().startswith("macos") else "",
                            url=None, status="update", error=None, label=label,
                            recommended=bool(re.search(r"Recommended:\s*YES|\[recommended\]", s, re.I)),
                            restart=bool(re.search(r"Action:\s*restart|\[restart\]", s, re.I))))
            label = None
    return out


def macos_updates(run=_run):
    rc, out, err = run(["/usr/sbin/softwareupdate", "-l"], SWU_TIMEOUT, None)
    if rc is None:
        return [], f"softwareupdate lỗi: {err}"
    rows = parse_softwareupdate(out, platform.mac_ver()[0])
    if not rows and rc != 0 and "no new software" not in (out + err).lower():
        return [], f"softwareupdate thoát mã {rc}: {(err or out).strip()[:160]}"
    return rows, None


# ---------- gom ----------
def gather(offline=False, timeout=TIMEOUT, roots=None, fetch=None, run=_run, brew=None, macos=True):
    """{updates[], errors[], sparkle, offline}. Các nguồn chạy song song; mỗi lỗi chỉ ảnh hưởng nguồn/app của nó."""
    fetch = fetch or globals()["fetch"]
    apps = sparkle_apps(roots or app_roots())
    errors = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        fb = ex.submit(brew_outdated, run, brew)
        fm = ex.submit(macos_updates, run) if macos and not offline else None
        rows = list(ex.map(lambda a: check_sparkle(a, fetch, timeout, offline), apps))
        b_rows, b_err = fb.result()
        m_rows, m_err = fm.result() if fm else ([], None)
    for e in (b_err, m_err):
        if e:
            errors.append(e)
    return dict(updates=[r for r in rows + b_rows + m_rows], errors=errors, offline=offline,
                sparkle=dict(apps=len(apps), checked=sum(r["status"] in ("update", "current") for r in rows),
                             failed=sum(r["status"] == "error" for r in rows)))


def collect(ctx):
    g = gather(offline=True, roots=app_roots(ctx.home))
    ups = [r for r in g["updates"] if r["status"] == "update"]
    items = [registry.entry(NAME, r["path"], name=r["app"], safety="inventory", delete_mode="list-only",
                            reason=f"có bản {r['latest']} (đang {r['current']}) qua {r['source']} — chỉ liệt kê",
                            source=r["source"], current=r["current"], latest=r["latest"])
             for r in ups if r.get("path")]
    return {"summary": dict(updates=len(ups), sparkleApps=g["sparkle"]["apps"], offline=True, errors=g["errors"],
                            note="scan không dùng mạng; chạy `updates` để kiểm tra appcast và macOS"),
            "updates": g["updates"], "items": items}


def cli(args):
    g = gather(offline=args.offline, timeout=args.timeout)
    if args.json:
        print(json.dumps(g, ensure_ascii=False))
        return 0
    ups = [r for r in g["updates"] if r["status"] == "update"]
    errs = [r for r in g["updates"] if r["status"] == "error"]
    if ups:
        print(f"{'nguồn':8} {'app':30} {'đang dùng':>14} {'mới nhất':>14}")
        for r in ups:
            print(f"{r['source']:8} {r['app'][:30]:30} {(r['current'] or '—')[:14]:>14} {(r['latest'] or '—')[:14]:>14}"
                  + (f"  {r['url']}" if r.get("url") else ""))
    for r in errs:
        print(f"! {r['source']} {r['app']}: {r['error']}")
    for e in g["errors"]:
        print(f"! {e}")
    sp = g["sparkle"]
    print(f"{len(ups)} bản cập nhật · Sparkle: {sp['apps']} app"
          + (" (offline — không tải appcast, bỏ qua macOS)" if g["offline"] else f", {sp['checked']} kiểm được, {sp['failed']} lỗi")
          + " · chỉ liệt kê, không cài")
    return 0


def register(subparsers):
    p = subparsers.add_parser(NAME, help="liệt kê bản cập nhật app (Sparkle, Homebrew cask) và macOS — không cài")
    p.add_argument("--offline", action="store_true", help="không dùng mạng (không tải appcast, không hỏi softwareupdate)")
    p.add_argument("--timeout", type=float, default=TIMEOUT, help=f"giây cho mỗi appcast (mặc định {TIMEOUT})")
    p.add_argument("--json", action="store_true", help="in JSON")
    p.set_defaults(func=cli)
