<script setup lang="ts">
import type { DiagnosticItem, NotificationPreference } from '@/api/settings'
import { NButton, NInput, NSwitch, NTag, useMessage } from 'naive-ui'
import { onMounted, ref } from 'vue'
import { applySettingsImport, exportSettings, getSettingsDiagnostics, listNotificationPreferences, previewSettingsImport, resetSettings, updateNotificationPreference } from '@/api/settings'
import { SettingsDangerAction, SettingsSection } from '../primitives'

const message = useMessage()
const notifications = ref<NotificationPreference[]>([])
const diagnostics = ref<DiagnosticItem[]>([])
const importText = ref('')
const preview = ref<{ preview_id: string, changes: Array<{ domain: string, action: string }> }>()
let importManifest: Record<string, unknown> | undefined

async function load() {
  notifications.value = await listNotificationPreferences()
  diagnostics.value = (await getSettingsDiagnostics()).items
}

async function toggle(item: NotificationPreference, enabled: boolean) {
  await updateNotificationPreference(item, enabled)
  await load()
}

async function downloadExport() {
  const data = await exportSettings()
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = 'noesis-settings.json'
  anchor.click()
  URL.revokeObjectURL(url)
}

async function runPreview() {
  try {
    importManifest = JSON.parse(importText.value)
    preview.value = await previewSettingsImport(importManifest!)
  } catch (error) {
    message.error(error instanceof Error ? error.message : '无法读取设置文件')
  }
}

async function applyImport() {
  if (!preview.value || !importManifest) {
    return
  }
  await applySettingsImport(importManifest, preview.value.preview_id)
  message.success('导入完成')
  preview.value = undefined
  await load()
}

async function reset() {
  await resetSettings()
  message.success('已恢复默认设置')
  await load()
}

function tone(status: DiagnosticItem['status']) {
  return status === 'healthy' ? 'success' : status === 'unavailable' ? 'error' : 'warning'
}

onMounted(() => void load())
</script>

<template>
  <SettingsSection title="通知" description="选择哪些事件需要在网页或已配置通道中提醒你。">
    <div v-for="item in notifications" :key="`${item.event_type}-${item.delivery_surface}`" class="row">
      <span>{{ item.event_type }} · {{ item.delivery_surface }}</span><n-switch :value="item.enabled" @update:value="value => toggle(item, value)" />
    </div>
  </SettingsSection>
  <SettingsSection title="系统诊断" description="查看各项能力是否可用；单项异常不会影响其它检查。">
    <n-button @click="load">重新检查</n-button>
    <div v-for="item in diagnostics" :key="item.key" class="diagnostic">
      <div><strong>{{ item.key }}</strong><p>{{ item.message }} · 关联 ID {{ item.correlation_id }}</p></div><n-tag :type="tone(item.status)">{{ item.status }}</n-tag>
    </div>
  </SettingsSection>
  <SettingsSection title="设置迁移" description="导出不含密钥的设置，或先预览再导入。">
    <n-button @click="downloadExport">导出设置</n-button>
    <n-input v-model:value="importText" type="textarea" :autosize="{ minRows: 8, maxRows: 18 }" placeholder="粘贴设置文件内容" />
    <div class="actions"><n-button @click="runPreview">预览导入</n-button><n-button v-if="preview" type="primary" @click="applyImport">确认导入 {{ preview.changes.length }} 个设置域</n-button></div>
    <SettingsDangerAction title="恢复默认通知设置？" description="这会清除已保存的通知偏好，其它设置保持不变。" confirm-label="恢复默认" @confirm="reset" />
  </SettingsSection>
</template>

<style scoped>
.row, .diagnostic { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--noesis-color-border-subtle, rgba(0,0,0,.08)); }
.diagnostic p { margin: 3px 0 0; color: var(--noesis-color-text-secondary); font-size: 12px; }
.actions { display: flex; gap: 8px; margin: 10px 0; }
</style>
