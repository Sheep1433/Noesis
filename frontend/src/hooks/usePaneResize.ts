import { useLocalStorage } from '@vueuse/core'
import { ref } from 'vue'

export interface UsePaneResizeOptions {
  storageKey: string
  defaultSize: number
  min: number
  max: number
  /** Dragging right decreases size (e.g. left-edge handle on a right-side panel). */
  invertDelta?: boolean
}

export function usePaneResize(options: UsePaneResizeOptions) {
  const size = useLocalStorage(options.storageKey, options.defaultSize)
  const isResizing = ref(false)

  function startResize(event: PointerEvent) {
    if (event.button !== 0) {
      return
    }

    event.preventDefault()
    const target = event.currentTarget as HTMLElement
    target.setPointerCapture(event.pointerId)

    const startX = event.clientX
    const startSize = size.value
    isResizing.value = true
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'

    const onMove = (e: PointerEvent) => {
      const delta = e.clientX - startX
      const adjusted = options.invertDelta ? -delta : delta
      size.value = Math.min(options.max, Math.max(options.min, startSize + adjusted))
    }

    const onEnd = () => {
      isResizing.value = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      target.removeEventListener('pointermove', onMove)
      target.removeEventListener('pointerup', onEnd)
      target.removeEventListener('pointercancel', onEnd)
      try {
        target.releasePointerCapture(event.pointerId)
      } catch {
        // pointer may already be released
      }
    }

    target.addEventListener('pointermove', onMove)
    target.addEventListener('pointerup', onEnd)
    target.addEventListener('pointercancel', onEnd)
  }

  return { size, isResizing, startResize }
}
