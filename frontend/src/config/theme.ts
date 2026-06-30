/**
 * 设计 token — TypeScript 侧单源（Naive props、内联 style、UnoCSS 对齐）
 * CSS 变量定义见 styles/tokens/_semantic.scss
 */

export const themeColors = {
  primary: '#6750A4',
  primaryHover: '#5E4896',
  primaryMuted: '#958DA5',
  primarySubtle: '#E8DEF8',
  success: '#51cf66',
  warning: '#fab005',
  danger: '#ff6b6b',
  info: '#868e96',
  bg: '#FFFBFE',
  bgElevated: '#F3EDF7',
  bgMuted: '#E7E0EC',
  border: '#CAC4D0',
  text: '#1C1B1F',
  textHeading: '#1C1B1F',
  textSecondary: '#49454F',
  textBody: '#1C1B1F',
  textTab: '#49454F',
  textNav: '#1C1B1F',
  primaryBorderSoft: '#CAC4D0',
  primaryTextSoft: '#6750A4',
  qaFault: '#e67e22',
  qaTest: '#16a085',
  blockLightIcon: '#6750A4',
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
