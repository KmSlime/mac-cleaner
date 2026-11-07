"""Tiện ích duyệt hệ thống file dùng chung cho space / dupes / cloud. Tên bắt đầu bằng "_" nên registry không nạp
như một tính năng.

Luật duyệt (CLEANMYMAC-PARITY §4): lstat, không đi theo symlink, không vượt sang volume khác, chỗ không đọc được thì
bỏ qua và ghi lại (needsFDA). Danh sách SKIP_FS cùng ý với scripts/classify.py (scripts/ không có trong bundle .app nên
không import được từ mclib).
"""
import contextlib
import ctypes
import os
import re
import stat
import time

from mclib import safety as S

# Volume khác / ảo / Data volume nhìn qua firmlink: không đi vào (trừ khi chính gốc quét nằm trong đó).
SKIP_FS = ("/System/Volumes", "/Volumes", "/dev", "/net", "/home", "/cores")
SF_DATALESS = getattr(stat, "SF_DATALESS", 0x40000000)  # file/thư mục chỉ còn trên cloud (File Provider)
# Bundle/thư viện do app quản: chỉ app sở hữu được sửa bên trong.
BUNDLE_EXT = (".app", ".photoslibrary", ".photolibrary", ".musiclibrary", ".tvlibrary", ".imovielibrary", ".fcpbundle")
FDA_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
DENIED_SAMPLE = 20


def cloud_roots(home=None):
    """Gốc các thư mục đồng bộ cloud: iCloud Drive + mọi nhà cung cấp File Provider."""
    home = home or S.HOME
    return [os.path.join(home, "Library", "Mobile Documents"), os.path.join(home, "Library", "CloudStorage")]


def apple_data(home=None):
    """Kho dữ liệu của app Apple: chỉ app sở hữu được sửa bên trong."""
    home = home or S.HOME
    return [os.path.join(home, "Library", n) for n in ("Mail", "Messages", "Photos")]


def under(p, prefix):
    return p == prefix or p.startswith(prefix.rstrip("/") + "/")


def parse_size(text):
    """'500M', '1.5G', '200MB', '64k', '1024' → byte (cơ số 1024). Sai định dạng → ValueError."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kmgt]?)(?:i?b)?\s*", str(text), re.I)
    if not m:
        raise ValueError(f"kích thước không hợp lệ: {text!r} (vd 500M, 1G)")
    return int(float(m.group(1)) * 1024 ** "_kmgt".index(m.group(2).lower() or "_"))


def parse_age(text):
    """'180d', '26w', '6m' (30 ngày), '1y', '0' → số ngày. Sai định dạng → ValueError."""
    m = re.fullmatch(r"\s*(\d+)\s*([dwmy]?)\s*", str(text), re.I)
    if not m:
        raise ValueError(f"tuổi không hợp lệ: {text!r} (vd 180d, 6m, 1y)")
    return int(m.group(1)) * {"": 1, "d": 1, "w": 7, "m": 30, "y": 365}[m.group(2).lower()]


class Walk:
    """Trạng thái một lần duyệt: thời hạn, chỗ không đọc được, số mục ở volume khác, inode đã đếm (hardlink)."""

    def __init__(self, root, timeout=None):
        self.root = root
        self.deadline = time.time() + timeout if timeout else None
        self.partial = False  # hết thời hạn → kết quả thiếu
        self.denied, self.denied_count, self.other_volume = [], 0, 0
        self.devs = allowed_devs(root)
        self._inodes = set()

    def expired(self):
        if self.deadline and time.time() > self.deadline:
            self.partial = True
        return self.partial

    def deny(self, path):
        self.denied_count += 1
        if len(self.denied) < DENIED_SAMPLE:
            self.denied.append(path)

    def first_link(self, st):
        """False nếu inode (hardlink) đã đếm rồi — không đếm dung lượng hai lần."""
        if st.st_nlink < 2:
            return True
        key = (st.st_dev, st.st_ino)
        if key in self._inodes:
            return False
        self._inodes.add(key)
        return True

    def skip_dir(self, path, st):
        """Thư mục con không được đi vào: volume khác hoặc nằm trong SKIP_FS (trừ khi gốc quét ở trong đó)."""
        if st.st_dev not in self.devs:
            self.other_volume += 1
            return True
        return any(under(path, s) and not under(self.root, s) for s in SKIP_FS)

    def scandir(self, path):
        """[(DirEntry, lstat)] của một thư mục; không đọc được → ghi denied, trả []."""
        try:
            with os.scandir(path) as it:
                entries = list(it)
        except OSError:
            self.deny(path)
            return []
        out = []
        for e in entries:
            try:
                out.append((e, e.stat(follow_symlinks=False)))
            except OSError:
                self.deny(e.path)
        return out

    def stats(self):
        return dict(partial=self.partial, needsFDA=self.denied_count > 0, deniedCount=self.denied_count,
                    denied=self.denied, otherVolumeSkipped=self.other_volume)


def allowed_devs(root):
    """Volume của gốc; quét "/" thì thêm Data volume (/Users, /Applications… là firmlink sang đó)."""
    devs = set()
    for p in (root, S.DATA_VOL if root == "/" else None):
        try:
            if p:
                devs.add(os.lstat(p).st_dev)
        except OSError:
            pass
    return devs


def canon(path):
    """Đường dẫn tuyệt đối, đã giải symlink của chính gốc (bên trong vẫn không theo symlink)."""
    return os.path.realpath(os.path.abspath(os.path.expanduser(path)))


def user_prune(root, home=None, extra_names=()):
    """Hàm prune(path, name) cho quét file của người dùng (large, dupes): bỏ bundle/thư viện app, thư mục ẩn
    (gồm .git), ~/Library (trừ khi gốc quét nằm trong đó), kho Mail/Messages/Photos và thư mục cloud.
    Trả lý do (chuỗi) hoặc None."""
    home = home or S.HOME
    lib = os.path.join(home, "Library")
    cloud, apple = cloud_roots(home), apple_data(home)

    def prune(path, name):
        low = name.lower()
        if low.endswith(BUNDLE_EXT):
            return "bundle/thư viện của app"
        if name.startswith("."):
            return "thư mục ẩn"
        if name in extra_names:
            return name
        if any(under(path, c) for c in cloud):
            return "thư mục đồng bộ cloud"
        if any(under(path, a) for a in apple):
            return "kho dữ liệu của app Apple"
        if path == lib and not under(root, lib):
            return "~/Library (dữ liệu app)"
        return None
    return prune


def excluded_root(root, home=None):
    """Lý do không quét file người dùng tại gốc này (nằm trong cloud / kho app Apple / bundle), hoặc None."""
    home = home or S.HOME
    if any(under(root, c) for c in cloud_roots(home)):
        return "nằm trong thư mục đồng bộ cloud — xoá ở đây là xoá trên cloud; dùng lệnh `cloud`"
    if any(under(root, a) for a in apple_data(home)):
        return "nằm trong kho dữ liệu của app Apple (Mail/Messages/Photos)"
    if any(part.lower().endswith(BUNDLE_EXT) for part in root.split("/")):
        return "nằm trong bundle/thư viện của app"
    return None


def iter_files(walk, prune):
    """Sinh (path, lstat) của mọi file thường dưới walk.root; không theo symlink, không vượt volume,
    thư mục bị prune thì bỏ cả cây. Dừng sớm khi hết thời hạn (walk.partial)."""
    stack = [walk.root]
    while stack:
        if walk.expired():
            return
        d = stack.pop()
        for e, st in walk.scandir(d):
            if stat.S_ISDIR(st.st_mode):
                if not prune(e.path, e.name) and not walk.skip_dir(e.path, st):
                    stack.append(e.path)
            elif stat.S_ISREG(st.st_mode):
                yield e.path, st


# ---- chính sách I/O theo luồng (macOS): không tải file cloud, không cập nhật atime khi đọc ----
_IOPOL_TYPE_ATIME, _IOPOL_TYPE_MATERIALIZE, _IOPOL_SCOPE_THREAD, _OFF = 2, 3, 1, 1


def _libc():
    try:
        return ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    except OSError:
        return None


@contextlib.contextmanager
def io_policy(no_materialize=True, no_atime=False):
    """Trong khối này, luồng hiện tại không kích hoạt tải file "chỉ còn trên cloud" (đọc nó trả lỗi thay vì tải)
    và (tuỳ chọn) không cập nhật atime. Không có libSystem/hàm → không làm gì."""
    lib, saved = _libc(), []
    fn = getattr(lib, "setiopolicy_np", None) if lib else None
    get = getattr(lib, "getiopolicy_np", None) if lib else None
    if fn and get:
        for typ, on in ((_IOPOL_TYPE_MATERIALIZE, no_materialize), (_IOPOL_TYPE_ATIME, no_atime)):
            if on:
                old = get(typ, _IOPOL_SCOPE_THREAD)
                if old >= 0 and fn(typ, _IOPOL_SCOPE_THREAD, _OFF) == 0:
                    saved.append((typ, old))
    try:
        yield bool(saved)
    finally:
        for typ, old in saved:
            fn(typ, _IOPOL_SCOPE_THREAD, old)
