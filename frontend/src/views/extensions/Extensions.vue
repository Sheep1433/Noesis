<script setup lang="ts">
import { NTabPane, NTabs } from 'naive-ui'
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useBreakpoint } from '@/hooks/useBreakpoint'
import MCPClient from '@/views/mcp/MCPClient.vue'
import SkillsManagement from '@/views/skills/SkillsManagement.vue'

type ExtensionsTab = 'skills' | 'mcp'

const route = useRoute()
const router = useRouter()
const { isMobile } = useBreakpoint()

const activeTab = computed<ExtensionsTab>({
  get() {
    return route.query.tab === 'mcp' ? 'mcp' : 'skills'
  },
  set(tab) {
    void router.replace({
      name: 'Extensions',
      query: tab === 'skills' ? {} : { tab },
    })
  },
})

function onTabUpdate(value: string | number) {
  activeTab.value = value === 'mcp' ? 'mcp' : 'skills'
}
</script>

<template>
  <div class="extensions">
    <header class="extensions-header">
      <h1 class="extensions-title">
        扩展
      </h1>
      <p v-if="!isMobile" class="extensions-subtitle">
        配置 Agent 可用的 Skills 与 MCP Servers；对话中在 Composer 勾选启用。
      </p>
    </header>

    <n-tabs
      :value="activeTab"
      type="line"
      animated
      class="extensions-tabs"
      @update:value="onTabUpdate"
    >
      <n-tab-pane name="skills" tab="Skills" display-directive="show:lazy">
        <SkillsManagement />
      </n-tab-pane>
      <n-tab-pane name="mcp" tab="MCP" display-directive="show:lazy">
        <MCPClient />
      </n-tab-pane>
    </n-tabs>
  </div>
</template>

<style scoped>
.extensions {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  padding: var(--noesis-shell-padding-desktop) 24px 0;
  box-sizing: border-box;
}

.extensions-header {
  flex-shrink: 0;
  margin-bottom: 4px;
}

.extensions-title {
  margin: 0;
  font-size: 22px;
  font-weight: 650;
  letter-spacing: -0.02em;
  color: var(--noesis-color-text-heading, #000);
}

.extensions-subtitle {
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 1.5;
  color: var(--noesis-color-text-muted, #737373);
  max-width: 560px;
}

.extensions-tabs {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.extensions-tabs :deep(.n-tabs-nav) {
  flex-shrink: 0;
}

.extensions-tabs :deep(.n-tabs-pane-wrapper) {
  flex: 1;
  min-height: 0;
}

.extensions-tabs :deep(.n-tab-pane) {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

@media (max-width: 768px) {
  .extensions {
    padding: 8px var(--noesis-content-gutter-mobile) 0;
  }

  .extensions-title {
    font-size: 18px;
  }

  .extensions-header {
    margin-bottom: 0;
  }
}
</style>
