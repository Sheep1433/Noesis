import { breakpoints } from '@/config/breakpoints'

interface UseResponsiveDrawerWidthOptions {
  /** 桌面端最大宽度 */
  max?: number
  /** 平板端左右留白 */
  tabletGutter?: number
  /** 手机端占视口的比例；默认保留全宽，按需由调用方覆盖 */
  mobileRatio?: number
}

/**
 * 根据视口宽度计算抽屉宽度（参考 DocumentDrawer 阶梯策略）。
 */
export function useResponsiveDrawerWidth(options: UseResponsiveDrawerWidthOptions = {}) {
  const { max = 640, tabletGutter = 24, mobileRatio = 1 } = options
  const { width: windowWidth } = useWindowSize()

  const drawerWidth = computed(() => {
    const w = windowWidth.value
    if (w <= breakpoints.xs) {
      return Math.min(Math.round(w * mobileRatio), max)
    }
    if (w <= breakpoints.md) {
      if (mobileRatio !== 1) {
        return Math.min(Math.round(w * mobileRatio), max)
      }
      return Math.min(w - tabletGutter, max)
    }
    return Math.min(w - tabletGutter * 2, max)
  })

  return { drawerWidth, windowWidth }
}
