<script lang="ts" setup>
import { computed } from 'vue'
import { welcomeGradientStyle } from '@/config/theme'
import { useBreakpoint } from '@/hooks/useBreakpoint'

const props = withDefaults(
  defineProps<{
    /** 与对话页底部页签一致的问答类型 */
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
  '① 故障诊断智能体多步推理定位根因',
  '② 结合 MCP 工具读日志、执行运维指令',
  '③ 知识库向量检索匹配运维知识',
  '④ 输出可执行的排查与恢复建议',
  '⑤ 适合线上告警、异常与复盘场景',
]

const currentPanel = computed(() => {
  switch (props.qaType) {
    case 'SUPER_AGENT_QA':
    case 'DEEP_RESEARCH_QA':
      return {
        title: '智能体',
        subtitle: '通用超级智能体：调研、检索、分析与多步任务编排',
        items: cardReportItems,
        gradientStyle: welcomeGradientStyle('SUPER_AGENT_QA'),
      }
    case 'FAULT_OPERATION_QA':
      return {
        title: '故障运维',
        subtitle: '基于 LangGraph 与 MCP 的故障诊断与运维智能体',
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

const visibleItems = computed(() => {
  if (!isMobile.value) {
    return currentPanel.value.items
  }
  return currentPanel.value.items.slice(0, 2)
})
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

    <header
      class="welcome-header"
      :class="{ 'welcome-header--mobile': isMobile }"
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
      class="detail-card detail-card--below-header"
      :class="{ 'detail-card--mobile': isMobile }"
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
        :class="{ 'detail-card__points--mobile': isMobile }"
      >
        <li
          v-for="(item, index) in visibleItems"
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
    padding: 4px 10px 8px;
    align-self: flex-start;
    width: 100%;
  }

  .welcome-root--mobile .welcome-header--mobile {
    margin-top: 0;
    gap: 12px;
    padding: 10px 12px;
    border-radius: var(--noesis-radius-lg);
    box-shadow: var(--noesis-shadow-xs, var(--noesis-shadow-sm));
    border: 1px solid var(--noesis-color-border-subtle, rgb(0 0 0 / 5%));
  }

  .welcome-root--mobile .welcome-header--mobile .logo-wrap,
  .welcome-root--mobile .welcome-header--mobile .brand-mark {
    width: 36px;
    height: 36px;
  }

  .welcome-root--mobile .welcome-header--mobile .welcome-title {
    font-size: 1rem;
  }

  .welcome-root--mobile .detail-card--below-header {
    margin-top: 10px;
  }

  .welcome-root--mobile .detail-card--mobile {
    flex: none;
    flex-direction: column;
    align-items: stretch;
    align-content: flex-start;
    gap: 8px;
    height: auto;
    min-height: 0;
    padding: 10px 12px;
    border-radius: var(--noesis-radius-lg);
    box-shadow: var(--noesis-shadow-xs, var(--noesis-shadow-sm));
    border: 1px solid var(--noesis-color-border-subtle, rgb(0 0 0 / 5%));
  }

  .welcome-root--mobile .detail-card--mobile:hover {
    transform: none;
    box-shadow: var(--noesis-shadow-xs, var(--noesis-shadow-sm));
  }

  .welcome-root--mobile .detail-card--mobile .detail-card__lead {
    flex: none;
    min-width: 0;
  }

  .welcome-root--mobile .detail-card__title {
    margin-bottom: 4px;
    font-size: 0.92rem;
    line-height: 1.3;
  }

  .welcome-root--mobile .detail-card__subtitle {
    display: none;
  }

  .welcome-root--mobile .detail-card__points--mobile {
    flex: none;
    display: flex;
    flex-direction: column;
    gap: 4px;
    margin: 0;
    padding: 0;
    list-style: none;
    border-top: none;
  }

  .welcome-root--mobile .detail-card__points--mobile .detail-point {
    font-size: 0.68rem;
    line-height: 1.35;
    color: var(--noesis-color-text-secondary);
  }

  .welcome-root--mobile .detail-card__points--mobile .detail-point:last-child:nth-child(odd) {
    grid-column: auto;
  }
}
</style>
