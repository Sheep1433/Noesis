import type { ThemePresetId } from '@/config/themePresets'
import { nextTick } from 'vue'
import { THEME_PRESET_SURFACE_BG } from '@/config/themePresets'

export const THEME_TRANSITION_MS = 420

export function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

function setTransitionLock(locked: boolean) {
  if (typeof document === 'undefined') {
    return
  }
  document.documentElement.classList.toggle('noesis-theme-transitioning', locked)
}

function waitForOpacityTransition(el: HTMLElement) {
  return new Promise<void>((resolve) => {
    const timer = window.setTimeout(resolve, THEME_TRANSITION_MS + 80)
    const onEnd = (event: TransitionEvent) => {
      if (event.target === el && event.propertyName === 'opacity') {
        window.clearTimeout(timer)
        el.removeEventListener('transitionend', onEnd)
        resolve()
      }
    }
    el.addEventListener('transitionend', onEnd)
  })
}

async function runOverlayTransition(
  targetId: ThemePresetId,
  apply: () => void,
): Promise<void> {
  const overlay = document.createElement('div')
  overlay.className = 'noesis-theme-transition-overlay'
  overlay.style.backgroundColor = THEME_PRESET_SURFACE_BG[targetId]
  document.body.appendChild(overlay)
  setTransitionLock(true)

  requestAnimationFrame(() => {
    overlay.classList.add('noesis-theme-transition-overlay--active')
  })

  try {
    await waitForOpacityTransition(overlay)
    apply()
    await nextTick()
    overlay.classList.remove('noesis-theme-transition-overlay--active')
    await waitForOpacityTransition(overlay)
  } finally {
    overlay.remove()
    setTransitionLock(false)
  }
}

export async function runThemeTransition(
  targetId: ThemePresetId,
  apply: () => void,
): Promise<void> {
  if (typeof document === 'undefined' || prefersReducedMotion()) {
    apply()
    return
  }

  const doc = document as Document & {
    startViewTransition?: (callback: () => void | Promise<void>) => {
      finished: Promise<void>
    }
  }

  if (doc.startViewTransition) {
    setTransitionLock(true)
    try {
      await doc.startViewTransition(async () => {
        apply()
        await nextTick()
      }).finished
    } catch {
      await runOverlayTransition(targetId, apply)
    } finally {
      setTransitionLock(false)
    }
    return
  }

  await runOverlayTransition(targetId, apply)
}
