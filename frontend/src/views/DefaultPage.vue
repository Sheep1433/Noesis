<script lang="ts" setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { welcomeGradientStyle } from '@/config/theme'

const props = withDefaults(
  defineProps<{
    /** 与对话页底部页签一致的问答类型 */
    qaType?: string
  }>(),
  { qaType: 'COMMON_QA' },
)

const router = useRouter()

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

const cardTestItems = [
  '① 需求与文档解析生成测试用例',
  '② 策略 / 场景 / 用例分层组织',
  '③ 覆盖度分析与补充建议',
  '④ 用例优先级与风险点提示',
  '⑤ 进入独立页面完成生成与导出',
]

const currentPanel = computed(() => {
  switch (props.qaType) {
    case 'DEEP_RESEARCH_QA':
      return {
        title: '深度研究',
        subtitle: '面向调研与报告类问题的检索增强问答',
        items: cardReportItems,
        gradientStyle: welcomeGradientStyle('DEEP_RESEARCH_QA'),
      }
    case 'FAULT_OPERATION_QA':
      return {
        title: '故障运维',
        subtitle: '基于 LangGraph 与 MCP 的故障诊断与运维智能体',
        items: cardFaultItems,
        gradientStyle: welcomeGradientStyle('FAULT_OPERATION_QA'),
      }
    case 'TEST_CASE_QA':
      return {
        title: '测试用例生成',
        subtitle: '基于 Multi-Agent 的用例生成（在独立页面中完成）',
        items: cardTestItems,
        gradientStyle: welcomeGradientStyle('TEST_CASE_QA'),
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

function openTestCasePage() {
  router.push({ name: 'TestCaseGenerate' })
}
</script>

<template>
  <div class="welcome-root">
    <header class="welcome-header">
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
      :style="currentPanel.gradientStyle"
    >
      <div class="detail-card__lead">
        <h2 class="detail-card__title">
          {{ currentPanel.title }}
        </h2>
        <p class="detail-card__subtitle">
          {{ currentPanel.subtitle }}
        </p>
        <p
          v-if="qaType === 'TEST_CASE_QA'"
          class="detail-card__cta"
        >
          <button type="button" class="link-btn" @click="openTestCasePage">
            打开测试用例生成页面 →
          </button>
        </p>
      </div>
      <ul class="detail-card__points">
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
  width: 100%;
  max-width: 960px;
  margin: 0 auto;
  padding: 16px 20px 24px;
  box-sizing: border-box;
}

.welcome-header {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 16px 18px;
  background: var(--noesis-color-bg-elevated);
  border-radius: var(--noesis-radius-lg);
  margin-top: 4%;
  box-shadow: var(--noesis-shadow-md);
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
  border: 1px solid rgb(255 255 255 / 60%);
  box-shadow: var(--noesis-shadow-lg);
  flex-wrap: wrap;
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

.detail-card__cta {
  margin: 12px 0 0;
}

.link-btn {
  padding: 0;
  border: none;
  background: none;
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--noesis-color-primary);
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 3px;
}

.link-btn:hover {
  color: var(--noesis-color-primary-hover);
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
}

.detail-point {
  font-size: 0.8rem;
  line-height: 1.45;
  color: var(--noesis-color-text-body);
  padding-left: 0;
}

@media (max-width: 640px) {
  .detail-card {
    flex-direction: column;
  }

  .detail-card__points {
    grid-template-columns: 1fr;
  }
}
</style>
