import { ref } from 'vue'

/**
 * 秒级时钟 composable：主聊天 processingNow / 子会话 durationTimer /
 * 任务目录 clockTimer 三处共用（各自的活动条件与副作用留在宿主）。
 *
 * start 幂等（已在跑则只刷新当前值）；stop 清定时器。
 */
export function useTicker(intervalMs = 1000) {
  const now = ref(Date.now())
  let timer: ReturnType<typeof setInterval> | null = null

  function start() {
    now.value = Date.now()
    if (timer !== null) {
      return
    }
    timer = setInterval(() => {
      now.value = Date.now()
    }, intervalMs)
  }

  function stop() {
    if (timer === null) {
      return
    }
    clearInterval(timer)
    timer = null
  }

  return { now, start, stop }
}
