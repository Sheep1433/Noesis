/** Unix 毫秒 → HH:MM（24h）。零依赖，沿用原生 Date 风格（参考 TableModal.vue formatTime）。 */
export function formatHHmm(ms?: number): string {
  if (!ms || !Number.isFinite(ms)) {
    return ''
  }
  const d = new Date(ms)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

/** 按整秒展示从开始时间到当前时间的处理时长。 */
export function formatElapsedSeconds(startedAt?: number, now = Date.now()): string {
  if (!startedAt || !Number.isFinite(startedAt) || !Number.isFinite(now)) {
    return ''
  }
  const seconds = Math.max(0, Math.floor((now - startedAt) / 1000))
  return `已处理 ${seconds} 秒`
}
