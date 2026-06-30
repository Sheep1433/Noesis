/**
 * 设计 token — TypeScript 侧单源（Naive props、内联 style、UnoCSS 对齐）
 * CSS 变量定义见 styles/tokens/_semantic.scss
 */

export const themeColors = {
  primary: '#111111',
  primaryHover: '#000000',
  primaryMuted: '#525252',
  primarySubtle: '#a3a3a3',
  success: '#51cf66',
  warning: '#fab005',
  danger: '#ff6b6b',
  info: '#868e96',
  bg: '#f4f1ea',
  bgElevated: '#faf8f3',
  bgMuted: '#ebe6dc',
  border: '#1a1a1a',
  text: '#111111',
  textHeading: '#000000',
  textSecondary: '#404040',
  textBody: '#262626',
  textTab: '#404040',
  textNav: '#111111',
  primaryBorderSoft: '#d4d0c8',
  primaryTextSoft: '#525252',
  qaFault: '#e67e22',
  qaTest: '#16a085',
  blockLightIcon: '#111111',
  blockDarkIcon: '#8bd9f0',
} as const

/** CSS 变量名（用于 :style 或 getComputedStyle） */
export const themeCssVar = {
  primary: '--noesis-color-primary',
  primaryBorderSoft: '--noesis-color-primary-border-soft',
  primaryTextSoft: '--noesis-color-primary-text-soft',
  bg: '--noesis-color-bg',
  bgElevated: '--noesis-color-bg-elevated',
  bgMuted: '--noesis-color-bg-muted',
  border: '--noesis-color-border',
  text: '--noesis-color-text',
  textNav: '--noesis-color-text-nav',
  textMuted: '--noesis-color-text-muted',
  sidebarBg: '--noesis-sidebar-bg',
} as const

export type QaTypeKey =
  | 'COMMON_QA'
  | 'SUPER_AGENT_QA'
  | 'DEEP_RESEARCH_QA'
  | 'FAULT_OPERATION_QA'
  | 'TEST_CASE_QA'

/** 欢迎页 QA 卡片 — 对应 CSS 变量名 */
export const welcomeGradientVar: Record<QaTypeKey, string> = {
  COMMON_QA: '--noesis-welcome-gradient-common',
  SUPER_AGENT_QA: '--noesis-welcome-gradient-research',
  DEEP_RESEARCH_QA: '--noesis-welcome-gradient-research',
  FAULT_OPERATION_QA: '--noesis-welcome-gradient-fault',
  TEST_CASE_QA: '--noesis-welcome-gradient-test',
}

export function cssVar(name: string): string {
  return `var(${name})`
}

export function welcomeGradientStyle(qaType: string): { background: string } {
  const key = (qaType in welcomeGradientVar ? qaType : 'COMMON_QA') as QaTypeKey
  return { background: cssVar(welcomeGradientVar[key]) }
}
