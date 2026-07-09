import type { Ref } from 'vue'
import mermaid from 'mermaid'
import { nextTick, onMounted, onUnmounted, watch } from 'vue'

let initialized = false

function ensureMermaid() {
  if (initialized) {
    return
  }
  mermaid.initialize({
    startOnLoad: false,
    theme: 'neutral',
    securityLevel: 'strict',
    fontFamily: 'inherit',
  })
  initialized = true
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
    ensureMermaid()
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
