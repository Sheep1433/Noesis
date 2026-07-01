/** 可切换的主题预设 — 视觉差异由 styles/tokens/_presets.scss 的 data-theme 覆盖 */

export type ThemePresetId = 'light' | 'deep' | 'newsprint'

export interface ThemePresetOption {
  id: ThemePresetId
  label: string
  description: string
}

export const THEME_PRESET_OPTIONS: ThemePresetOption[] = [
  {
    id: 'newsprint',
    label: '纸墨',
    description: 'Newsprint · 纸感米色 · 衬线排版 · 报刊质感',
  },
  {
    id: 'light',
    label: '浅色',
    description: 'Flat Design · 几何无阴影 · 蓝绿琥珀色块 · Outfit',
  },
  {
    id: 'deep',
    label: '深色',
    description: 'Minimalist Dark · 石板层叠 · 琥珀光晕 · 玻璃质感',
  },
]

export const DEFAULT_THEME_PRESET: ThemePresetId = 'newsprint'

export const THEME_PRESET_STORAGE_KEY = 'noesis-theme-preset'

/** 旧版预设 id → 新版映射 */
export const LEGACY_THEME_PRESET_MAP: Record<string, ThemePresetId> = {
  default: 'newsprint',
  saas: 'light',
  enterprise: 'deep',
  fancy: 'newsprint',
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
    primary: '#3B82F6',
    primaryHover: '#2563EB',
    primaryMuted: '#60A5FA',
    primaryBorderSoft: '#E5E7EB',
    textPlaceholder: '#9CA3AF',
  },
  deep: {
    primary: '#F59E0B',
    primaryHover: '#FBBF24',
    primaryMuted: '#FCD34D',
    primaryBorderSoft: '#422006',
    textPlaceholder: '#71717A',
  },
  newsprint: {
    primary: '#111111',
    primaryHover: '#000000',
    primaryMuted: '#525252',
    primaryBorderSoft: '#d4d0c8',
    textPlaceholder: '#9ca3af',
  },
}

export function isDarkThemePreset(id: ThemePresetId): boolean {
  return id === 'deep'
}
