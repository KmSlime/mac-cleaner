"""Tính năng mẫu, CHỈ ĐỌC: tình trạng máy — ổ đĩa, RAM, uptime, load. Không sudo, chỉ sysctl/vm_stat/os.

  python3 maccleaner.py status [--json]
  scan → report["features"]["status"] = {"summary": {...}, "items": []}
"""
import json
import os
import re
import time

from mclib import safety as S

NAME = "status"


def parse_vm_stat(text):
    """Output `vm_stat` → {"page_size": int, "<tên trang>": số trang} (khoá viết thường, nối bằng '_')."""
    out = {}
    m = re.search(r"page size of (\d+) bytes", text)
    if m:
        out["page_size"] = int(m.group(1))
    for line in text.splitlines():
        m = re.match(r'^"?([A-Za-z][\w \-"]*?)"?:\s+(\d+)\.?\s*$', line.strip())
        if m:
            out[re.sub(r"[^a-z]+", "_", m.group(1).lower()).strip("_")] = int(m.group(2))
    return out


def parse_boottime(text):
    """`sysctl -n kern.boottime` ("{ sec = 1700000000, usec = 0 } ...") → epoch giây, hoặc None."""
    m = re.search(r"sec\s*=\s*(\d+)", text or "")
    return int(m.group(1)) if m else None


def memory(run=S.run):
    total = run(["/usr/sbin/sysctl", "-n", "hw.memsize"]).strip()
    vm = parse_vm_stat(run(["/usr/bin/vm_stat"]))
    page = vm.get("page_size", 0)
    pages = lambda *ks: sum(vm.get(k, 0) for k in ks) * page  # noqa: E731
    return dict(total=int(total) if total.isdigit() else None,
                free=pages("pages_free", "pages_speculative"),
                active=pages("pages_active"), inactive=pages("pages_inactive"),
                wired=pages("pages_wired_down"), compressed=pages("pages_occupied_by_compressor"),
                used_estimate=pages("pages_active", "pages_wired_down", "pages_occupied_by_compressor"))


def gather(run=S.run, now=None):
    d = S.disk_usage()
    boot = parse_boottime(run(["/usr/sbin/sysctl", "-n", "kern.boottime"]))
    now = time.time() if now is None else now
    try:
        load = [round(x, 2) for x in os.getloadavg()]
    except OSError:
        load = None
    return dict(disk=dict(total=d.total, used=d.used, free=d.free), memory=memory(run),
                boot_time=boot, uptime_seconds=int(now - boot) if boot else None, load_avg=load)


def collect(ctx):
    return {"summary": gather(ctx.run), "items": []}


def _human(s):
    days, rem = divmod(int(s), 86400)
    return f"{days} ngày {rem // 3600} giờ {rem % 3600 // 60} phút"


def cli(args):
    info = gather()
    if args.json:
        print(json.dumps(info, ensure_ascii=False))
        return 0
    d, m = info["disk"], info["memory"]
    print(f"Ổ đĩa : dùng {S.fmt(d['used'])} / {S.fmt(d['total'])}, trống {S.fmt(d['free'])}")
    print(f"RAM   : {S.fmt(m['total'])} · đang dùng ~{S.fmt(m['used_estimate'])} (active + wired + nén)")
    print(f"Uptime: {_human(info['uptime_seconds']) if info['uptime_seconds'] is not None else '—'}"
          + (f" · load {' '.join(map(str, info['load_avg']))}" if info["load_avg"] else ""))
    return 0


def register(subparsers):
    p = subparsers.add_parser(NAME, help="tình trạng máy: ổ đĩa, RAM, uptime (chỉ đọc)")
    p.add_argument("--json", action="store_true", help="in JSON")
    p.set_defaults(func=cli)
