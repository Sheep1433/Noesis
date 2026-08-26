import { useLocalStorage } from '@vueuse/core'
import { computed } from 'vue'

export type ToolDisplayMode = 'compact' | 'verbose'

const STORAGE_KEY = 'noesis-tool-display-mode'
const DEFAULT_MODE: ToolDisplayMode = 'compact'

const stored = useLocalStorage<ToolDisplayMode>(STORAGE_KEY, DEFAULT_MODE)

function normalizeMode(v: unknown): ToolDisplayMode {
  return v === 'verbose' ? 'verbose' : 'compact'
}

const mode = computed<ToolDisplayMode>({
  get: () => normalizeMode(stored.value),
  set: (v) => {
    stored.value = normalizeMode(v)
  },
})

/** 工具调用展示模式：compact=简洁（variant 卡片+跑完收起），verbose=详细（原始命令+完整输出）。 */
export function useToolDisplayMode() {
  return {
    mode,
    isCompact: computed(() => mode.value === 'compact'),
    isVerbose: computed(() => mode.value === 'verbose'),
    toggle() {
      mode.value = mode.value === 'compact' ? 'verbose' : 'compact'
    },
  }
}
