#!/usr/bin/env bash
# Noesis 生产看门狗：磁盘水位 + /health 探测，经 Telegram 告警。
# 覆盖两类已发生事故：磁盘满导致 Postgres 崩溃循环；advisory lock 断连
# 静默降级（/health 忠实报 503 但无人看）。
# 状态跃迁立即告警；持续异常每小时重报一次（防单条丢失）。
# 部署于仓库 checkout 之外（/root/zzq/），不受 git reset/clean 影响。
set -u

HEALTH_URL="https://zzqnoesis.cn/health"
DISK_WARN_PCT=85
DISK_CRIT_KB=$((10 * 1024 * 1024))   # 10G
REPEAT_ALERT_SECONDS=3600
STATE_FILE="/var/tmp/noesis-watchdog.state"
LOG_FILE="/var/tmp/noesis-watchdog.log"
ALERT_CONF="/root/zzq/.watchdog.conf"

tg_config() {
  # 独立告警配置（与 Noesis 进程解耦：Noesis 停机不影响告警）。
  # 生成方式：经容器解密 channels.json 的 enc: 密文一次性导出（见决策记录）。
  [ -r "$ALERT_CONF" ] || return 1
  local token chat
  token="$(sed -n 's/^BOT_TOKEN=//p' "$ALERT_CONF")"
  chat="$(sed -n 's/^CHAT_ID=//p' "$ALERT_CONF")"
  [ -n "$token" ] && [ -n "$chat" ] || return 1
  printf '%s\t%s\n' "$token" "$chat"
}

send_alert() {
  local cfg token chat
  cfg="$(tg_config)" || {
    echo "$(date '+%F %T') [watchdog] 告警配置缺失/不可读，仅记录: $1" >> "$LOG_FILE"
    return 1
  }
  token="${cfg%%$'\t'*}"
  chat="${cfg##*$'\t'}"
  curl -s --max-time 10 -X POST "https://api.telegram.org/bot${token}/sendMessage" \
    --data-urlencode "chat_id=${chat}" --data-urlencode "text=${1}" \
    | grep -q '"ok":true' || return 1
}

probe() {
  local avail_kb pct http
  avail_kb="$(df -P / | awk 'NR==2{print $4}')"
  pct="$(df -P / | awk 'NR==2{gsub("%","",$5); print $5}')"
  if [ "${avail_kb:-0}" -lt "$DISK_CRIT_KB" ]; then
    echo "bad:磁盘剩余不足 10G（已用 ${pct}%，剩 $((avail_kb / 1024 / 1024))G）"
    return
  fi
  if [ "${pct:-0}" -ge "$DISK_WARN_PCT" ]; then
    echo "bad:磁盘使用率 ${pct}%（阈值 ${DISK_WARN_PCT}%）"
    return
  fi
  http="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$HEALTH_URL" 2>/dev/null || echo 000)"
  if [ "$http" != "200" ]; then
    echo "bad:/health 探测异常 http=${http}（502=后端停；503=锁丢失降级；000=超时/宕机）"
    return
  fi
  echo "ok"
}

result="$(probe)"
prev="$(head -1 "$STATE_FILE" 2>/dev/null || echo ok)"
last_alert="$(tail -1 "$STATE_FILE" 2>/dev/null || echo 0)"
now="$(date +%s)"
now_bad=0; case "$result" in bad:*) now_bad=1;; esac
prev_bad=0; case "$prev" in bad:*) prev_bad=1;; esac

if [ "$now_bad" -eq 1 ]; then
  should_alert=0
  [ "$prev_bad" -eq 0 ] && should_alert=1
  [ $((now - last_alert)) -ge "$REPEAT_ALERT_SECONDS" ] && should_alert=1
  if [ "$should_alert" -eq 1 ]; then
    send_alert "🚨 Noesis 看门狗: ${result#bad:}
（$(date '+%F %T') · 43.134.128.65）" && last_alert="$now"
  fi
elif [ "$prev_bad" -eq 1 ]; then
  send_alert "✅ Noesis 看门狗: 已恢复正常（$(date '+%F %T')）"
  last_alert="$now"
fi
printf '%s\n%s\n' "$result" "$last_alert" > "$STATE_FILE"
echo "$(date '+%F %T') ${result}" >> "$LOG_FILE"
