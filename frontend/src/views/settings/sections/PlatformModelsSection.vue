<script setup lang="ts">
import type { ChatModelCatalog } from '@/api/models'
import { NTag, useMessage } from 'naive-ui'
import { onMounted, ref } from 'vue'
import { getChatModels } from '@/api/models'
import { SettingsEmptyState, SettingsSection, SettingsStatus } from '../primitives'

const message = useMessage()
const loading = ref(false)
const catalog = ref<ChatModelCatalog>()

async function refresh() {
  loading.value = true
  try {
    catalog.value = await getChatModels()
  } catch (error) {
    message.error(error instanceof Error ? error.message : '加载模型目录失败')
  } finally {
    loading.value = false
  }
}

onMounted(() => void refresh())
</script>

<template>
  <SettingsSection title="模型" description="查看当前可用模型。对话使用的模型可在聊天页切换。">
    <SettingsStatus v-if="loading" title="正在加载">
      正在读取模型目录…
    </SettingsStatus>
    <SettingsEmptyState v-else-if="!catalog?.models.length" title="暂无可用模型" description="请联系部署者检查模型配置。" />
    <div v-else class="model-list">
      <div v-for="model in catalog.models" :key="model.id" class="model-row">
        <div>
          <strong>{{ model.label }}</strong>
          <div class="muted">{{ model.model_name }}</div>
        </div>
        <div class="tags">
          <n-tag v-if="model.id === catalog.default_id" size="small" type="success">默认</n-tag>
          <n-tag v-if="model.supports_vision" size="small">视觉</n-tag>
          <n-tag size="small" :bordered="false">{{ model.model_type }}</n-tag>
        </div>
      </div>
    </div>
  </SettingsSection>
</template>

<style scoped>
.model-list { display: grid; gap: 0; max-width: 720px; }
.model-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 14px 0; border-bottom: 1px solid var(--noesis-color-border-subtle, rgba(0,0,0,.08)); }
.muted { margin-top: 4px; color: var(--noesis-color-text-secondary); font-size: 12px; }
.tags { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
</style>
