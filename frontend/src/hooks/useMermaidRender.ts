import type { Ref } from 'vue'
import { nextTick, onMounted, onUnmounted, watch } from 'vue'

let initialized = false
let mermaidLoader: Promise<typeof import('mermaid').default> | null = null

async function ensureMermaid() {
  if (!mermaidLoader) {
    mermaidLoader = import('mermaid').then((module) => module.default)
  }
  const mermaid = await mermaidLoader
  if (initialized) {
    return mermaid
  }
  mermaid.initialize({
    startOnLoad: false,
    theme: 'neutral',
    securityLevel: 'strict',
    fontFamily: 'inherit',
  })
  initialized = true
  return mermaid
}

export function useMermaidRender(
  containerRef: Ref<HTMLElement | null | undefined>,
  source: Ref<string>,
  enabled: Ref<boolean>,
  debounceMs = 300,
) {
  let renderId = 0
  let debounceTimer: ReturnType<typeof setTimeout> | undefined

  async function renderMermaid() {
    if (!enabled.value) {
      return
    }
    await nextTick()
    const container = containerRef.value
    if (!container) {
      return
    }
    const nodes = Array.from(container.querySelectorAll('.mermaid')) as HTMLElement[]
    if (!nodes.length) {
      return
    }
    const mermaid = await ensureMermaid()
    const currentId = ++renderId
    try {
      await mermaid.run({ nodes })
    } catch (error) {
      if (currentId === renderId) {
        console.warn('[mermaid] render failed', error)
      }
    }
  }

  function scheduleRender() {
    if (debounceTimer) {
      clearTimeout(debounceTimer)
    }
    debounceTimer = setTimeout(() => {
      debounceTimer = undefined
      void renderMermaid()
    }, debounceMs)
  }

  watch([source, enabled], () => {
    scheduleRender()
  }, { flush: 'post' })

  onMounted(() => {
    scheduleRender()
  })

  onUnmounted(() => {
    if (debounceTimer) {
      clearTimeout(debounceTimer)
    }
  })

  return { renderMermaid }
}
