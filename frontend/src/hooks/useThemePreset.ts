import { computed } from 'vue'
import { useLocalStorage } from '@vueuse/core'
import {
  DEFAULT_THEME_PRESET,
  LEGACY_THEME_PRESET_MAP,
  PRESET_NAIVE_COLORS,
  THEME_PRESET_OPTIONS,
  THEME_PRESET_STORAGE_KEY,
  type ThemePresetId,
} from '@/config/themePresets'

const VALID_PRESET_IDS = new Set<ThemePresetId>(
  THEME_PRESET_OPTIONS.map(item => item.id),
)

export function normalizeThemePresetId(id: unknown): ThemePresetId {
  if (typeof id === 'string') {
    if (VALID_PRESET_IDS.has(id as ThemePresetId)) {
      return id as ThemePresetId
    }
    const legacy = LEGACY_THEME_PRESET_MAP[id]
    if (legacy) {
      return legacy
    }
  }
  return DEFAULT_THEME_PRESET
}

function syncThemeAttribute(id: ThemePresetId) {
  if (typeof document === 'undefined') {
    return
  }
  const root = document.documentElement
  if (id === DEFAULT_THEME_PRESET) {
    root.removeAttribute('data-theme')
  }
  else {
    root.dataset.theme = id
  }
}

const presetId = useLocalStorage<ThemePresetId>(THEME_PRESET_STORAGE_KEY, DEFAULT_THEME_PRESET)

const currentPresetId = computed({
  get: () => normalizeThemePresetId(presetId.value),
  set: (id: ThemePresetId) => {
    presetId.value = id
    syncThemeAttribute(id)
  },
})

/** 应用启动前调用，避免首屏闪烁 */
export function initThemePreset() {
  const id = normalizeThemePresetId(presetId.value)
  if (id !== presetId.value) {
    presetId.value = id
  }
  syncThemeAttribute(id)
}

export function applyThemePreset(id: ThemePresetId) {
  currentPresetId.value = id
}

export function useThemePreset() {
  return {
    presetId: currentPresetId,
    presets: THEME_PRESET_OPTIONS,
    applyThemePreset,
  }
}

/** Naive 组件 :color / themeOverrides 用 — 随预设切换的 hex 色板 */
export function useNaivePresetColors() {
  const { presetId } = useThemePreset()
  return computed(() => PRESET_NAIVE_COLORS[normalizeThemePresetId(presetId.value)])
}

export function useIsDarkThemePreset() {
  const { presetId } = useThemePreset()
  return computed(() => normalizeThemePresetId(presetId.value) === 'deep')
}
