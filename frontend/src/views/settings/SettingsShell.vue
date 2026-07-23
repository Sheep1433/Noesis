<script setup lang="ts">
import type { SettingsSection } from './SettingsNav.vue'
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AccountSection from './sections/AccountSection.vue'
import AutomationSection from './sections/AutomationSection.vue'
import CapabilitiesSection from './sections/CapabilitiesSection.vue'
import ChannelsSection from './sections/ChannelsSection.vue'
import MemoryEditorSection from './sections/MemoryEditorSection.vue'
import OverviewSection from './sections/OverviewSection.vue'
import SettingsNav from './SettingsNav.vue'

const SECTIONS: SettingsSection[] = [
  'overview',
  'profile',
  'memory',
  'capabilities',
  'automation',
  'channels',
  'account',
]

const route = useRoute()
const router = useRouter()

const section = computed<SettingsSection>({
  get() {
    const s = String(route.query.s || 'overview')
    return (SECTIONS.includes(s as SettingsSection) ? s : 'overview') as SettingsSection
  },
  set(value) {
    void router.replace({
      name: 'Settings',
      query: value === 'overview' ? {} : { s: value },
    })
  },
})

function onGoto(s: SettingsSection) {
  section.value = s
}
</script>

<template>
  <div class="settings">
    <header class="settings-header">
      <h1 class="settings-title">
        设置
      </h1>
      <p class="settings-subtitle">
        个人与 Agent 相关配置
      </p>
    </header>

    <div class="settings-body">
      <SettingsNav v-model:section="section" />
      <div class="settings-main">
        <OverviewSection v-if="section === 'overview'" @goto="onGoto" />
        <MemoryEditorSection
          v-else-if="section === 'profile'"
          file="USER.md"
          title="画像"
          description="USER.md：用户画像与稳定信息（每会话注入）。"
        />
        <MemoryEditorSection
          v-else-if="section === 'memory'"
          file="AGENTS.md"
          title="记忆"
          description="AGENTS.md：跨会话偏好与惯例（每会话注入，注意控长）。"
        />
        <CapabilitiesSection v-else-if="section === 'capabilities'" />
        <AutomationSection v-else-if="section === 'automation'" />
        <ChannelsSection v-else-if="section === 'channels'" />
        <AccountSection v-else-if="section === 'account'" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.settings {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  padding: var(--noesis-shell-padding-desktop);
  box-sizing: border-box;
}

.settings-header {
  margin-bottom: 16px;
}

.settings-title {
  margin: 0;
  font-size: 22px;
  font-weight: 650;
  color: var(--noesis-color-text-heading);
}

.settings-subtitle {
  margin: 6px 0 0;
  color: var(--noesis-color-text-secondary);
  font-size: 13px;
}

.settings-body {
  display: flex;
  gap: 24px;
  min-height: 0;
  flex: 1;
}

.settings-main {
  flex: 1;
  min-width: 0;
  overflow: auto;
}

@media (max-width: 768px) {
  .settings {
    padding: var(--noesis-shell-padding-mobile, 16px);
  }
  .settings-body {
    flex-direction: column;
  }
}
</style>
