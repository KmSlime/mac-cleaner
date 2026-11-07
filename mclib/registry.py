"""Nạp tính năng kiểu plugin từ mclib/features/ và hợp đồng dữ liệu cho report["features"].

Một tính năng = một file mclib/features/<tên>.py, khai (đều tuỳ chọn, cần ít nhất một trong hai):
  NAME = "<tên>"                       tên lệnh con + khoá trong report["features"] (mặc định: tên file)
  def register(subparsers) -> None     thêm lệnh con; parser phải set_defaults(func=<hàm chạy>(args) -> int)
  def collect(ctx: ReportContext) -> dict   chạy trong `scan`; kết quả ghi vào report["features"][NAME]
File bắt đầu bằng "_" bị bỏ qua. Module lỗi chỉ in cảnh báo, không làm hỏng scan/clean.
"""
import hashlib
import importlib
import json
import os
import pkgutil
import sys
import time

from . import safety as S

FEATURES_PKG = "mclib.features"
RESERVED = {"scan", "clean"}
SAFETY_LEVELS = ("safe", "review", "inventory", "protected")
DELETE_MODES = ("trash", "permanent", "list-only")


def _warn(msg):
    print(f"[mclib] {msg}", file=sys.stderr, flush=True)


class ReportContext:
    """Những gì collect() được dùng. items: mục của scan chính (CHỈ ĐỌC). log: in tiến độ (stderr)."""

    def __init__(self, items=(), log=None, home=None, data=None):
        self.items = list(items)
        self.log = log or (lambda m: None)
        self.home = home or S.HOME
        self.data = data or S.DATA
        self.run = S.run
        self.du = S.du


def entry(feature, path, *, name="", size=None, safety="inventory", delete_mode="list-only", reason="", **extra):
    """Một mục trong report["features"][feature]["items"] theo hợp đồng. Áp luật an toàn:
    - safety/delete_mode phải hợp lệ; 'permanent' không được đi với 'safe' (xoá vĩnh viễn luôn cần xem lại);
    - đường dẫn bị chặn cứng (SIP, thư mục gốc, bảo mật) hoặc nằm trong vùng bảo vệ → protected + list-only;
    - protected luôn list-only."""
    if safety not in SAFETY_LEVELS:
        raise ValueError(f"safety không hợp lệ: {safety!r} (chỉ {SAFETY_LEVELS})")
    if delete_mode not in DELETE_MODES:
        raise ValueError(f"delete_mode không hợp lệ: {delete_mode!r} (chỉ {DELETE_MODES})")
    if delete_mode == "permanent" and safety == "safe":
        raise ValueError("delete_mode 'permanent' không được để mức 'safe' — dùng 'review'")
    if not os.path.isabs(path):
        raise ValueError(f"path phải là đường dẫn tuyệt đối: {path!r}")
    guard = None
    if delete_mode != "list-only":
        guard = S.hard_deny(path) or (S.is_protected(path, extra.get("kind", "")) and "được bảo vệ") or S.guard_reason(path)
    if safety == "protected" or guard:
        safety, delete_mode = "protected", "list-only"
        if guard:
            reason = (reason + " · " if reason else "") + guard
    iid = hashlib.sha1(f"feature|{feature}|{path}".encode()).hexdigest()[:10]
    return dict(id=iid, feature=feature, name=name or os.path.basename(path.rstrip("/")) or path, path=path,
                size=size, safety=safety, delete_mode=delete_mode, reason=reason, **extra)


def check_entry(e):
    """Kiểm một mục do module trả về (có thể không tạo bằng entry()). Lỗi → ValueError."""
    for k in ("id", "path", "safety", "delete_mode", "reason"):
        if k not in e:
            raise ValueError(f"mục thiếu khoá {k!r}: {e}")
    if e["safety"] not in SAFETY_LEVELS or e["delete_mode"] not in DELETE_MODES:
        raise ValueError(f"safety/delete_mode không hợp lệ: {e}")
    if e["safety"] == "protected" and e["delete_mode"] != "list-only":
        raise ValueError(f"mục protected phải là list-only: {e}")
    return e


def discover(package=FEATURES_PKG):
    """[(tên, module)] của mọi tính năng trong package (theo tên file). Module lỗi/trùng tên → cảnh báo, bỏ qua."""
    try:
        pkg = importlib.import_module(package)
    except Exception as e:  # noqa: BLE001 — thư mục features hỏng không được làm hỏng lệnh chính
        _warn(f"không nạp được {package}: {e}")
        return []
    out, seen = [], set()
    for m in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
        if m.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"{package}.{m.name}")
        except Exception as e:  # noqa: BLE001
            _warn(f"bỏ qua tính năng {m.name}: lỗi import {type(e).__name__}: {e}")
            continue
        name = getattr(mod, "NAME", m.name)
        if name in RESERVED or name in seen:
            _warn(f"bỏ qua tính năng {m.name}: tên {name!r} trùng")
            continue
        if not (callable(getattr(mod, "register", None)) or callable(getattr(mod, "collect", None))):
            _warn(f"bỏ qua tính năng {m.name}: không có register() hay collect()")
            continue
        seen.add(name)
        out.append((name, mod))
    return out


def register_all(subparsers, features):
    """Gọi register(subparsers) của từng tính năng có khai."""
    for name, mod in features:
        if callable(getattr(mod, "register", None)):
            try:
                mod.register(subparsers)
            except Exception as e:  # noqa: BLE001
                _warn(f"tính năng {name}: register() lỗi {type(e).__name__}: {e}")


def collect_all(ctx, features):
    """{tên: kết quả collect()} cho report["features"]. Kết quả phải là dict JSON được; nếu có "items" thì từng mục
    phải đúng hợp đồng. Lỗi của một tính năng → {"error": "..."} cho riêng tính năng đó, scan vẫn chạy tiếp."""
    out = {}
    for name, mod in features:
        fn = getattr(mod, "collect", None)
        if not callable(fn):
            continue
        t = time.time()
        ctx.log(f"▸ Tính năng: {name}…")
        try:
            res = fn(ctx)
            if not isinstance(res, dict):
                raise TypeError(f"collect() phải trả dict, nhận {type(res).__name__}")
            for e in res.get("items", []):
                check_entry(e)
            json.dumps(res, ensure_ascii=False)
        except Exception as e:  # noqa: BLE001
            res = {"error": f"{type(e).__name__}: {e}"}
            ctx.log(f"  ! {name}: {res['error']}")
        res["seconds"] = round(time.time() - t, 2)
        out[name] = res
        if "error" not in res:
            ctx.log(f"  ✓ {len(res.get('items', []))} mục · {res['seconds']:.1f}s")
    return out
