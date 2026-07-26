<script setup lang="ts">
import type { ModelPurpose, ModelPurposeBinding, ProviderConnection, ProviderModel } from '@/api/settings'
import { NButton, NInput, NSelect, NSwitch, NTag, useDialog, useMessage } from 'naive-ui'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import {
  bindModel, createProvider, deleteProvider, discoverProviderModels,
  listModelBindings, listProviders, testProvider, updateProvider,
} from '@/api/settings'
import { SettingsEmptyState, SettingsSection, SettingsStatus } from '../primitives'

const emit = defineEmits<{ (event: 'dirtyChange', value: boolean): void }>()
const message = useMessage()
const dialog = useDialog()
const loading = ref(false)
const saving = ref(false)
const providers = ref<ProviderConnection[]>([])
const bindings = ref<ModelPurposeBinding[]>([])
const models = reactive<Record<string, ProviderModel[]>>({})
const selectedProviders = reactive<Partial<Record<ModelPurpose, string>>>({})
const editingId = ref<string | null>(null)
const form = reactive({ provider_type: 'openai' as ProviderConnection['provider_type'], display_name: '', base_url: 'https://api.openai.com/v1', api_key: '', enabled: true })
const purposes: ModelPurpose[] = ['chat', 'vision', 'embedding', 'rerank']
const dirty = computed(() => Boolean(form.display_name || form.api_key || editingId.value))
watch(dirty, (value) => emit('dirtyChange', value), { immediate: true })

const providerOptions = computed(() => providers.value.filter((item) => item.enabled).map((item) => ({ label: item.display_name, value: item.id })))

async function refresh() {
  loading.value = true
  try {
    [providers.value, bindings.value] = await Promise.all([listProviders(), listModelBindings()])
    for (const binding of bindings.value) {
      selectedProviders[binding.purpose] = binding.provider_id
    }
    await Promise.all([...new Set(bindings.value.map((binding) => binding.provider_id))].map(async (providerId) => {
      try {
        models[providerId] = await discoverProviderModels(providerId)
      } catch {
        models[providerId] = []
      }
    }))
  } catch (error) {
    message.error(error instanceof Error ? error.message : '加载模型设置失败')
  } finally {
    loading.value = false
  }
}

function resetForm() {
  editingId.value = null
  Object.assign(form, { provider_type: 'openai', display_name: '', base_url: 'https://api.openai.com/v1', api_key: '', enabled: true })
}

function edit(item: ProviderConnection) {
  editingId.value = item.id
  Object.assign(form, { provider_type: item.provider_type, display_name: item.display_name, base_url: item.base_url, api_key: '', enabled: item.enabled })
}

async function save() {
  saving.value = true
  try {
    const current = providers.value.find((item) => item.id === editingId.value)
    if (current) {
      await updateProvider(current, { ...form, secret: form.api_key ? { action: 'replace', value: form.api_key } : { action: 'keep' } })
    } else {
      await createProvider({ ...form, secret: form.api_key ? { action: 'replace', value: form.api_key } : { action: 'keep' } })
    }
    message.success('Provider 已保存')
    resetForm()
    await refresh()
  } catch (error) {
    message.error(error instanceof Error ? error.message : '保存失败')
  } finally {
    saving.value = false
  }
}

function confirmDelete(item: ProviderConnection) {
  dialog.warning({ title: `删除 ${item.display_name}？`, content: '关联的默认模型绑定也会被移除。此操作无法撤销。', positiveText: '删除', negativeText: '取消',
    async onPositiveClick() {
      await deleteProvider(item.id)
      message.success('已删除')
      await refresh()
    } })
}

async function probe(item: ProviderConnection) {
  try {
    const result = await testProvider(item.id)
    result.ok ? message.success(`${result.message}，发现 ${result.model_count} 个模型`) : message.warning(result.message)
  } catch (error) {
    message.error(error instanceof Error ? error.message : '连接测试失败')
  }
}

async function loadModels(providerId: string) {
  try {
    models[providerId] = await discoverProviderModels(providerId)
  } catch (error) {
    models[providerId] = []
    message.error(error instanceof Error ? error.message : '模型发现失败')
  }
}

function currentBinding(purpose: ModelPurpose) {
  return bindings.value.find((item) => item.purpose === purpose)
}
async function chooseProvider(purpose: ModelPurpose, providerId: string) {
  selectedProviders[purpose] = providerId
  if (!models[providerId]) {
    await loadModels(providerId)
  }
  if (!models[providerId]?.some((item) => item.capabilities.includes(purpose))) {
    return message.warning(`该 Provider 未发现支持 ${purpose} 的模型`)
  }
}
function modelOptions(purpose: ModelPurpose) {
  const providerId = selectedProviders[purpose]
  return providerId
    ? (models[providerId] || []).filter((model) => model.capabilities.includes(purpose)).map((model) => ({ label: model.name, value: model.id }))
    : []
}
async function chooseModel(purpose: ModelPurpose, modelId: string) {
  const providerId = selectedProviders[purpose]
  const model = providerId ? models[providerId]?.find((item) => item.id === modelId) : undefined
  if (!providerId || !model) {
    return
  }
  await bindModel(purpose, providerId, model)
  message.success(`${purpose} 默认模型已更新`)
  bindings.value = await listModelBindings()
}

onMounted(() => void refresh())
</script>

<template>
  <SettingsSection title="模型与 Provider" description="管理连接并为聊天、视觉、Embedding 和 Rerank 选择默认模型。">
    <SettingsStatus v-if="loading" title="正在加载">
      正在读取 Provider 与默认模型…
    </SettingsStatus>
    <div class="form-grid">
      <n-select v-model:value="form.provider_type" :options="['openai', 'deepseek', 'qwen', 'minimax', 'opencode'].map(value => ({ label: value, value }))" />
      <n-input v-model:value="form.display_name" placeholder="显示名称" />
      <n-input v-model:value="form.base_url" placeholder="Base URL" />
      <n-input v-model:value="form.api_key" type="password" show-password-on="click" :placeholder="editingId ? '留空则保留现有密钥' : 'API Key（可稍后配置）'" />
      <label class="switch"><n-switch v-model:value="form.enabled" /> 启用</label>
      <div class="actions"><n-button type="primary" :loading="saving" :disabled="!form.display_name || !form.base_url" @click="save">{{ editingId ? '保存修改' : '添加 Provider' }}</n-button><n-button v-if="editingId" @click="resetForm">取消</n-button></div>
    </div>

    <SettingsEmptyState v-if="!loading && providers.length === 0" title="尚未配置 Provider" description="添加一个连接后即可测试并发现模型。" />
    <div v-for="item in providers" :key="item.id" class="provider-row">
      <div><strong>{{ item.display_name }}</strong><div class="muted">{{ item.provider_type }} · {{ item.base_url }}</div><n-tag size="small" :type="item.enabled ? 'success' : 'default'">{{ item.enabled ? '已启用' : '已停用' }}</n-tag> <n-tag size="small">{{ item.secret.configured ? `密钥 ····${item.secret.suffix || ''}` : '未配置密钥' }}</n-tag></div>
      <div class="actions"><n-button size="small" @click="probe(item)">测试连接</n-button><n-button size="small" @click="loadModels(item.id)">发现模型</n-button><n-button size="small" @click="edit(item)">编辑</n-button><n-button size="small" type="error" quaternary @click="confirmDelete(item)">删除</n-button></div>
      <div v-if="models[item.id]?.length" class="model-list">{{ models[item.id].map(model => model.name).join('、') }}</div>
    </div>

    <h3>默认模型用途</h3>
    <div v-for="purpose in purposes" :key="purpose" class="binding-row">
      <div><strong>{{ purpose }}</strong><div class="muted">{{ currentBinding(purpose)?.model_name || '沿用平台默认' }}</div></div>
      <div class="binding-selects">
        <n-select :value="selectedProviders[purpose] || null" clearable placeholder="选择 Provider" :options="providerOptions" @update:value="value => value && chooseProvider(purpose, value)" />
        <n-select :value="currentBinding(purpose)?.model_id || null" :disabled="!selectedProviders[purpose]" placeholder="选择兼容模型" :options="modelOptions(purpose)" @update:value="value => value && chooseModel(purpose, value)" />
      </div>
    </div>
  </SettingsSection>
</template>

<style scoped>
.form-grid { display: grid; gap: 10px; max-width: 620px; margin-bottom: 24px; }
.provider-row, .binding-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; padding: 14px 0; border-bottom: 1px solid var(--noesis-color-border-subtle, rgba(0,0,0,.08)); }
.actions, .switch { display: flex; align-items: center; gap: 8px; }
.muted, .model-list { margin-top: 4px; color: var(--noesis-color-text-secondary); font-size: 12px; }
.model-list { flex-basis: 100%; }
.binding-selects { display: grid; grid-template-columns: minmax(150px, 1fr) minmax(180px, 1fr); gap: 8px; width: min(520px, 100%); }
h3 { margin: 28px 0 4px; color: var(--noesis-color-text-heading); }
</style>
