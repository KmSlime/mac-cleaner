"""Bản đồ dung lượng + file lớn và cũ. CHỈ ĐỌC (thay `mo analyze`).

  python3 maccleaner.py analyze [path] [--depth N] [--top K] [--json] [--out DIR] [--timeout S]
  python3 maccleaner.py large [path…] [--min 500M] [--older 180d] [--top N] [--json] [--timeout S]
  scan → report["features"]["space"] = {"summary": {tóm tắt cây home cấp 1–2}, "items": []}

Duyệt bằng lstat (st_blocks = dung lượng thật trên đĩa), không theo symlink, không vượt volume, hardlink đếm một lần;
thư mục không đọc được bị bỏ qua và ghi vào needsFDA/denied. Cây có hơn TREE_INLINE_MAX nút thì ghi đủ vào
<out>/space-<ts>.json, còn stdout (--json) chỉ giữ cấp 1 kèm "treeFile".
`large` trả mục `review` + `delete_mode: trash`; bỏ qua bundle/thư viện app (.app, .photoslibrary…), thư mục ẩn,
~/Library (trừ khi quét thẳng trong đó), kho Mail/Messages/Photos và thư mục cloud.
"""
import json
import os
import stat
import time

from mclib import registry, safety as S
from mclib.features import _fs

NAME = "space"
TREE_INLINE_MAX = 2000      # nút; lớn hơn → ghi file space-<ts>.json riêng
COLLECT_TIMEOUT = 15        # giây cho tóm tắt trong `scan` (hết giờ → partial; số đủ: `analyze`)


# ---------- cây dung lượng ----------
def _trim(kids, top, path):
    kids.sort(key=lambda n: (-n["size"], n["name"]))
    if top and len(kids) > top:
        rest = kids[top:]
        kids = kids[:top] + [dict(name=f"({len(rest)} mục khác)", path=path, kind="other", count=len(rest),
                                  size=sum(n["size"] for n in rest), files=sum(n["files"] for n in rest))]
    return kids


def _dir(w, path, st, level, depth, top):
    size, files, kids, keep = st.st_blocks * 512, 0, [], level < depth
    for e, est in w.scandir(path):
        if stat.S_ISDIR(est.st_mode):
            if w.skip_dir(e.path, est) or w.expired():
                continue
            child = _dir(w, e.path, est, level + 1, depth, top)
            size += child["size"]
            files += child["files"]
            if keep:
                kids.append(child)
        else:
            b = est.st_blocks * 512 if w.first_link(est) else 0
            size += b
            files += 1
            if keep:
                kids.append(dict(name=e.name, path=e.path, size=b, files=1, kind="file"))
    node = dict(name=os.path.basename(path) or path, path=path, size=size, files=files, kind="dir")
    if keep:
        node["children"] = _trim(kids, top, path)
    return node


def count_nodes(node):
    return 1 + sum(count_nodes(c) for c in node.get("children", ()))


def cut(node, depth):
    """Bản sao của cây chỉ giữ tới `depth` cấp con."""
    out = {k: v for k, v in node.items() if k != "children"}
    if "children" in node:
        out["children"] = [cut(c, depth - 1) for c in node["children"]] if depth > 0 else []
        out["truncated"] = depth <= 0 and bool(node["children"])
    return out


def build_tree(root, depth=3, top=20, timeout=None):
    """{"root", "tree", "nodes", "seconds", partial, needsFDA, deniedCount, denied[], otherVolumeSkipped}.
    tree = {name, path, size, files, kind: dir|file|other, children[]} tới `depth` cấp; mỗi cấp giữ `top` con lớn
    nhất, phần còn lại gộp thành một nút "other"."""
    t0 = time.time()
    root = _fs.canon(root)
    st = os.lstat(root)
    if not stat.S_ISDIR(st.st_mode):
        raise NotADirectoryError(root)
    w = _fs.Walk(root, timeout)
    tree = _dir(w, root, st, 0, max(depth, 0), top)
    return dict(root=root, tree=tree, nodes=count_nodes(tree), seconds=round(time.time() - t0, 2), **w.stats())


def summarize(res, top=10):
    """Tóm tắt cho report: tổng + top con cấp 1 (kèm top con cấp 2)."""
    kids = [dict(name=c["name"], path=c["path"], size=c["size"], files=c["files"], kind=c["kind"],
                 children=[dict(name=g["name"], path=g["path"], size=g["size"], kind=g["kind"])
                           for g in c.get("children", [])[:top]])
            for c in res["tree"].get("children", [])[:top]]
    return dict(root=res["root"], size=res["tree"]["size"], files=res["tree"]["files"], top=kids,
                **{k: res[k] for k in ("seconds", "partial", "needsFDA", "deniedCount", "denied", "otherVolumeSkipped")})


def collect(ctx):
    res = build_tree(ctx.home, depth=2, top=10, timeout=COLLECT_TIMEOUT)
    return {"summary": summarize(res), "items": []}


def save_tree(res, out):
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, "space-" + time.strftime("%Y%m%d-%H%M%S") + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)
    return path


def _print_tree(node, level=0, parent=None):
    if level:
        pct = f"{100 * node['size'] / parent:4.0f}%" if parent else "   —"
        name = node["name"] + ("/" if node["kind"] == "dir" else "")
        print(f"{'  ' * level}{S.fmt(node['size']):>9} {pct}  {name}")
    for c in node.get("children", []):
        _print_tree(c, level + 1, node["size"])


def _print_walk_notes(res):
    if res["partial"]:
        print("! Hết thời hạn (--timeout) — số liệu thiếu, chạy lại với --timeout lớn hơn.")
    if res["otherVolumeSkipped"]:
        print(f"  Bỏ qua {res['otherVolumeSkipped']} thư mục ở volume khác (không vượt volume).")
    if res["needsFDA"]:
        print(f"  Không đọc được {res['deniedCount']} chỗ (thiếu quyền). Cấp Full Disk Access cho Terminal/app: "
              f"open '{_fs.FDA_PANE}'")


def cli_analyze(args):
    try:
        res = build_tree(args.path, args.depth, args.top, args.timeout or None)
    except (OSError, NotADirectoryError) as e:
        print(f"Không phân tích được {args.path}: {e}")
        return 1
    if res["nodes"] > TREE_INLINE_MAX:
        res["treeFile"] = save_tree(res, args.out)
    if args.json:
        out = dict(res, tree=cut(res["tree"], 1)) if res.get("treeFile") else res
        print(json.dumps(out, ensure_ascii=False))
        return 0
    t = res["tree"]
    print(f"{res['root']} — {S.fmt(t['size'])} · {t['files']:,} file · {res['seconds']}s")
    _print_tree(t)
    _print_walk_notes(res)
    if res.get("treeFile"):
        print(f"  Cây đầy đủ ({res['nodes']:,} nút): {res['treeFile']}")
    return 0


# ---------- file lớn và cũ ----------
def find_large(roots, min_size=500 * 2 ** 20, older_days=180, timeout=None, now=None, home=None, limit=100):
    """{"summary": {...}, "items": [...]}: file ≥ min_size (kích thước logic) không mở/sửa trong older_days ngày
    (lần dùng cuối = max(mtime, atime)). Mỗi mục: review + trash. Không đọc nội dung file."""
    now = time.time() if now is None else now
    t0, found, skipped, walks = time.time(), [], [], []
    deadline = t0 + timeout if timeout else None
    for r in roots:
        root = _fs.canon(r)
        why = _fs.excluded_root(root, home)
        if why or not os.path.isdir(root):
            skipped.append(dict(path=root, reason=why or "không phải thư mục"))
            continue
        w = _fs.Walk(root, (deadline - time.time()) if deadline else None)
        walks.append(w)
        for path, st in _fs.iter_files(w, _fs.user_prune(root, home)):
            if st.st_size < min_size or not w.first_link(st):
                continue
            last = max(st.st_mtime, st.st_atime)
            age = (now - last) / 86400
            if age < older_days:
                continue
            found.append(registry.entry(
                NAME, path, size=st.st_blocks * 512, safety="review", delete_mode="trash",
                reason=f"file lớn {S.fmt(st.st_size)}, không mở/sửa {int(age)} ngày — xem lại trước khi xoá "
                       "(vào Thùng rác, khôi phục được)",
                kind="large-old", logicalSize=st.st_size, lastUsed=int(last)))
    found.sort(key=lambda e: -(e["size"] or 0))
    stats = [w.stats() for w in walks]
    summary = dict(roots=[w.root for w in walks], skipped=skipped, minSize=min_size, olderDays=older_days,
                   found=len(found), totalBytes=sum(e["size"] or 0 for e in found), shown=min(len(found), limit),
                   seconds=round(time.time() - t0, 2), lastUsedFrom="max(mtime, atime)",
                   partial=any(s["partial"] for s in stats), needsFDA=any(s["needsFDA"] for s in stats),
                   deniedCount=sum(s["deniedCount"] for s in stats),
                   denied=[p for s in stats for p in s["denied"]][:_fs.DENIED_SAMPLE])
    return {"summary": summary, "items": found[:limit] if limit else found}


def cli_large(args):
    try:
        min_size, older = _fs.parse_size(args.min), _fs.parse_age(args.older)
    except ValueError as e:
        print(e)
        return 2
    res = find_large(args.paths or [S.HOME], min_size, older, args.timeout or None, limit=args.top)
    if args.json:
        print(json.dumps(res, ensure_ascii=False))
        return 0
    s = res["summary"]
    print(f"File ≥ {S.fmt(min_size)}, không dùng ≥ {older} ngày: {s['found']} file · {S.fmt(s['totalBytes'])} · {s['seconds']}s")
    for e in res["items"]:
        print(f"  {S.fmt(e['size']):>9}  {time.strftime('%Y-%m-%d', time.localtime(e['lastUsed']))}  [{e['safety']}] {e['path']}")
    for k in s["skipped"]:
        print(f"  Bỏ qua {k['path']}: {k['reason']}")
    _print_walk_notes(dict(s, otherVolumeSkipped=0))
    if s["found"]:
        print("Chỉ xem — chưa xoá gì. Mục `review` xoá bằng cách chuyển vào Thùng rác (pha sau nối vào `clean`).")
    return 0


def register(subparsers):
    p = subparsers.add_parser("analyze", help="bản đồ dung lượng một thư mục (chỉ đọc)")
    p.add_argument("path", nargs="?", default=S.HOME)
    p.add_argument("--depth", type=int, default=3, help="số cấp hiển thị (mặc định 3)")
    p.add_argument("--top", type=int, default=20, help="giữ K con lớn nhất mỗi cấp, còn lại gộp (mặc định 20)")
    p.add_argument("--json", action="store_true", help="in JSON")
    p.add_argument("--out", default=os.path.join(S.DATA, "reports"), help="nơi ghi space-<ts>.json khi cây lớn")
    p.add_argument("--timeout", type=float, default=600, help="giới hạn giây (0 = không giới hạn; mặc định 600)")
    p.set_defaults(func=cli_analyze)
    q = subparsers.add_parser("large", help="file lớn và lâu không dùng (chỉ đọc)")
    q.add_argument("paths", nargs="*", help="thư mục (mặc định: home)")
    q.add_argument("--min", default="500M", help="kích thước tối thiểu (mặc định 500M)")
    q.add_argument("--older", default="180d", help="không dùng ít nhất (mặc định 180d; 0 = mọi tuổi)")
    q.add_argument("--top", type=int, default=100, help="số file tối đa in ra (mặc định 100)")
    q.add_argument("--json", action="store_true", help="in JSON")
    q.add_argument("--timeout", type=float, default=600, help="giới hạn giây (0 = không giới hạn; mặc định 600)")
    q.set_defaults(func=cli_large)
