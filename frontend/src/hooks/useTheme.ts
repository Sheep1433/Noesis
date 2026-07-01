import type { GlobalThemeOverrides } from 'naive-ui'
import { darkTheme, lightTheme } from 'naive-ui'
import { themeColors } from '@/config/theme'
import { PRESET_NAIVE_COLORS, type ThemePresetId } from '@/config/themePresets'
import { useIsDarkThemePreset, useThemePreset } from '@/hooks/useThemePreset'

const baseThemeOverrides: GlobalThemeOverrides = {
  common: {
    borderRadius: '8px',
    heightLarge: '40px',
    fontSizeLarge: '18px',
  },
}

/** Flat Design — 纯色块、无阴影、几何圆角 */
const LIGHT_NAIVE_COMMON: GlobalThemeOverrides['common'] = {
  bodyColor: '#FFFFFF',
  cardColor: '#F3F4F6',
  modalColor: '#FFFFFF',
  popoverColor: '#FFFFFF',
  tableColor: '#FFFFFF',
  borderColor: '#E5E7EB',
  dividerColor: '#E5E7EB',
  textColor1: '#111827',
  textColor2: '#4B5563',
  textColor3: '#9CA3AF',
  hoverColor: 'rgba(59, 130, 246, 0.08)',
  pressedColor: 'rgba(59, 130, 246, 0.12)',
  borderRadius: '8px',
  borderRadiusSmall: '6px',
}

/** Minimalist Dark — Naive 暗色面板与玻璃卡片对齐 */
const DEEP_NAIVE_COMMON: GlobalThemeOverrides['common'] = {
  bodyColor: '#0A0A0F',
  cardColor: '#1A1A24',
  modalColor: '#12121A',
  popoverColor: '#1A1A24',
  tableColor: '#12121A',
  borderColor: '#2A2A34',
  dividerColor: '#2A2A34',
  textColor1: '#FAFAFA',
  textColor2: '#A1A1AA',
  textColor3: '#71717A',
  hoverColor: 'rgba(255, 255, 255, 0.05)',
  pressedColor: 'rgba(255, 255, 255, 0.08)',
  borderRadius: '8px',
  borderRadiusSmall: '6px',
}

function presetNaiveOverrides(presetId: ThemePresetId): GlobalThemeOverrides {
  if (presetId === 'light') {
    const naiveColors = PRESET_NAIVE_COLORS.light
    return {
      common: {
        ...LIGHT_NAIVE_COMMON,
        primaryColor: naiveColors.primary,
        primaryColorHover: naiveColors.primaryHover,
        primaryColorPressed: naiveColors.primaryHover,
        primaryColorSuppl: naiveColors.primaryMuted,
      },
      Button: {
        borderRadiusTiny: '6px',
        borderRadiusSmall: '6px',
        borderRadiusMedium: '8px',
        borderRadiusLarge: '8px',
        heightMedium: '40px',
      },
      Input: {
        color: '#F3F4F6',
        border: '2px solid transparent',
        borderRadius: '8px',
        placeholderColor: naiveColors.textPlaceholder,
      },
    }
  }

  if (presetId === 'deep') {
    const naiveColors = PRESET_NAIVE_COLORS.deep
    return {
      common: {
        ...DEEP_NAIVE_COMMON,
        primaryColor: naiveColors.primary,
        primaryColorHover: naiveColors.primaryHover,
        primaryColorPressed: naiveColors.primaryHover,
        primaryColorSuppl: naiveColors.primaryMuted,
      },
      Button: {
        borderRadiusMedium: '12px',
      },
      Input: {
        color: 'rgba(26, 26, 36, 0.6)',
        border: '1px solid rgba(255, 255, 255, 0.08)',
        borderRadius: '12px',
        placeholderColor: naiveColors.textPlaceholder,
      },
    }
  }

  return {}
}

export function useTheme() {
  const { presetId } = useThemePreset()
  const isDark = useIsDarkThemePreset()

  const defaultTheme = computed(() => (isDark.value ? darkTheme : lightTheme))
  const themeRevert = computed(() => (isDark.value ? lightTheme : darkTheme))

  const themeOverrides = computed<GlobalThemeOverrides>(() => {
    const naiveColors = PRESET_NAIVE_COLORS[presetId.value]
    const presetOverrides = presetNaiveOverrides(presetId.value)
    return {
      common: {
        ...baseThemeOverrides.common,
        ...presetOverrides.common,
        primaryColor: presetOverrides.common?.primaryColor ?? naiveColors.primary,
        primaryColorHover: presetOverrides.common?.primaryColorHover ?? naiveColors.primaryHover,
        primaryColorPressed: presetOverrides.common?.primaryColorPressed ?? naiveColors.primaryHover,
        primaryColorSuppl: presetOverrides.common?.primaryColorSuppl ?? naiveColors.primaryMuted,
        successColor: themeColors.success,
        warningColor: themeColors.warning,
        errorColor: themeColors.danger,
        infoColor: themeColors.info,
      },
      Input: {
        ...presetOverrides.Input,
        placeholderColor: naiveColors.textPlaceholder,
      },
      Button: presetOverrides.Button,
    }
  })

  return {
    defaultTheme,
    themeRevert,
    themeOverrides,
  }
}
