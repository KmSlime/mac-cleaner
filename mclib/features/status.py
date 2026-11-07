"""Theo dõi máy (module monitor, thay `mo status`), CHỈ ĐỌC, không sudo: ổ đĩa, RAM (+ swap, áp lực bộ nhớ), CPU,
uptime/load, pin (pmset/ioreg), mạng (byte vào/ra từ lúc khởi động), thiết bị gắn ngoài (volume, USB, Bluetooth),
cảnh báo theo ngưỡng.

  python3 maccleaner.py status [--json]
  scan → report["features"]["status"] = {"summary": {disk, memory, cpu, battery, net, devices, alerts, …}, "items": []}

Mỗi phần đọc lỗi (máy không có pin, lệnh không có) → None, không làm hỏng phần khác.
"""
import json
import os
import re
import shutil
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


# ---------- monitor: CPU, pin, mạng, thiết bị, cảnh báo ----------
# Ngưỡng cảnh báo (chỉnh ở đây). level: info < warn < critical.
DISK_FREE_WARN, DISK_FREE_CRIT = 0.10, 0.05          # tỉ lệ trống của ổ
DISK_FREE_WARN_BYTES = 10 * 1024 ** 3                # hoặc còn dưới 10 GB
SWAP_WARN = 0.5                                      # swap đang dùng / RAM
CPU_BUSY_WARN = 90.0                                 # % CPU bận (user + sys)
BATTERY_LOW_WARN, BATTERY_LOW_CRIT = 20, 10          # % khi đang chạy pin
BATTERY_HEALTH_WARN = 80                             # % dung lượng tối đa so với thiết kế
BATTERY_CYCLES_WARN = 1000
UPTIME_INFO_DAYS = 30
PRESSURE = {1: "normal", 2: "warn", 4: "critical"}   # kern.memorystatus_vm_pressure_level


def parse_top_cpu(text):
    """`top -l N -n 0` → {"user","sys","idle"} (%) của mẫu CUỐI (mẫu đầu của top là trung bình từ lúc khởi động)."""
    found = re.findall(r"CPU usage:\s*([\d.]+)% user,\s*([\d.]+)% sys,\s*([\d.]+)% idle", text or "")
    if not found:
        return None
    u, sy, idle = (float(x) for x in found[-1])
    return dict(user=u, sys=sy, idle=idle, busy=round(u + sy, 2))


def parse_swap(text):
    """`sysctl -n vm.swapusage` ("total = 2048.00M  used = 1024.00M  free = …") → {total, used, free} byte."""
    mult = {"K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}
    out = {}
    for k, v, u in re.findall(r"(total|used|free)\s*=\s*([\d.]+)([KMG])", text or ""):
        out[k] = int(float(v) * mult[u])
    return out or None


def parse_pmset_batt(text):
    """`pmset -g batt` → {source, percent, state, remaining} hoặc None (máy không có pin)."""
    src = re.search(r"drawing from '([^']+)'", text or "")
    m = re.search(r"(\d+)%;\s*([^;]+?)\s*;\s*([^\n]*)", text or "")
    if not m:
        return None
    rem = re.search(r"(\d+:\d+) remaining", m.group(3))
    return dict(source=src.group(1) if src else None, percent=int(m.group(1)), state=m.group(2).strip(),
                remaining=rem.group(1) if rem else None)


IOREG_KEYS = ("CycleCount", "DesignCapacity", "MaxCapacity", "AppleRawMaxCapacity", "NominalChargeCapacity",
              "Temperature", "IsCharging", "ExternalConnected", "FullyCharged")


def parse_ioreg_battery(text):
    """`ioreg -rn AppleSmartBattery` → các khoá số/Yes-No ở cấp ngoài cùng (bỏ khối lồng như BatteryData chứa serial)."""
    out = {}
    for k, v in re.findall(r'^\s*"(\w+)" = (\d+|Yes|No)\s*$', text or "", re.M):
        if k in IOREG_KEYS:
            out[k] = True if v == "Yes" else False if v == "No" else int(v)
    if not out:
        return None
    raw, design = out.get("AppleRawMaxCapacity") or out.get("NominalChargeCapacity"), out.get("DesignCapacity")
    if raw and design:  # trên Apple silicon MaxCapacity là %, dung lượng thật nằm ở AppleRawMaxCapacity (mAh)
        out["health_percent"] = round(100 * raw / design, 1)
    elif out.get("MaxCapacity") and design and out["MaxCapacity"] > 100:
        out["health_percent"] = round(100 * out["MaxCapacity"] / design, 1)
    if "Temperature" in out:
        out["temperature_c"] = round(out.pop("Temperature") / 100, 1)
    return out


def parse_netstat(text):
    """`netstat -ib` → {iface: {"in": byte, "out": byte}} từ dòng <Link#>, bỏ lo0. Cột địa chỉ có thể trống nên
    đếm từ cuối dòng: … Ibytes Opkts Oerrs Obytes Coll."""
    out = {}
    for line in (text or "").splitlines()[1:]:
        cols = line.split()
        if len(cols) < 10 or not cols[2].startswith("<Link#"):
            continue
        name = cols[0].rstrip("*")
        if name.startswith("lo") or not (cols[-5].isdigit() and cols[-2].isdigit()):
            continue
        out[name] = {"in": int(cols[-5]), "out": int(cols[-2])}
    return out


def parse_usb(js):
    """JSON của `system_profiler -json SPUSBHostDataType` (macOS mới) / `SPUSBDataType` (cũ) → [{name, vendor}] của
    thiết bị lá (bỏ hub, bus). Không lấy số serial."""
    out = []

    def walk(nodes, depth):
        for n in nodes or []:
            if not isinstance(n, dict):
                continue
            kids = n.get("_items")
            name = n.get("_name", "")
            if kids:
                walk(kids, depth + 1)
            elif depth > 0 and name and "hub" not in name.lower():
                out.append(dict(name=name, vendor=n.get("USBDeviceKeyVendorName") or n.get("manufacturer") or ""))
    for key in ("SPUSBHostDataType", "SPUSBDataType"):
        if isinstance((js or {}).get(key), list):
            walk(js[key], 0)
    return out


def parse_bluetooth(js):
    """JSON của `system_profiler -json SPBluetoothDataType` → [{name, type}] thiết bị đang kết nối."""
    out = []
    for ctl in (js or {}).get("SPBluetoothDataType") or []:
        for dev in (ctl.get("device_connected") or []) if isinstance(ctl, dict) else []:
            for name, info in dev.items() if isinstance(dev, dict) else []:
                out.append(dict(name=name, type=(info or {}).get("device_minorType", "")))
    return out


def _profiler(run, kind):
    try:
        return json.loads(run(["/usr/sbin/system_profiler", "-json", "-detailLevel", "mini", kind], timeout=30) or "{}")
    except ValueError:
        return {}


def volumes(root="/Volumes"):
    """Volume gắn ngoài (bỏ volume hệ thống là symlink tới /)."""
    out = []
    for n in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        p = os.path.join(root, n)
        if os.path.realpath(p) == "/" or not os.path.ismount(p):
            continue
        try:
            u = shutil.disk_usage(p)
            out.append(dict(name=n, path=p, total=u.total, free=u.free))
        except OSError:
            out.append(dict(name=n, path=p, total=None, free=None))
    return out


def cpu(run=S.run):
    c = parse_top_cpu(run(["/usr/bin/top", "-l", "2", "-n", "0", "-s", "1"], timeout=20))
    ncpu = run(["/usr/sbin/sysctl", "-n", "hw.ncpu"]).strip()
    model = run(["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"]).strip()
    return dict(c or {}, cores=int(ncpu) if ncpu.isdigit() else None, model=model or None)


def battery(run=S.run):
    b = parse_pmset_batt(run(["/usr/bin/pmset", "-g", "batt"]))
    if b is None:
        return None
    b.update(parse_ioreg_battery(run(["/usr/sbin/ioreg", "-rn", "AppleSmartBattery"])) or {})
    return b


def alerts(info):
    """[{level, key, message}] theo ngưỡng ở đầu phần monitor."""
    out = []
    add = lambda lv, k, msg: out.append(dict(level=lv, key=k, message=msg))  # noqa: E731
    d = info.get("disk") or {}
    if d.get("total"):
        ratio = d["free"] / d["total"]
        if ratio < DISK_FREE_CRIT:
            add("critical", "disk", f"ổ gần đầy: còn {S.fmt(d['free'])} ({ratio:.0%})")
        elif ratio < DISK_FREE_WARN or d["free"] < DISK_FREE_WARN_BYTES:
            add("warn", "disk", f"ổ sắp đầy: còn {S.fmt(d['free'])} ({ratio:.0%})")
    m = info.get("memory") or {}
    lv = m.get("pressure")
    if lv in ("warn", "critical"):
        add(lv, "memory", f"áp lực bộ nhớ: {lv}")
    sw = m.get("swap") or {}
    if m.get("total") and sw.get("used", 0) > SWAP_WARN * m["total"]:
        add("warn", "swap", f"swap dùng {S.fmt(sw['used'])} (> {SWAP_WARN:.0%} RAM) — máy thiếu RAM")
    c = info.get("cpu") or {}
    if c.get("busy") is not None and c["busy"] >= CPU_BUSY_WARN:
        add("warn", "cpu", f"CPU bận {c['busy']:.0f}%")
    b = info.get("battery")
    if b:
        on_batt = (b.get("source") or "").lower().startswith("battery") or b.get("state") == "discharging"
        if on_batt and b.get("percent") is not None and b["percent"] <= BATTERY_LOW_CRIT:
            add("critical", "battery", f"pin còn {b['percent']}%")
        elif on_batt and b.get("percent") is not None and b["percent"] <= BATTERY_LOW_WARN:
            add("warn", "battery", f"pin còn {b['percent']}%")
        if b.get("health_percent") is not None and b["health_percent"] < BATTERY_HEALTH_WARN:
            add("warn", "battery-health", f"pin chai: còn {b['health_percent']:.0f}% dung lượng thiết kế")
        if (b.get("CycleCount") or 0) >= BATTERY_CYCLES_WARN:
            add("info", "battery-cycles", f"pin đã {b['CycleCount']} chu kỳ sạc")
    up = info.get("uptime_seconds")
    if up and up > UPTIME_INFO_DAYS * 86400:
        add("info", "uptime", f"máy chạy liên tục {up // 86400} ngày — khởi động lại giúp giải phóng bộ nhớ")
    return out


def monitor(run=S.run, now=None, volumes_root="/Volumes"):
    """gather() + CPU, swap, áp lực bộ nhớ, pin, mạng, thiết bị, cảnh báo. Phần nào lỗi → None."""
    info = gather(run, now)
    d = info["disk"]
    d["used_percent"] = round(100 * d["used"] / d["total"], 1) if d.get("total") else None
    lvl = run(["/usr/sbin/sysctl", "-n", "kern.memorystatus_vm_pressure_level"]).strip()
    info["memory"]["pressure"] = PRESSURE.get(int(lvl)) if lvl.isdigit() else None
    info["memory"]["swap"] = parse_swap(run(["/usr/sbin/sysctl", "-n", "vm.swapusage"]))
    info["cpu"] = cpu(run)
    info["battery"] = battery(run)
    ifaces = parse_netstat(run(["/usr/sbin/netstat", "-ib"]))
    info["net"] = dict(bytes_in=sum(v["in"] for v in ifaces.values()), bytes_out=sum(v["out"] for v in ifaces.values()),
                       interfaces={k: v for k, v in ifaces.items() if v["in"] or v["out"]}, since="boot")
    usb = _profiler(run, "SPUSBHostDataType")
    if not usb.get("SPUSBHostDataType"):
        usb = _profiler(run, "SPUSBDataType")
    try:
        vols = volumes(volumes_root)
    except OSError:
        vols = []
    info["devices"] = dict(volumes=vols, usb=parse_usb(usb), bluetooth=parse_bluetooth(_profiler(run, "SPBluetoothDataType")))
    info["alerts"] = alerts(info)
    return info


def collect(ctx):
    return {"summary": monitor(ctx.run), "items": []}


def _human(s):
    days, rem = divmod(int(s), 86400)
    return f"{days} ngày {rem // 3600} giờ {rem % 3600 // 60} phút"


def cli(args):
    info = monitor()
    if args.json:
        print(json.dumps(info, ensure_ascii=False))
        return 0
    d, m, c, b, n = info["disk"], info["memory"], info["cpu"], info["battery"], info["net"]
    print(f"Ổ đĩa : dùng {S.fmt(d['used'])} / {S.fmt(d['total'])}, trống {S.fmt(d['free'])}")
    sw = m.get("swap") or {}
    print(f"RAM   : {S.fmt(m['total'])} · đang dùng ~{S.fmt(m['used_estimate'])} (active + wired + nén)"
          + (f" · swap {S.fmt(sw.get('used'))}" if sw else "") + (f" · áp lực {m['pressure']}" if m.get("pressure") else ""))
    if c.get("busy") is not None:
        print(f"CPU   : bận {c['busy']:.0f}% (user {c['user']:.0f}%, sys {c['sys']:.0f}%) · {c.get('cores') or '?'} lõi"
              + (f" · {c['model']}" if c.get("model") else ""))
    print(f"Uptime: {_human(info['uptime_seconds']) if info['uptime_seconds'] is not None else '—'}"
          + (f" · load {' '.join(map(str, info['load_avg']))}" if info["load_avg"] else ""))
    if b:
        print(f"Pin   : {b['percent']}% · {b['state']}" + (f" · còn {b['remaining']}" if b.get("remaining") else "")
              + (f" · sức khoẻ {b['health_percent']:.0f}%" if b.get("health_percent") is not None else "")
              + (f" · {b['CycleCount']} chu kỳ" if b.get("CycleCount") is not None else ""))
    print(f"Mạng  : vào {S.fmt(n['bytes_in'])} · ra {S.fmt(n['bytes_out'])} (từ lúc khởi động)")
    dv = info["devices"]
    names = [v["name"] for v in dv["volumes"]] + [u["name"] for u in dv["usb"]] + [x["name"] for x in dv["bluetooth"]]
    print("Thiết bị: " + (", ".join(names) if names else "không có"))
    for a in info["alerts"]:
        print(f"[{a['level']}] {a['message']}")
    return 0


def register(subparsers):
    p = subparsers.add_parser(NAME, help="theo dõi máy: ổ, RAM, CPU, pin, mạng, thiết bị, cảnh báo (chỉ đọc)")
    p.add_argument("--json", action="store_true", help="in JSON")
    p.set_defaults(func=cli)
