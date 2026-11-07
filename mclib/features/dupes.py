"""File trùng lặp. CHỈ ĐỌC (đọc nội dung để băm, không sửa, không cập nhật atime, không tải file cloud).

  python3 maccleaner.py dupes [path…] [--min-size 1M] [--prefer DIR] [--top N] [--json] [--timeout S] [--images]

Cách tìm: gom theo kích thước → sha256 của 64 KB đầu + 64 KB cuối → sha256 toàn file chỉ cho nhóm còn trùng.
Hardlink (cùng inode) là một file, không tính trùng. Mỗi nhóm GIỮ một bản (`inventory` + `list-only`): ưu tiên bản nằm
trong --prefer, rồi bản không ở ~/Downloads và không mang tên kiểu "copy"/"(1)", rồi bản cũ nhất; các bản còn lại
`review` + `trash`. Không bao giờ đề xuất xoá hết một nhóm.
Bỏ qua: bundle/thư viện app (.app, .photoslibrary…), thư mục ẩn (gồm .git), ~/Library (trừ khi quét thẳng trong đó),
kho Mail/Messages/Photos, thư mục cloud, node_modules/Pods/DerivedData.
--images (ảnh gần giống): bị chặn ở Python thuần — cần Vision (VNGenerateImageFeaturePrintRequest) trong app Swift, pha
sau. Lệnh chỉ in ghi chú, không làm bản thô.
"""
import hashlib
import json
import os
import re
import time
from collections import defaultdict

from mclib import registry, safety as S
from mclib.features import _fs

NAME = "dupes"
CHUNK = 64 * 1024
PRUNE_NAMES = ("node_modules", "Pods", "DerivedData")
COPY_NAME = re.compile(r"(?i)(\scopy(\s\d+)?|\s-\scopy|\s-\sbản sao(\s\d+)?|\sbản sao(\s\d+)?|\s?\(\d+\))$")
IMAGES_NOTE = ("Ảnh gần giống cần Vision (VNGenerateImageFeaturePrintRequest) trong app Swift — pha sau. "
               "Python thuần không làm bản thô; kết quả dưới đây chỉ là file trùng y hệt.")
APFS_NOTE = ("Ước tính: bản clone APFS dùng chung block nên xoá có thể không giải phóng — so `df` trước và sau.")


def file_hash(path, size, partial):
    """sha256 hex. partial=True và file > 2*CHUNK: chỉ băm CHUNK đầu + CHUNK cuối (kèm kích thước)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        if partial and size > 2 * CHUNK:
            h.update(str(size).encode())
            h.update(f.read(CHUNK))
            f.seek(-CHUNK, os.SEEK_END)
            h.update(f.read(CHUNK))
        else:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
    return h.hexdigest()


def _by_hash(files, size, partial, errors, expired):
    out = defaultdict(list)
    for path, st in files:
        if expired():
            break
        try:
            out[file_hash(path, size, partial)].append((path, st))
        except OSError:
            errors.append(path)
    return [g for g in out.items() if len(g[1]) > 1]


def _secondary(path, home):
    stem = os.path.splitext(os.path.basename(path))[0]
    return _fs.under(path, os.path.join(home, "Downloads")) or bool(COPY_NAME.search(stem))


def pick_keep(files, prefer=(), home=None):
    """Bản giữ lại: trong --prefer trước, rồi bản ở vị trí "chính", rồi cũ nhất (birthtime), rồi đường dẫn ngắn."""
    home = home or S.HOME
    def key(f):
        path, st = f
        return (not any(_fs.under(path, p) for p in prefer), _secondary(path, home),
                getattr(st, "st_birthtime", st.st_mtime), len(path), path)
    return min(files, key=key)


def find_dupes(roots, min_size=2 ** 20, prefer=(), timeout=None, home=None):
    """{"summary", "duplicates": [{hash, size, files[], keep, reclaimable}], "items": [...]}."""
    t0 = time.time()
    deadline = t0 + timeout if timeout else None
    expired = lambda: bool(deadline) and time.time() > deadline  # noqa: E731
    prefer = [_fs.canon(p) for p in prefer]
    by_size, seen, walks, skipped, errors, scanned = defaultdict(list), set(), [], [], [], 0
    with _fs.io_policy(no_materialize=True, no_atime=True):
        for r in roots:
            root = _fs.canon(r)
            why = _fs.excluded_root(root, home)
            if why or not os.path.isdir(root):
                skipped.append(dict(path=root, reason=why or "không phải thư mục"))
                continue
            w = _fs.Walk(root, (deadline - time.time()) if deadline else None)
            walks.append(w)
            for path, st in _fs.iter_files(w, _fs.user_prune(root, home, PRUNE_NAMES)):
                if st.st_size < min_size or st.st_flags & _fs.SF_DATALESS:
                    continue
                ino = (st.st_dev, st.st_ino)
                if ino in seen:  # hardlink hoặc gốc chồng nhau: cùng một file
                    continue
                seen.add(ino)
                scanned += 1
                by_size[st.st_size].append((path, st))
        groups = []
        for size, files in sorted(by_size.items(), reverse=True):
            if len(files) < 2 or expired():
                continue
            for h, same in _by_hash(files, size, True, errors, expired):
                if size > 2 * CHUNK:
                    groups += [(size, full, g) for full, g in _by_hash(same, size, False, errors, expired)]
                else:
                    groups.append((size, h, same))
    dups, items = [], []
    for size, h, files in groups:
        keep = pick_keep(files, prefer, home)
        files = [keep] + sorted(f for f in files if f is not keep)
        alloc = keep[1].st_blocks * 512
        gid = h[:12]
        group_items = [registry.entry(NAME, keep[0], size=alloc, safety="inventory", delete_mode="list-only",
                                      reason=f"bản giữ lại của nhóm {len(files)} file trùng", group=gid, keep=True)]
        for path, st in files[1:]:
            group_items.append(registry.entry(
                NAME, path, size=st.st_blocks * 512, safety="review", delete_mode="trash",
                reason=f"trùng nội dung (sha256) với {keep[0]} — bản đó được giữ; xoá vào Thùng rác", group=gid,
                keep=False))
        if not any(e["delete_mode"] == "list-only" for e in group_items):  # không bao giờ xoá hết một nhóm
            raise RuntimeError(f"nhóm {gid} không còn bản giữ lại")
        items += group_items
        dups.append(dict(hash=h, size=size, files=[f[0] for f in files], keep=keep[0],
                         reclaimable=sum(e["size"] or 0 for e in group_items if e["delete_mode"] == "trash")))
    dups.sort(key=lambda d: -d["reclaimable"])
    stats = [w.stats() for w in walks]
    summary = dict(roots=[w.root for w in walks], skipped=skipped, minSize=min_size, scannedFiles=scanned,
                   groups=len(dups), duplicateFiles=sum(len(d["files"]) - 1 for d in dups),
                   reclaimableBytes=sum(d["reclaimable"] for d in dups), note=APFS_NOTE, unreadable=len(errors),
                   seconds=round(time.time() - t0, 2), partial=expired() or any(s["partial"] for s in stats),
                   needsFDA=any(s["needsFDA"] for s in stats), deniedCount=sum(s["deniedCount"] for s in stats),
                   denied=[p for s in stats for p in s["denied"]][:_fs.DENIED_SAMPLE])
    return {"summary": summary, "duplicates": dups, "items": items}


def cli(args):
    try:
        min_size = _fs.parse_size(args.min_size)
    except ValueError as e:
        print(e)
        return 2
    res = find_dupes(args.paths or [S.HOME], min_size, args.prefer, args.timeout or None)
    if args.images:
        res["images"] = dict(blocked=True, note=IMAGES_NOTE)
    if args.json:
        print(json.dumps(res, ensure_ascii=False))
        return 0
    s = res["summary"]
    if args.images:
        print("! " + IMAGES_NOTE)
    print(f"Quét {s['scannedFiles']:,} file ≥ {S.fmt(min_size)}: {s['groups']} nhóm trùng, {s['duplicateFiles']} bản thừa, "
          f"thu hồi được ~{S.fmt(s['reclaimableBytes'])} · {s['seconds']}s")
    for d in res["duplicates"][:args.top]:
        print(f"\n  {len(d['files'])} × {S.fmt(d['size'])}  (thừa {S.fmt(d['reclaimable'])})  sha256 {d['hash'][:12]}")
        print(f"    GIỮ   {d['keep']}")
        for p in d["files"][1:]:
            print(f"    xoá?  {p}")
    for k in s["skipped"]:
        print(f"  Bỏ qua {k['path']}: {k['reason']}")
    if s["partial"]:
        print("! Hết thời hạn (--timeout) — kết quả thiếu.")
    if s["needsFDA"]:
        print(f"  Không đọc được {s['deniedCount']} chỗ (thiếu quyền): open '{_fs.FDA_PANE}'")
    if s["groups"]:
        print(f"\nChỉ xem — chưa xoá gì. {APFS_NOTE}")
    return 0


def register(subparsers):
    p = subparsers.add_parser(NAME, help="file trùng lặp (chỉ đọc; luôn giữ một bản mỗi nhóm)")
    p.add_argument("paths", nargs="*", help="thư mục (mặc định: home)")
    p.add_argument("--min-size", default="1M", help="bỏ file nhỏ hơn (mặc định 1M)")
    p.add_argument("--prefer", action="append", default=[], help="thư mục ưu tiên giữ bản trong đó (lặp được)")
    p.add_argument("--top", type=int, default=50, help="số nhóm in ra (mặc định 50)")
    p.add_argument("--json", action="store_true", help="in JSON")
    p.add_argument("--timeout", type=float, default=900, help="giới hạn giây (0 = không giới hạn; mặc định 900)")
    p.add_argument("--images", action="store_true", help="ảnh gần giống — chưa hỗ trợ ở Python (cần Vision, app Swift)")
    p.set_defaults(func=cli)
