import type { GlobalThemeOverrides } from 'naive-ui'
import { darkTheme, lightTheme } from 'naive-ui'
import { themeColors } from '@/config/theme'
import { PRESET_NAIVE_COLORS } from '@/config/themePresets'
import { useIsDarkThemePreset, useThemePreset } from '@/hooks/useThemePreset'

const baseThemeOverrides: GlobalThemeOverrides = {
  common: {
    borderRadius: '6px',
    heightLarge: '40px',
    fontSizeLarge: '18px',
  },
}

export function useTheme() {
  const { presetId } = useThemePreset()
  const isDark = useIsDarkThemePreset()

  const defaultTheme = computed(() => (isDark.value ? darkTheme : lightTheme))
  const themeRevert = computed(() => (isDark.value ? lightTheme : darkTheme))

  const themeOverrides = computed<GlobalThemeOverrides>(() => {
    const naiveColors = PRESET_NAIVE_COLORS[presetId.value]
    return {
      common: {
        ...baseThemeOverrides.common,
        primaryColor: naiveColors.primary,
        primaryColorHover: naiveColors.primaryHover,
        primaryColorPressed: naiveColors.primaryHover,
        primaryColorSuppl: naiveColors.primaryMuted,
        successColor: themeColors.success,
        warningColor: themeColors.warning,
        errorColor: themeColors.danger,
        infoColor: themeColors.info,
      },
      Input: {
        placeholderColor: naiveColors.textPlaceholder,
      },
    }
  })

  return {
    defaultTheme,
    themeRevert,
    themeOverrides,
  }
}
