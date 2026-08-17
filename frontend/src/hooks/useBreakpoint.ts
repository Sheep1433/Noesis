import { breakpoints } from '@/config/breakpoints'

/**
 * 响应式断点检测。
 * - isMobile: <= md（768px），侧栏改为底栏 / 抽屉
 */
export function useBreakpoint() {
  const isMobile = useMediaQuery(`(max-width: ${breakpoints.md}px)`)
  return { isMobile }
}
