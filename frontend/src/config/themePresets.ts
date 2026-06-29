/** 可切换的主题预设 — 视觉差异由 styles/tokens/_presets.scss 的 data-theme 覆盖 */

export type ThemePresetId = 'light' | 'deep' | 'fancy'

export interface ThemePresetOption {
  id: ThemePresetId
  label: string
  description: string
}

/** 浅色 · Swiss Minimalist / Flat Design */
export const THEME_PRESET_OPTIONS: ThemePresetOption[] = [
  {
    id: 'light',
    label: '浅色',
    description: '素净留白 · 柔和蓝 · 日常办公',
  },
  {
    id: 'deep',
    label: '深度',
    description: '暗色沉浸 · 高对比 · 长时间盯屏',
  },
  {
    id: 'fancy',
    label: '花哨',
    description: '渐变撞色 · 粗边框 · 几何活泼',
  },
]

export const DEFAULT_THEME_PRESET: ThemePresetId = 'light'

export const THEME_PRESET_STORAGE_KEY = 'noesis-theme-preset'

/** 旧版预设 id → 新版映射 */
export const LEGACY_THEME_PRESET_MAP: Record<string, ThemePresetId> = {
  default: 'light',
  saas: 'light',
  enterprise: 'deep',
}

/** Naive UI 须用真实 hex（seemly 无法解析 CSS var）— 与 _presets.scss 对齐 */
export interface PresetNaiveColors {
  primary: string
  primaryHover: string
  primaryMuted: string
  primaryBorderSoft: string
  textPlaceholder: string
}

export const PRESET_NAIVE_COLORS: Record<ThemePresetId, PresetNaiveColors> = {
  light: {
    primary: '#5c7cfa',
    primaryHover: '#3d5ae6',
    primaryMuted: '#a48ef4',
    primaryBorderSoft: '#e0dfff',
    textPlaceholder: '#a8aeb8',
  },
  deep: {
    primary: '#818cf8',
    primaryHover: '#6366f1',
    primaryMuted: '#a5b4fc',
    primaryBorderSoft: '#312e81',
    textPlaceholder: '#64748b',
  },
  fancy: {
    primary: '#7c3aed',
    primaryHover: '#6d28d9',
    primaryMuted: '#c084fc',
    primaryBorderSoft: '#f0abfc',
    textPlaceholder: '#a78bfa',
  },
}

export function isDarkThemePreset(id: ThemePresetId): boolean {
  return id === 'deep'
}
