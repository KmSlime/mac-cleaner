#!/bin/bash
# PreToolUse (Bash): lệnh XOÁ THẬT / ĐỔI TRẠNG THÁI MÁY của mac-cleaner (clean, uninstall, startup, privacy, maintain,
# self-update, admin kèm --yes), Mole (nếu người dùng tự cài) hay Thùng rác → bắt Claude hỏi người dùng duyệt ("ask"),
# kể cả ở auto mode.
# Lệnh xem trước (không --yes, có --dry-run) đi qua bình thường.
# Không bao giờ chặn cứng: chỉ chuyển quyết định cho người dùng.
input="$(/bin/cat)"

# Lấy lệnh Bash từ payload JSON; không có python3 thì dò trên chuỗi thô (vẫn hỏi được, không "fail-open").
cmd="$(printf '%s' "$input" | /usr/bin/env python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' 2>/dev/null)" \
  || cmd="$input"

reason=""
# Đoạn lệnh mac-cleaner <sub> … (trong cùng một lệnh, không qua ; & |) — `--yes` và động từ có thể ở bất kỳ vị trí nào.
seg() { printf '%s' "$cmd" | /usr/bin/grep -Eo "(mac-cleaner|maccleaner\.py)[^;&|]*[[:space:]]$1([[:space:]][^;&|]*)?" ; }
has() { printf '%s' "$1" | /usr/bin/grep -Eq -- "$2"; }
YES='(^|[[:space:]])--yes([[:space:]=]|$)'
if s="$(seg clean)" && has "$s" "$YES"; then
  reason="mac-cleaner sắp dọn thật (--yes): các mục sẽ bị chuyển vào Thùng rác."
  has "$s" '--force' \
    && reason="mac-cleaner sắp xoá đích danh (--force --yes): gồm cả mục kiểm kê/bảo vệ như app, repo, plugin; nếu có mục Thùng rác thì Thùng rác bị đổ vĩnh viễn."
  has "$s" '--permanent' \
    && reason="mac-cleaner sắp XOÁ HẲN (--permanent --yes): mục loại permanent không qua Thùng rác, không khôi phục được."
elif s="$(seg uninstall)" && has "$s" "$YES"; then
  reason="mac-cleaner sắp gỡ ứng dụng (uninstall --yes): app và file liên quan sẽ vào Thùng rác."
elif s="$(seg startup)" && has "$s" "$YES" && has "$s" '(^|[[:space:]])(disable|enable)([[:space:]]|$)'; then
  reason="mac-cleaner sắp tắt/bật mục khởi động (startup --yes): đổi launchd/login item của máy."
elif s="$(seg privacy)" && has "$s" "$YES" && has "$s" '(^|[[:space:]])reset([[:space:]]|$)'; then
  reason="mac-cleaner sắp reset quyền riêng tư (privacy reset --yes): app sẽ phải xin lại quyền macOS."
elif s="$(seg maintain)" && has "$s" "$YES" && has "$s" '(^|[[:space:]])run([[:space:]]|$)'; then
  reason="mac-cleaner sắp chạy tác vụ bảo trì (maintain run --yes): xoá cache hệ thống cấp user / dựng lại LaunchServices."
elif s="$(seg self-update)" && has "$s" "$YES" && has "$s" '(^|[[:space:]])apply([[:space:]]|$)'; then
  reason="mac-cleaner sắp tự cập nhật (self-update apply --yes): tải bản mới từ GitHub và thay bản đang cài."
elif s="$(seg self-update)" && has "$s" "$YES" && has "$s" '(^|[[:space:]])auto[[:space:]]+on([[:space:]]|$)'; then
  reason="mac-cleaner sắp bật tự kiểm tra bản mới theo lịch (self-update auto on --yes): tạo LaunchAgent, tự kết nối GitHub định kỳ — cần người dùng đồng ý."
elif s="$(seg admin)" && has "$s" "$YES" && has "$s" '(^|[[:space:]])(grant|revoke)([[:space:]]|$)'; then
  reason="mac-cleaner sắp đổi quyền quản trị (admin grant/revoke --yes): sửa /etc/sudoers.d/mac-cleaner, macOS sẽ hỏi mật khẩu quản trị."
elif printf '%s' "$cmd" | /usr/bin/grep -Eq '(^|[;&|[:space:]/"(])(mo|mole)[[:space:]]+(clean|purge|uninstall|optimize|installer|remove)\b' \
     && ! printf '%s' "$cmd" | /usr/bin/grep -Eq -- '--dry-run|(^|[[:space:]])-n([[:space:]]|$)'; then
  reason="Mole (người dùng tự cài) sắp chạy thật (không có --dry-run): có thể xoá cache, file, hoặc gỡ ứng dụng."
elif printf '%s' "$cmd" | /usr/bin/grep -Eqi '\.Trash|empty[[:space:]]+(the[[:space:]]+)?trash'; then
  reason="Lệnh đụng tới Thùng rác: đổ Thùng rác là xoá vĩnh viễn, không khôi phục được."
fi

[ -z "$reason" ] && exit 0
python3 -c 'import json,sys; print(json.dumps({"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":sys.argv[1]}}, ensure_ascii=False))' "$reason" 2>/dev/null \
  || printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"%s"}}\n' "$reason"
exit 0
