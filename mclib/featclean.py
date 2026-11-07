"""Dọn mục của tính năng (report["features"][*]["items"], tạo bằng registry.entry) trong lệnh `clean`.

Luật (không đổi được bằng cờ):
- `list-only` và `protected` không bao giờ bị dọn, kể cả --force;
- `permanent` (xoá hẳn, không hoàn tác) chỉ chạy khi có thêm cờ --permanent;
- `trash`: kiểm lại bằng safety.validate rồi safety.run_action → Thùng rác (LaunchAgent được bootout trước);
- app sở hữu mục đang chạy (khai qua khoá `owners` / `lock_dir` của mục) → bỏ qua;
- ghi vào cùng logs/clean-<ts>.jsonl với mục gốc, cùng định dạng.

Chọn mục: --ids (mọi mục, kể cả list-only để báo lý do), --category <tính năng> hoặc <tính năng>:<kind>,
mặc định chỉ `safe`; --include-review thêm `review`.
"""
import json
import os
import re
import shutil
import time

from . import safety as S


def entries(features):
    """Mọi mục hợp lệ trong report["features"] (bỏ qua tính năng lỗi / không có items)."""
    for res in (features or {}).values():
        if isinstance(res, dict):
            for e in res.get("items") or []:
                if isinstance(e, dict) and e.get("id") and e.get("path"):
                    yield e


def matches(e, categories):
    feat, kind = e.get("feature", ""), e.get("kind", "")
    return any(c == feat or (kind and c == f"{feat}:{kind}") for c in categories)


def select(features, ids=None, categories=None, include_review=False):
    ids = set(ids or [])
    out = []
    for e in entries(features):
        if ids:
            if e["id"] in ids:
                out.append(e)
            continue
        if categories and not matches(e, categories):
            continue
        if e.get("delete_mode") == "list-only":
            continue
        if e.get("safety") == "safe" or (include_review and e.get("safety") == "review"):
            out.append(e)
    return out


def as_item(e):
    """Mục feature → dạng mục gốc để dùng safety.validate (inventory ≈ info; trash/permanent đều cần đường dẫn hợp lệ)."""
    return dict(e, category=e.get("feature", ""), kind=e.get("kind", ""),
                safety={"inventory": "info"}.get(e.get("safety"), e.get("safety")),
                action="trash" if e.get("delete_mode") in ("trash", "permanent") else None)


# ---------- app sở hữu đang chạy ----------
def running_names(run=S.run):
    """Tên nhận diện (chữ thường) của app đang chạy: bundle id, tên .app, CFBundleName, CFBundleExecutable."""
    out, seen = set(), set()
    for line in run(["/bin/ps", "-axo", "comm="], timeout=10).splitlines():
        m = re.match(r"(.+?\.app)/Contents/MacOS/", line.strip())
        if not m or m.group(1) in seen:
            continue
        app = m.group(1)
        seen.add(app)
        out.add(os.path.basename(app)[:-4].lower())
        info = S.plist(os.path.join(app, "Contents/Info.plist"))
        for k in ("CFBundleIdentifier", "CFBundleName", "CFBundleExecutable"):
            v = info.get(k)
            if isinstance(v, str) and v:
                out.add(v.lower())
    return out


def lock_alive(d):
    """Chromium/Electron giữ symlink SingletonLock ("<máy>-<pid>") trong thư mục dữ liệu khi đang chạy.
    Có khoá mà không đọc được pid → coi như đang chạy (nghi ngờ thì giữ)."""
    if not d:
        return False
    try:
        target = os.readlink(os.path.join(d, "SingletonLock"))
    except OSError:
        return False
    m = re.search(r"-(\d+)$", target)
    if not m:
        return True
    try:
        os.kill(int(m.group(1)), 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:  # PermissionError: tiến trình của user khác vẫn đang sống
        return True


def owner_running(e, names):
    """Mục có app chủ đang chạy? owners: bundle id / tên app; bundle id khớp cả tiền tố (helper, ByHost)."""
    for o in e.get("owners") or []:
        o = str(o).lower()
        if o in names:
            return True
        if "." in o and any("." in n and (n.startswith(o + ".") or o.startswith(n + ".")) for n in names):
            return True
    return lock_alive(e.get("lock_dir"))


def remove_permanent(p):
    try:
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p)
        else:
            os.remove(p)
        return None
    except OSError as err:
        return str(err)[:200]


def clean(chosen, yes=False, log=print, force=False, permanent=False, log_path=None, running=None):
    """Như safety.clean nhưng cho mục feature. yes=False: chỉ xem trước. running: tập tên app đang chạy (test)."""
    results, freed = [], 0
    if yes:
        log_path = log_path or os.path.join(S.DATA, "logs", "clean-" + time.strftime("%Y%m%d-%H%M%S") + ".jsonl")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
    names = running_names() if running is None and chosen else (running or set())
    for e in chosen:
        mode, it = e.get("delete_mode"), as_item(e)
        if mode not in ("trash", "permanent") or e.get("safety") == "protected":
            why = "chỉ liệt kê — mac-cleaner không dọn mục này"
        elif mode == "permanent" and not permanent:
            why = "xoá vĩnh viễn, không hoàn tác — cần thêm --permanent"
        else:
            why = S.validate(it, force)
        if not why and owner_running(e, names):
            why = "app sở hữu đang chạy — đóng app rồi dọn lại"
        status = "skip" if why else "dry-run" if not yes else "ok"
        if not why and yes:
            err = S.run_action(it, "trash") if mode == "trash" else remove_permanent(e["path"])
            if err:
                status, why = "error", err
        if status in ("ok", "dry-run"):
            freed += e.get("size") or 0
        res = dict(id=e["id"], path=e["path"], size=e.get("size"), status=status, reason=why or "",
                   feature=e.get("feature", ""), delete_mode=mode)
        results.append(res)
        log(f"[{status:7}] {S.fmt(e.get('size')):>9}  {e['path']}" + (f"  ({why})" if why else ""))
        if yes:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(res, ensure_ascii=False) + "\n")
    return dict(results=results, freed=freed, log=log_path if yes else None)
