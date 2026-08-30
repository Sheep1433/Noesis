import { computed } from 'vue'

/**
 * 待发队列 CRUD（主/子 Agent composer 共用）。
 *
 * 存储适配器注入：主 Agent 传 ref 读写，子 Agent 传 localStorage 模块
 * （跨抽屉开关存活）读写——队列的删除/编辑回填/重排逻辑只有这一份，
 * 两侧不再各自实现数组操作。
 */
export interface FollowupQueueStore {
  /** 队列读取（须响应式：ref.value 或 reactive 容器读取） */
  get(): string[]
  /** 队列整体替换 */
  set(messages: string[]): void
}

export function useFollowupQueue(store: FollowupQueueStore) {
  const messages = computed(() => store.get())

  function remove(index: number): void {
    store.set(messages.value.filter((_, i) => i !== index))
  }

  /** 编辑回填：出队并返回文本，宿主写回自己的输入框 */
  function edit(index: number): string {
    const text = messages.value[index] ?? ''
    remove(index)
    return text
  }

  function reorder(from: number, to: number): void {
    const list = messages.value
    if (from === to || from < 0 || to < 0 || from >= list.length || to >= list.length) {
      return
    }
    const next = [...list]
    const [moved] = next.splice(from, 1)
    next.splice(to, 0, moved)
    store.set(next)
  }

  return { messages, remove, edit, reorder }
}
