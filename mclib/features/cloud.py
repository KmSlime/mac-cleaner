"""Thư mục đồng bộ cloud: dung lượng bản local và file chỉ còn trên cloud. CHỈ ĐỌC, mọi mục `list-only`.

  python3 maccleaner.py cloud [--top N] [--json] [--timeout S]
  scan → report["features"]["cloud"] = {"summary": {"roots": [{provider, root, localBytes, files[]…}]}, "items": [...]}

Phát hiện: iCloud Drive (~/Library/Mobile Documents) và mọi nhà cung cấp File Provider (~/Library/CloudStorage/*:
OneDrive, Google Drive, Dropbox, Box…). Chỉ dùng lstat: bản local = st_blocks; file chỉ còn trên cloud = cờ SF_DATALESS
trong st_flags (hoặc placeholder `.<tên>.icloud` kiểu cũ, kích thước đọc từ plist của nó). Không đọc nội dung file, không
liệt kê thư mục dataless (liệt kê sẽ kích hoạt tải danh sách từ cloud) và chặn tải file trong lúc quét (setiopolicy_np);
phần không liệt kê được → "không xác định".
Xoá trong các thư mục này là xoá trên cloud, nên không có mục xoá. Giải phóng bản local (giữ trên cloud) làm ở app Swift
(`FileManager.evictUbiquitousItem(at:)` cho iCloud; nhà cung cấp khác: "Remove Download" trong Finder) — pha sau.
"""
import heapq
import json
import os
import stat
import time

from mclib import registry, safety as S
from mclib.features import _fs

NAME = "cloud"
COLLECT_TIMEOUT = 120
PROVIDERS = {"onedrive": "OneDrive", "googledrive": "Google Drive", "dropbox": "Dropbox", "box": "Box",
             "pcloud": "pCloud", "pclouddrive": "pCloud", "nextcloud": "Nextcloud", "megasync": "MEGA", "mega": "MEGA",
             "synologydrive": "Synology Drive", "icloud": "iCloud Drive"}
EVICT_NOTE = ("Giải phóng bản local (file vẫn còn trên cloud) làm ở app Swift — pha sau. Xoá trong thư mục này là "
              "xoá trên cloud.")


def provider_of(name):
    """Tên thư mục trong ~/Library/CloudStorage ("OneDrive-Personal", "GoogleDrive-<tài khoản>") → tên nhà cung cấp."""
    head = name.split("-", 1)[0]
    return PROVIDERS.get(head.lower().replace(" ", ""), head or name)


def find_roots(home=None):
    """[(provider, root)] các thư mục đồng bộ đang có."""
    icloud, storage = _fs.cloud_roots(home)
    out = [("iCloud Drive", icloud)] if os.path.isdir(icloud) else []
    try:
        names = sorted(os.listdir(storage))
    except OSError:
        names = []
    out += [(provider_of(n), os.path.join(storage, n)) for n in names
            if not n.startswith(".") and os.path.isdir(os.path.join(storage, n)) and not os.path.islink(os.path.join(storage, n))]
    return out


def placeholder_size(path):
    """Kích thước file gốc ghi trong placeholder `.<tên>.icloud` (plist), hoặc None."""
    v = S.plist(path).get("NSURLFileSizeKey")
    return v if isinstance(v, int) else None


def measure(root, timeout=None, top=10):
    """Duyệt một thư mục đồng bộ chỉ bằng lstat. Trả dict số đo + `largestLocal` (top file đang có bản local)."""
    t0 = time.time()
    w = _fs.Walk(root, timeout)
    files = local = logical = cloud_files = cloud_bytes = unknown_dirs = unknown_placeholders = 0
    biggest = []
    with _fs.io_policy(no_materialize=True):
        stack = [root]
        while stack and not w.expired():
            for e, st in w.scandir(stack.pop()):
                dataless = bool(st.st_flags & _fs.SF_DATALESS)
                if stat.S_ISDIR(st.st_mode):
                    if dataless:
                        unknown_dirs += 1  # chưa tải danh sách: liệt kê sẽ kích hoạt tải từ cloud
                    elif not w.skip_dir(e.path, st):
                        stack.append(e.path)
                    continue
                if not stat.S_ISREG(st.st_mode):
                    continue
                if e.name.startswith(".") and e.name.endswith(".icloud"):
                    size = placeholder_size(e.path)
                    files += 1
                    cloud_files += 1
                    if size is None:
                        unknown_placeholders += 1
                    else:
                        cloud_bytes += size
                        logical += size
                    continue
                files += 1
                logical += st.st_size
                if dataless:
                    cloud_files += 1
                    cloud_bytes += st.st_size
                    continue
                b = st.st_blocks * 512
                local += b
                item = (b, e.path)
                if len(biggest) < top:
                    heapq.heappush(biggest, item)
                elif item > biggest[0]:
                    heapq.heapreplace(biggest, item)
    stats = w.stats()
    complete = not (unknown_dirs or unknown_placeholders or stats["deniedCount"] or stats["partial"])
    note = "" if complete else ("không xác định đủ: " + ", ".join(x for x in (
        f"{unknown_dirs} thư mục chỉ còn trên cloud (không liệt kê để khỏi tải)" if unknown_dirs else "",
        f"{unknown_placeholders} placeholder không đọc được kích thước" if unknown_placeholders else "",
        f"{stats['deniedCount']} chỗ không đọc được" if stats["deniedCount"] else "",
        "hết thời hạn" if stats["partial"] else "") if x))
    big = [dict(path=p, localBytes=b) for b, p in sorted(biggest, reverse=True)]
    return dict(root=root, fileCount=files, files=[f["path"] for f in big], localBytes=local, logicalBytes=logical,
                cloudOnlyFiles=cloud_files,
                cloudOnlyBytes=cloud_bytes if complete else None, cloudOnlyBytesSeen=cloud_bytes,
                unknownDirs=unknown_dirs, complete=complete, note=note, seconds=round(time.time() - t0, 2),
                largestLocal=big, **stats)


def gather(home=None, timeout=None, top=10):
    """{"summary": {roots[], localBytes, …, note}, "items": [...]} — mục gốc + top file local, đều list-only."""
    t0 = time.time()
    deadline = t0 + timeout if timeout else None
    roots, items = [], []
    for provider, root in find_roots(home):
        left = (deadline - time.time()) if deadline else None
        m = measure(root, max(left, 0.01) if left is not None else None, top)
        m["provider"] = provider
        roots.append(m)
        items.append(registry.entry(
            NAME, root, name=provider, size=m["localBytes"], safety="inventory", delete_mode="list-only",
            reason=f"thư mục đồng bộ {provider} — {EVICT_NOTE}", kind="cloud-root", provider=provider,
            localBytes=m["localBytes"], cloudOnlyBytes=m["cloudOnlyBytes"], complete=m["complete"]))
        for f in m["largestLocal"]:
            items.append(registry.entry(
                NAME, f["path"], size=f["localBytes"], safety="inventory", delete_mode="list-only",
                reason=f"bản local của file đồng bộ {provider} — giải phóng (giữ trên cloud) làm ở app Swift",
                kind="cloud-local", provider=provider))
    summary = dict(roots=roots, providers=sorted({r["provider"] for r in roots}),
                   localBytes=sum(r["localBytes"] for r in roots),
                   cloudOnlyBytes=None if any(r["cloudOnlyBytes"] is None for r in roots)
                   else sum(r["cloudOnlyBytes"] for r in roots),
                   seconds=round(time.time() - t0, 2), note=EVICT_NOTE)
    return {"summary": summary, "items": items}


def collect(ctx):
    return gather(ctx.home, COLLECT_TIMEOUT)


def cli(args):
    res = gather(timeout=args.timeout or None, top=args.top)
    if args.json:
        print(json.dumps(res, ensure_ascii=False))
        return 0
    s = res["summary"]
    if not s["roots"]:
        print("Không thấy thư mục đồng bộ cloud (iCloud Drive / ~/Library/CloudStorage).")
        return 0
    unk = "không xác định"
    print(f"{len(s['roots'])} thư mục đồng bộ · bản local {S.fmt(s['localBytes'])} · chỉ còn trên cloud "
          f"{S.fmt(s['cloudOnlyBytes']) if s['cloudOnlyBytes'] is not None else unk} · {s['seconds']}s")
    for r in s["roots"]:
        cloud = S.fmt(r["cloudOnlyBytes"]) if r["cloudOnlyBytes"] is not None else f"{unk} (thấy {S.fmt(r['cloudOnlyBytesSeen'])})"
        print(f"\n  {r['provider']}: {r['root']}")
        print(f"    {r['fileCount']:,} file · local {S.fmt(r['localBytes'])} · trên cloud {cloud} ({r['cloudOnlyFiles']:,} file)")
        if r["note"]:
            print(f"    {r['note']}")
        for f in r["largestLocal"][:5]:
            print(f"    {S.fmt(f['localBytes']):>9}  {f['path']}")
    print(f"\nChỉ liệt kê. {EVICT_NOTE}")
    return 0


def register(subparsers):
    p = subparsers.add_parser(NAME, help="thư mục đồng bộ cloud: bản local vs chỉ còn trên cloud (chỉ đọc)")
    p.add_argument("--top", type=int, default=10, help="số file local lớn nhất mỗi thư mục (mặc định 10)")
    p.add_argument("--json", action="store_true", help="in JSON")
    p.add_argument("--timeout", type=float, default=300, help="giới hạn giây (0 = không giới hạn; mặc định 300)")
    p.set_defaults(func=cli)
