<script lang="ts" setup>
import { computed } from 'vue'
import { welcomeGradientStyle } from '@/config/theme'
import { useBreakpoint } from '@/hooks/useBreakpoint'
import { chatModeOption } from '@/utils/qaType'

const props = withDefaults(
  defineProps<{
    /** 当前对话模式对应的内部问答类型 */
    qaType?: string
  }>(),
  { qaType: 'COMMON_QA' },
)

const { isMobile } = useBreakpoint()

const cardOneItems = [
  '① RAG 检索增强，结合知识库精准作答',
  '② 向量检索提升相关片段召回质量',
  '③ 支持多轮上下文与长文本理解',
  '④ 通用办公与技术问题快速解答',
  '⑤ 可扩展对接企业文档与工具链',
]

const cardReportItems = [
  '① 网络检索与多源信息综合',
  '② 适合调研、对比与事实核查类问题',
  '③ 结构化输出便于阅读与引用',
  '④ 与知识库能力协同（按环境配置）',
  '⑤ 适合报告类、深度了解类需求',
]

const cardFaultItems = [
  '① 多步骤分析定位故障根因',
  '② 结合 MCP 工具读日志、执行运维指令',
  '③ 知识库向量检索匹配运维知识',
  '④ 输出可执行的排查与恢复建议',
  '⑤ 适合线上告警、异常与复盘场景',
]

const currentPanel = computed(() => {
  const mode = chatModeOption(props.qaType)
  switch (mode.qaType) {
    case 'SUPER_AGENT_QA':
      return {
        title: '智能体',
        subtitle: '通用超级智能体：调研、检索、分析与多步任务编排',
        items: cardReportItems,
        gradientStyle: welcomeGradientStyle('SUPER_AGENT_QA'),
      }
    case 'FAULT_OPERATION_QA':
      return {
        title: '故障运维',
        subtitle: '面向故障诊断、排查与恢复的专项助手',
        items: cardFaultItems,
        gradientStyle: welcomeGradientStyle('FAULT_OPERATION_QA'),
      }
    case 'COMMON_QA':
    default:
      return {
        title: '智能问答',
        subtitle: '基于 RAG 与向量检索的通用智能问答',
        items: cardOneItems,
        gradientStyle: welcomeGradientStyle('COMMON_QA'),
      }
  }
})

const visibleItems = computed(() => isMobile.value ? currentPanel.value.items.slice(0, 2) : currentPanel.value.items)
</script>

<template>
  <div
    class="welcome-root"
    :class="{ 'welcome-root--mobile': isMobile }"
  >
    <div
      v-if="!isMobile"
      class="welcome-atmosphere"
      aria-hidden="true"
    >
      <div class="welcome-blob welcome-blob--primary"></div>
      <div class="welcome-blob welcome-blob--secondary"></div>
      <div class="welcome-blob welcome-blob--tertiary"></div>
    </div>

    <section
      v-if="isMobile"
      class="mobile-intro"
      :style="currentPanel.gradientStyle"
    >
      <div class="mobile-intro__brand">
        <span class="mobile-intro__brand-mark i-my-svg:system-logo" aria-hidden="true"></span>
        <span>智枢</span>
      </div>
      <h2 class="mobile-intro__title">
        {{ currentPanel.title }}
      </h2>
      <p class="mobile-intro__subtitle">
        {{ currentPanel.subtitle }}
      </p>
      <ul class="mobile-intro__points">
        <li
          v-for="(item, index) in visibleItems"
          :key="index"
          class="mobile-intro__point"
        >
          {{ item }}
        </li>
      </ul>
    </section>

    <header
      v-if="!isMobile"
      class="welcome-header"
    >
      <div class="logo-wrap">
        <div class="brand-mark i-my-svg:system-logo"></div>
      </div>
      <div class="welcome-header-text">
        <h1 class="welcome-title">
          智枢
        </h1>
      </div>
    </header>

    <div
      v-if="!isMobile"
      class="detail-card detail-card--below-header"
      :style="currentPanel.gradientStyle"
    >
      <div class="detail-card__lead">
        <h2 class="detail-card__title">
          {{ currentPanel.title }}
        </h2>
        <p class="detail-card__subtitle">
          {{ currentPanel.subtitle }}
        </p>
      </div>
      <ul
        class="detail-card__points"
      >
        <li
          v-for="(item, index) in currentPanel.items"
          :key="index"
          class="detail-point"
        >
          {{ item }}
        </li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.welcome-root {
  position: relative;
  width: 100%;
  max-width: 960px;
  height: auto;
  min-height: 0;
  margin: 0 auto;
  padding: 16px 20px 24px;
  box-sizing: border-box;
  overflow: hidden;
}

.welcome-atmosphere {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 0;
}

.welcome-blob {
  position: absolute;
  border-radius: 50%;
  filter: blur(64px);
  opacity: 0.22;
}

.welcome-blob--primary {
  top: -12%;
  right: -8%;
  width: 280px;
  height: 280px;
  background: var(--noesis-color-primary);
}

.welcome-blob--secondary {
  top: 28%;
  left: -14%;
  width: 220px;
  height: 220px;
  background: var(--noesis-color-secondary-container);
}

.welcome-blob--tertiary {
  bottom: -6%;
  right: 18%;
  width: 180px;
  height: 180px;
  background: var(--noesis-color-tertiary);
  opacity: 0.14;
}

.welcome-header,
.detail-card {
  position: relative;
  z-index: 1;
}

.welcome-header {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 16px 18px;
  background: var(--noesis-color-bg-elevated);
  border-radius: var(--noesis-radius-xl);
  margin-top: 4%;
  box-shadow: var(--noesis-shadow-sm);
}

.logo-wrap {
  flex-shrink: 0;
  width: 52px;
  height: 52px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.brand-mark {
  width: 52px;
  height: 52px;
  border-radius: 50%;
  overflow: hidden;
  background-repeat: no-repeat;
  background-position: center;
  background-size: contain;
}

.welcome-header-text {
  flex: 1;
  min-width: 0;
  text-align: left;
}

.welcome-title {
  margin: 0;
  font-size: 1.35rem;
  font-weight: 600;
  color: var(--noesis-color-text);
}

.detail-card--below-header {
  margin-top: 16px;
}

.detail-card {
  display: flex;
  flex-direction: row;
  align-items: stretch;
  gap: 20px 28px;
  padding: 20px 22px;
  border-radius: var(--noesis-radius-xl);
  border: none;
  box-shadow: var(--noesis-shadow-sm);
  flex-wrap: wrap;
  height: auto;
  min-height: 0;
  align-content: flex-start;
  transition:
    box-shadow var(--noesis-motion-duration) var(--noesis-motion-ease),
    transform var(--noesis-motion-duration) var(--noesis-motion-ease);
}

.detail-card:hover {
  box-shadow: var(--noesis-shadow-md);
  transform: scale(1.01);
}

.detail-card__lead {
  flex: 0 1 220px;
  min-width: 180px;
}

.detail-card__title {
  margin: 0 0 8px;
  font-size: 1.15rem;
  font-weight: 600;
  color: var(--noesis-color-text-heading);
}

.detail-card__subtitle {
  margin: 0;
  font-size: 0.85rem;
  line-height: 1.55;
  color: var(--noesis-color-text-secondary);
}

@media (prefers-reduced-motion: reduce) {
  .detail-card:hover {
    transform: none;
  }
}

.detail-card__points {
  flex: 1 1 280px;
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 8px 16px;
  align-content: start;
  align-self: flex-start;
}

.detail-point {
  font-size: 0.8rem;
  line-height: 1.45;
  color: var(--noesis-color-text-body);
  padding-left: 0;
}

@media (max-width: 768px) {
  .welcome-root--mobile {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    padding: 24px;
  }
}

.mobile-intro {
  width: min(100%, 440px);
  margin: 0 0 10vh;
  padding: 18px 16px;
  border-radius: var(--noesis-radius-xl);
  box-shadow: var(--noesis-shadow-sm);
  box-sizing: border-box;
  text-align: center;
}

.mobile-intro__brand {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
  color: var(--noesis-color-text-heading);
  font-size: 15px;
  font-weight: 600;
}

.mobile-intro__brand-mark {
  display: inline-block;
  width: 24px;
  height: 24px;
}

.mobile-intro__title {
  margin: 0;
  color: var(--noesis-color-text-heading);
  font-size: 20px;
  font-weight: 600;
}

.mobile-intro__subtitle {
  margin: 8px 0 0;
  color: var(--noesis-color-text-secondary);
  font-size: 14px;
  line-height: 1.55;
}

.mobile-intro__points {
  display: grid;
  gap: 6px;
  margin: 14px 0 0;
  padding: 0;
  color: var(--noesis-color-text-body);
  font-size: 13px;
  line-height: 1.45;
  list-style: none;
}

.mobile-intro__point {
  margin: 0;
}
</style>
