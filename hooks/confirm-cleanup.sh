#!/bin/bash
# PreToolUse (Bash): lệnh XOÁ THẬT của mac-cleaner / Mole / Thùng rác → bắt Claude hỏi người dùng duyệt
# ("ask"), kể cả ở auto mode. Lệnh xem trước (không --yes, có --dry-run) đi qua bình thường.
# Không bao giờ chặn cứng: chỉ chuyển quyết định cho người dùng.
input="$(/bin/cat)"

# Lấy lệnh Bash từ payload JSON; không có python3 thì dò trên chuỗi thô (vẫn hỏi được, không "fail-open").
cmd="$(printf '%s' "$input" | /usr/bin/env python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' 2>/dev/null)" \
  || cmd="$input"

reason=""
if printf '%s' "$cmd" | /usr/bin/grep -Eq '(mac-cleaner|maccleaner\.py)[^;&|]*[[:space:]]clean[[:space:]][^;&|]*--yes'; then
  reason="mac-cleaner sắp dọn thật (--yes): các mục sẽ bị chuyển vào Thùng rác."
  printf '%s' "$cmd" | /usr/bin/grep -q -- '--force' \
    && reason="mac-cleaner sắp xoá đích danh (--force --yes): gồm cả mục kiểm kê/bảo vệ như app, repo, plugin; nếu có mục Thùng rác thì Thùng rác bị đổ vĩnh viễn."
elif printf '%s' "$cmd" | /usr/bin/grep -Eq '(^|[;&|[:space:]/"(])(mo|mole)[[:space:]]+(clean|purge|uninstall|optimize|installer|remove)\b' \
     && ! printf '%s' "$cmd" | /usr/bin/grep -Eq -- '--dry-run|(^|[[:space:]])-n([[:space:]]|$)'; then
  reason="Mole sắp chạy thật (không có --dry-run): có thể xoá cache, file, hoặc gỡ ứng dụng."
elif printf '%s' "$cmd" | /usr/bin/grep -Eqi '\.Trash|empty[[:space:]]+(the[[:space:]]+)?trash'; then
  reason="Lệnh đụng tới Thùng rác: đổ Thùng rác là xoá vĩnh viễn, không khôi phục được."
fi

[ -z "$reason" ] && exit 0
python3 -c 'import json,sys; print(json.dumps({"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":sys.argv[1]}}, ensure_ascii=False))' "$reason" 2>/dev/null \
  || printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"%s"}}\n' "$reason"
exit 0
