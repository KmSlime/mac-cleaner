# mclib — quy ước thêm tính năng

`maccleaner.py` giữ phần quét gốc + CLI `scan`/`clean`. Mọi tính năng mới là **một file riêng** trong
`mclib/features/`, nên nhiều người làm song song mà không sửa chung `maccleaner.py`.

```
mclib/safety.py      rào an toàn + dọn (vùng bảo vệ, chặn cứng, app đang chạy, Thùng rác, log, du, preview/--yes/--force)
mclib/registry.py    nạp mclib/features/*, hợp đồng report["features"], entry()/check_entry()
mclib/featclean.py   `clean` cho mục của tính năng (list-only không dọn, permanent cần --permanent)
mclib/features/      mỗi file một tính năng; status.py là mẫu chỉ đọc
```

## Thêm một tính năng

Tạo `mclib/features/<tên>.py` (tên file không bắt đầu bằng `_`). Không cần đăng ký ở đâu khác.

```python
from mclib import registry, safety as S

NAME = "dev-caches"                       # tên lệnh con + khoá report["features"]; mặc định = tên file

def collect(ctx):                         # tuỳ chọn — chạy trong `scan`, sau khi đo dung lượng các mục gốc
    # ctx.items (mục scan gốc, CHỈ ĐỌC) · ctx.log(msg) · ctx.home · ctx.data · ctx.run(cmd) · ctx.du(paths)
    p = ctx.home + "/Library/Caches/com.example.tool"
    return {"summary": {...},             # tự do, miễn JSON được
            "items": [registry.entry(NAME, p, size=ctx.du([p]).get(p), safety="safe",
                                     delete_mode="trash", reason="cache, tự tạo lại")]}

def register(subparsers):                 # tuỳ chọn — thêm lệnh `python3 maccleaner.py dev-caches`
    p = subparsers.add_parser(NAME, help="…")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cli)              # bắt buộc: main() gọi args.func(args)

def cli(args) -> int:
    ...
    return 0
```

- Cần ít nhất một trong `collect` / `register`. Tên `scan`, `clean` và tên trùng bị bỏ qua.
- Module lỗi import hay `collect()` ném lỗi **không làm hỏng** scan: chỉ in cảnh báo / ghi
  `report["features"][NAME] = {"error": "…"}`. Đừng dựa vào điều này — test phải bắt lỗi trước.
- Mỗi kết quả `collect()` được thêm `seconds`.

## Hợp đồng dữ liệu (`report["features"][NAME]["items"][]`)

| Khoá | Ý nghĩa |
|---|---|
| `id` | 10 ký tự hex, ổn định theo (tính năng, path) — `entry()` tự tạo |
| `path` | đường dẫn tuyệt đối |
| `size` | byte hoặc `null` (chưa đo) |
| `safety` | `safe` (dọn không mất gì) · `review` (dọn được, có hệ quả) · `inventory` (chỉ kiểm kê) · `protected` (không bao giờ dọn tự động) |
| `delete_mode` | `trash` (vào Thùng rác — mặc định nên dùng) · `permanent` (xoá hẳn; không được đi với `safe`) · `list-only` (chỉ liệt kê) |
| `reason` | vì sao xếp mức này / xoá thì sao, tiếng Việt |
| `name`, `feature` | `entry()` tự điền; thêm khoá riêng qua `**extra` |

Luôn tạo mục bằng `registry.entry()`: nó kiểm giá trị hợp lệ và **tự hạ** mục nằm ở chỗ chặn cứng (SIP, thư mục gốc,
phần mềm bảo mật — `safety.hard_deny`) hay vùng bảo vệ (`safety.is_protected`, `safety.guard_reason`) xuống
`protected` + `list-only`, kèm lý do. Mục `protected` luôn `list-only`.

Report gốc **không đổi**: `items[]` vẫn dùng định dạng cũ (`safety: safe|review|info|protected`, `action`, `note`);
`info` ở đó tương ứng `inventory`, `action: "trash"` ≈ `delete_mode: "trash"`, `action: null` ≈ `list-only`.
Dashboard/app chưa hiển thị `features`. `clean` dọn cả mục của tính năng (`mclib/featclean.py`): chọn bằng `--ids`,
`--category <tính năng>` hoặc `--category <tính năng>:<kind>` (khoá `kind` qua `**extra`); mặc định chỉ `safe`,
`--include-review` thêm `review`. `list-only`/`protected` **không bao giờ** bị dọn (kể cả `--force`); `permanent` cần
thêm `--permanent`; `trash` đi qua `safety.validate` + Thùng rác + `logs/clean-*.jsonl`. Khai `owners=[bundle id |
tên app]` và/hoặc `lock_dir=<thư mục có SingletonLock>` để `clean` bỏ qua mục khi app chủ đang chạy.

## Luật an toàn bắt buộc

1. **Không sudo**, không tắt SIP, không xin quyền admin. Thiếu quyền thì báo, không lách.
2. **Mặc định xem trước.** Lệnh nào có thể xoá thì không xoá gì nếu thiếu `--yes`; `--force` chỉ cho mục chọn đích danh (`--ids`).
3. **Ưu tiên Thùng rác**: xoá qua `safety.to_trash()` / `safety.clean()`; `permanent` chỉ khi Thùng rác không áp dụng được và phải để `review`.
4. **Kiểm lại lúc xoá**, không tin report: gọi `safety.validate(it, force)`; cache của app đang chạy thì bỏ qua (`safety.cache_owner_running`).
5. Mục **hệ thống / bảo mật / khoá** (SIP, Keychains, `~/.ssh`, `~/.gnupg`, EDR/MDM, ví, trình quản lý mật khẩu) chỉ liệt kê.
6. Xoá thật thì ghi log `logs/clean-<ts>.jsonl` (`safety.clean` đã làm).
7. Chỉ thư viện chuẩn, Python 3.9+ (không `match`, không `X | Y` trong annotation). Lệnh ngoài gọi bằng đường dẫn tuyệt đối (`/usr/sbin/sysctl`…) qua `safety.run` (có timeout, lỗi → chuỗi rỗng).
8. Không ghi đường dẫn/dữ liệu riêng của máy vào code, test hay commit (repo public).

## Viết test

`tests/test_<tên>.py`, chạy `python3 -m unittest discover tests`. Mẫu: `tests/test_features.py`.

- Tách phần **parse** output lệnh ra hàm thuần, test bằng chuỗi mẫu (vd `status.parse_vm_stat`).
- Hàm gọi lệnh nhận `run=` để thay bằng lệnh giả (vd `status.gather(run=fake, now=…)`).
- Test `collect(registry.ReportContext())` trả dict JSON được và mọi `items` qua `registry.check_entry`.
- Có `register`: test `maccleaner.main(["<tên>", ...])` (bắt stdout bằng `contextlib.redirect_stdout`).
- Tính năng có xoá: chỉ test trên `tempfile.TemporaryDirectory()`, kiểm chế độ xem trước không chạm gì.
  Lưu ý: nếu `TMPDIR` nằm trong thư mục ẩn của home thì đường dẫn tạm bị coi là protected.
