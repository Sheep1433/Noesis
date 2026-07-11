import { breakpoints } from '@/config/breakpoints'

const queries = {
  xs: `(max-width: ${breakpoints.xs}px)`,
  sm: `(max-width: ${breakpoints.sm}px)`,
  md: `(max-width: ${breakpoints.md}px)`,
  lg: `(max-width: ${breakpoints.lg}px)`,
  xl: `(max-width: ${breakpoints.xl}px)`,
} as const

/**
 * 响应式断点检测。
 * - isMobile: <= md（768px），侧栏改为底栏 / 抽屉
 * - isTablet: md ~ lg 之间，保留桌面导航与内容布局
 * - isDesktop: > lg
 */
export function useBreakpoint() {
  const isXs = useMediaQuery(queries.xs)
  const isSm = useMediaQuery(queries.sm)
  const isMd = useMediaQuery(queries.md)
  const isLg = useMediaQuery(queries.lg)
  const isXl = useMediaQuery(queries.xl)

  const isMobile = computed(() => isMd.value)
  const isTablet = computed(() => isLg.value && !isMd.value)
  const isDesktop = computed(() => !isLg.value)

  return {
    isXs,
    isSm,
    isMd,
    isLg,
    isXl,
    isMobile,
    isTablet,
    isDesktop,
    breakpoints,
  }
}
