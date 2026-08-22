<script setup lang="ts">
import type { SettingsSectionId } from './registry'
import { useDialog } from 'naive-ui'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { useBreakpoint } from '@/hooks/useBreakpoint'
import { resolveSettingsSection } from './registry'
import AccountSection from './sections/AccountSection.vue'
import AutomationSection from './sections/AutomationSection.vue'
import CapabilitiesSection from './sections/CapabilitiesSection.vue'
import ChannelsSection from './sections/ChannelsSection.vue'
import DiagnosticsSection from './sections/DiagnosticsSection.vue'
import MemoryEditorSection from './sections/MemoryEditorSection.vue'
import OverviewSection from './sections/OverviewSection.vue'
import PlatformModelsSection from './sections/PlatformModelsSection.vue'
import SettingsNav from './SettingsNav.vue'

const route = useRoute()
const router = useRouter()
const dialog = useDialog()
const { isMobile } = useBreakpoint()

const section = computed<SettingsSectionId>(() => resolveSettingsSection(route.query.s))
const hasUnsavedChanges = ref(false)

function confirmDiscard(): Promise<boolean> {
  if (!hasUnsavedChanges.value) {
    return Promise.resolve(true)
  }
  return new Promise((resolve) => {
    dialog.warning({
      title: '放弃未保存修改？',
      content: '当前设置尚未保存，离开后修改将丢失。',
      positiveText: '放弃修改',
      negativeText: '继续编辑',
      onPositiveClick: () => resolve(true),
      onNegativeClick: () => resolve(false),
      onClose: () => resolve(false),
    })
  })
}

async function onGoto(next: SettingsSectionId) {
  if (next === section.value || !(await confirmDiscard())) {
    return
  }
  hasUnsavedChanges.value = false
  void router.replace({
    name: 'Settings',
    query: next === 'overview' ? {} : { s: next },
  })
}

function onBeforeUnload(event: BeforeUnloadEvent) {
  if (!hasUnsavedChanges.value) {
    return
  }
  event.preventDefault()
  event.returnValue = ''
}

watch(() => route.query.s, (raw) => {
  if (raw !== undefined && resolveSettingsSection(raw) === 'overview' && raw !== 'overview') {
    void router.replace({ name: 'Settings' })
  }
}, { immediate: true })

onMounted(() => window.addEventListener('beforeunload', onBeforeUnload))
onBeforeUnmount(() => window.removeEventListener('beforeunload', onBeforeUnload))
onBeforeRouteLeave(async () => confirmDiscard())
</script>

<template>
  <div class="settings" :class="{ 'settings--mobile': isMobile }">
    <header class="settings-header">
      <h1 class="settings-title">
        设置
      </h1>
      <p class="settings-subtitle">
        个人与 Agent 相关配置
      </p>
    </header>

    <div class="settings-body">
      <SettingsNav :section="section" @select="onGoto" />
      <div class="settings-main">
        <OverviewSection v-if="section === 'overview'" @goto="onGoto" />
        <PlatformModelsSection v-else-if="section === 'models'" />
        <MemoryEditorSection
          v-else-if="section === 'profile'"
          file="USER.md"
          title="画像"
          description="USER.md：用户画像与稳定信息（每会话注入）。"
          @dirty-change="hasUnsavedChanges = $event"
        />
        <MemoryEditorSection
          v-else-if="section === 'memory'"
          file="AGENTS.md"
          title="记忆"
          description="AGENTS.md：跨会话偏好与惯例（每会话注入，注意控长）。"
          @dirty-change="hasUnsavedChanges = $event"
        />
        <CapabilitiesSection v-else-if="section === 'capabilities'" />
        <AutomationSection v-else-if="section === 'automation'" />
        <ChannelsSection v-else-if="section === 'channels'" />
        <DiagnosticsSection v-else-if="section === 'diagnostics'" />
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

.settings--mobile {
  padding: var(--noesis-shell-padding-mobile, 16px);
}

.settings--mobile .settings-body {
  flex-direction: column;
}

.settings-main {
  flex: 1;
  min-width: 0;
  overflow: auto;
}

@media (max-width: $bp-md) {

  .settings {
    padding: var(--noesis-shell-padding-mobile, 16px);
  }

  .settings-body {
    flex-direction: column;
  }
}
</style>
