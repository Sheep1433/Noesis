/** Unix 毫秒 → HH:MM（24h）。零依赖，沿用原生 Date 风格（参考 TableModal.vue formatTime）。 */
export function formatHHmm(ms?: number): string {
  if (!ms || !Number.isFinite(ms)) {
    return ''
  }
  const d = new Date(ms)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

/** 按整秒展示从开始时间到当前时间的处理时长，自动转换单位。 */
export function formatElapsedSeconds(startedAt?: number, now = Date.now()): string {
  if (!startedAt || !Number.isFinite(startedAt) || !Number.isFinite(now)) {
    return ''
  }
  const total = Math.max(0, Math.floor((now - startedAt) / 1000))
  return `已处理 ${formatDuration(total)}`
}

/** 将秒数转为人类可读的时长（60秒以下显示秒，以上显示分/小时）。 */
export function formatDuration(seconds: number): string {
  if (seconds < 60) {
    return `${seconds} 秒`
  }
  const minutes = Math.floor(seconds / 60)
  const restSeconds = seconds % 60
  if (minutes < 60) {
    return restSeconds > 0 ? `${minutes} 分 ${restSeconds} 秒` : `${minutes} 分`
  }
  const hours = Math.floor(minutes / 60)
  const restMinutes = minutes % 60
  return restMinutes > 0 ? `${hours} 小时 ${restMinutes} 分` : `${hours} 小时`
}

/** wire 时间戳归一化（秒/毫秒自适应）：无效值返回 undefined。 */
export function wireTimestampMs(value: number | null | undefined): number | undefined {
  if (value == null || !Number.isFinite(value)) {
    return undefined
  }
  return Math.abs(value) < 1e12 ? value * 1000 : value
}
