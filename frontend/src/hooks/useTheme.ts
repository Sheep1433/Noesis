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

/** Material You — 色调表面 + 药丸按钮 */
const LIGHT_NAIVE_COMMON: GlobalThemeOverrides['common'] = {
  bodyColor: '#FFFBFE',
  cardColor: '#F3EDF7',
  modalColor: '#F3EDF7',
  popoverColor: '#F3EDF7',
  tableColor: '#FFFBFE',
  borderColor: '#CAC4D0',
  dividerColor: '#E7E0EC',
  textColor1: '#1C1B1F',
  textColor2: '#49454F',
  textColor3: '#79747E',
  hoverColor: 'rgba(103, 80, 164, 0.05)',
  pressedColor: 'rgba(103, 80, 164, 0.08)',
  borderRadius: '16px',
  borderRadiusSmall: '8px',
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
        borderRadiusTiny: '9999px',
        borderRadiusSmall: '9999px',
        borderRadiusMedium: '9999px',
        borderRadiusLarge: '9999px',
        heightMedium: '40px',
      },
      Input: {
        color: '#E7E0EC',
        border: '1px solid transparent',
        borderRadius: '12px',
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
