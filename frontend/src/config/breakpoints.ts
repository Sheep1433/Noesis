/** 全站统一断点（与 UnoCSS presetWind3 默认 sm/md/lg 对齐，xs 用于超窄屏） */
export const breakpoints = {
  'xs': 480,
  'sm': 640,
  'md': 768,
  'lg': 1024,
  'xl': 1280,
  '2xl': 1536,
} as const

export type BreakpointKey = keyof typeof breakpoints
