<script setup lang="ts">
import type { ChatModelCatalog } from '@/api/models'
import type { UserLLMDiscoveredModel, UserLLMModel, UserLLMProvider } from '@/api/settings'
import { NButton, NInput, NInputNumber, NSelect, NSwitch, NTag, useDialog, useMessage } from 'naive-ui'
import { onMounted, reactive, ref } from 'vue'
import { getChatModels } from '@/api/models'
import {
  createLLMModel, createLLMProvider, deleteLLMModel, deleteLLMProvider,
  discoverLLMProvider, listLLMModels, listLLMProviders, updateLLMModel, updateLLMProvider,
} from '@/api/settings'
import { SettingsEmptyState, SettingsSection, SettingsStatus } from '../primitives'

const message = useMessage()
const dialog = useDialog()
const loading = ref(false)
const catalog = ref<ChatModelCatalog>()
const providers = ref<UserLLMProvider[]>([])
const models = ref<UserLLMModel[]>([])
const discoveryByProvider = ref<Record<string, { models: UserLLMDiscoveredModel[], message: string }>>({})
const discoveringProviderId = ref<string | null>(null)

const apiTypeOptions = [
  { label: 'OpenAI 兼容', value: 'openai' },
  { label: 'DeepSeek', value: 'deepseek' },
  { label: 'Qwen（通义）', value: 'qwen' },
  { label: 'MiniMax', value: 'minimax' },
  { label: 'OpenCode Zen', value: 'opencode' },
]

const providerForm = reactive({
  name: '', api_type: 'openai', base_url: '', api_key: '', enabled: true,
})
const editingProviderId = ref<string | null>(null)

const modelForm = reactive({
  provider_id: '', model_id: '', label: '', temperature: null as number | null, context_window: 0,
})
const editingModelId = ref<string | null>(null)

async function refresh() {
  loading.value = true
  try {
    ;[catalog.value, providers.value, models.value] = await Promise.all([
      getChatModels(), listLLMProviders(), listLLMModels(),
    ])
  } catch (error) {
    message.error(error instanceof Error ? error.message : '加载失败')
  } finally {
    loading.value = false
  }
}

function resetProviderForm() {
  editingProviderId.value = null
  Object.assign(providerForm, { name: '', api_type: 'openai', base_url: '', api_key: '', enabled: true })
}

function editProvider(provider: UserLLMProvider) {
  editingProviderId.value = provider.provider_id
  Object.assign(providerForm, {
    name: provider.name, api_type: provider.api_type, base_url: provider.base_url,
    api_key: '', enabled: provider.enabled,
  })
}

async function saveProvider() {
  if (!providerForm.base_url.trim()) {
    return message.warning('请填写 API 端点地址')
  }
  if (!editingProviderId.value && !providerForm.api_key.trim()) {
    return message.warning('新建服务必须填写 API Key')
  }
  const payload = {
    name: providerForm.name, api_type: providerForm.api_type,
    base_url: providerForm.base_url, enabled: providerForm.enabled,
    api_key: providerForm.api_key, api_key_action: providerForm.api_key.trim() ? 'replace' : 'keep',
  }
  try {
    if (editingProviderId.value) {
      await updateLLMProvider(editingProviderId.value, payload)
    } else {
      await createLLMProvider(payload)
    }
    message.success('模型服务已保存')
    resetProviderForm()
    await refresh()
  } catch (error) {
    message.error(error instanceof Error ? error.message : '保存失败')
  }
}

async function toggleProvider(provider: UserLLMProvider, enabled: boolean) {
  try {
    await updateLLMProvider(provider.provider_id, {
      name: provider.name, api_type: provider.api_type, base_url: provider.base_url,
      enabled, api_key_action: 'keep',
    })
    await refresh()
  } catch (error) {
    message.error(error instanceof Error ? error.message : '更新失败')
  }
}

async function probe(provider: UserLLMProvider) {
  try {
    const result = await discoverLLMProvider(provider.provider_id)
    result.ok ? message.success(result.message) : message.warning(result.message)
    if (result.ok || result.status === 'unsupported') {
      discoveryByProvider.value = {
        ...discoveryByProvider.value,
        [provider.provider_id]: { models: result.models, message: result.message },
      }
    }
  } catch (error) {
    message.error(error instanceof Error ? error.message : '测试失败')
  }
}

async function discover(provider: UserLLMProvider) {
  discoveringProviderId.value = provider.provider_id
  try {
    const result = await discoverLLMProvider(provider.provider_id)
    discoveryByProvider.value = {
      ...discoveryByProvider.value,
      [provider.provider_id]: { models: result.models, message: result.message },
    }
    result.ok ? message.success(result.message) : message.warning(result.message)
  } catch (error) {
    message.error(error instanceof Error ? error.message : '发现模型失败')
  } finally {
    discoveringProviderId.value = null
  }
}

function useDiscoveredModel(provider: UserLLMProvider, discovered: UserLLMDiscoveredModel) {
  resetModelForm()
  Object.assign(modelForm, {
    provider_id: provider.provider_id,
    model_id: discovered.model_id,
    label: discovered.label,
    context_window: discovered.context_window,
  })
  message.info(`已填入「${discovered.model_id}」，确认上下文窗口后保存`)
}

function removeProvider(provider: UserLLMProvider) {
  dialog.warning({
    title: `删除「${provider.name}」？`,
    content: '其下的自定义模型会一并移除。',
    positiveText: '删除', negativeText: '取消',
    async onPositiveClick() {
      await deleteLLMProvider(provider.provider_id)
      message.success('已删除')
      await refresh()
    },
  })
}

function resetModelForm() {
  editingModelId.value = null
  Object.assign(modelForm, {
    provider_id: providers.value[0]?.provider_id || '', model_id: '', label: '',
    temperature: null, context_window: 0,
  })
}

function editModel(model: UserLLMModel) {
  editingModelId.value = model.entry_id
  Object.assign(modelForm, {
    provider_id: model.provider_id, model_id: model.model_id, label: model.label,
    temperature: model.temperature, context_window: model.context_window,
  })
}

async function saveModel() {
  if (!modelForm.provider_id) {
    return message.warning('请选择所属模型服务')
  }
  if (!modelForm.model_id.trim()) {
    return message.warning('请填写模型 ID')
  }
  const payload = {
    provider_id: modelForm.provider_id, model_id: modelForm.model_id, label: modelForm.label,
    temperature: modelForm.temperature, context_window: modelForm.context_window || 0,
  }
  try {
    if (editingModelId.value) {
      await updateLLMModel(editingModelId.value, payload)
    } else {
      await createLLMModel(payload)
    }
    message.success('自定义模型已保存')
    resetModelForm()
    await refresh()
  } catch (error) {
    message.error(error instanceof Error ? error.message : '保存失败')
  }
}

function removeModel(model: UserLLMModel) {
  dialog.warning({
    title: `删除模型「${model.label}」？`,
    positiveText: '删除', negativeText: '取消',
    async onPositiveClick() {
      await deleteLLMModel(model.entry_id)
      message.success('已删除')
      await refresh()
    },
  })
}

onMounted(() => {
  void refresh()
  resetModelForm()
})
</script>

<template>
  <SettingsSection title="模型" description="查看可用模型，并添加自己的模型服务（API Key 加密存储，仅本人可用）。">
    <SettingsStatus v-if="loading" title="正在加载">
      正在读取模型目录…
    </SettingsStatus>
    <template v-else>
      <div v-if="catalog?.models.length" class="model-list">
        <div v-for="model in catalog.models" :key="model.id" class="model-row">
          <div>
            <strong>{{ model.label }}</strong>
            <div class="muted">{{ model.id }}</div>
          </div>
          <div class="tags">
            <n-tag v-if="model.id === catalog.default_id" size="small" type="success">默认</n-tag>
            <n-tag v-if="model.supports_vision" size="small">视觉</n-tag>
            <n-tag v-if="model.custom" size="small" type="info">自定义</n-tag>
            <n-tag size="small" :bordered="false">{{ model.model_type }}</n-tag>
          </div>
        </div>
      </div>
      <SettingsEmptyState v-else title="暂无可用模型" description="可在下方添加自己的模型服务。" />

      <h4 class="sub-title">模型服务</h4>
      <div class="form">
        <n-select v-model:value="providerForm.api_type" :options="apiTypeOptions" placeholder="协议类型" />
        <n-input v-model:value="providerForm.name" placeholder="服务名称（如 我的 DeepSeek）" />
        <n-input v-model:value="providerForm.base_url" placeholder="API 端点（如 https://api.deepseek.com/v1）" />
        <n-input
          v-model:value="providerForm.api_key" type="password" show-password-on="click"
          :placeholder="editingProviderId ? '留空则保留现有 Key' : 'API Key（加密存储）'"
        />
        <label class="enabled"><n-switch v-model:value="providerForm.enabled" /> 启用服务</label>
        <div class="actions">
          <n-button type="primary" @click="saveProvider">{{ editingProviderId ? '保存修改' : '添加模型服务' }}</n-button>
          <n-button v-if="editingProviderId" @click="resetProviderForm">取消</n-button>
        </div>
      </div>

      <div v-for="provider in providers" :key="provider.provider_id" class="channel-card">
        <div class="channel-head">
          <div>
            <strong>{{ provider.name }}</strong>
            <div class="muted">
              {{ provider.api_type }} · {{ provider.base_url }} · {{ provider.has_key ? `Key ${provider.api_key_masked}` : '未配置 Key' }}
            </div>
          </div>
          <div class="actions">
            <n-switch :value="provider.enabled" @update:value="value => toggleProvider(provider, value)" />
            <n-button size="small" :disabled="!provider.has_key" @click="probe(provider)">测试连接</n-button>
            <n-button size="small" :loading="discoveringProviderId === provider.provider_id" :disabled="!provider.has_key" @click="discover(provider)">发现模型</n-button>
            <n-button size="small" @click="editProvider(provider)">编辑</n-button>
            <n-button size="small" type="error" quaternary @click="removeProvider(provider)">删除</n-button>
          </div>
        </div>
        <div v-if="discoveryByProvider[provider.provider_id]" class="discovery-panel">
          <div class="muted">{{ discoveryByProvider[provider.provider_id].message }}。发现结果不会自动保存。</div>
          <div v-if="discoveryByProvider[provider.provider_id].models.length" class="discovery-list">
            <div v-for="discovered in discoveryByProvider[provider.provider_id].models" :key="discovered.model_id" class="discovery-row">
              <div class="discovery-model">
                <strong>{{ discovered.label }}</strong>
                <span class="muted">{{ discovered.model_id }}</span>
                <span class="muted">{{ discovered.context_window ? `${discovered.context_window} tokens` : '窗口未知' }}</span>
              </div>
              <n-button size="small" @click="useDiscoveredModel(provider, discovered)">填入</n-button>
            </div>
          </div>
        </div>
      </div>

      <template v-if="providers.length">
        <h4 class="sub-title">自定义模型</h4>
        <div class="form">
          <n-select
            v-model:value="modelForm.provider_id"
            :options="providers.map(p => ({ label: p.name, value: p.provider_id }))"
            placeholder="所属模型服务"
          />
          <n-input v-model:value="modelForm.model_id" placeholder="模型 ID（发送给端点的名称，如 deepseek-chat）" />
          <n-input v-model:value="modelForm.label" placeholder="显示名称（留空同模型 ID）" />
          <div class="form-row">
            <n-input-number v-model:value="modelForm.context_window" :min="0" placeholder="上下文窗口（token，可选）" style="width: 100%" />
            <n-input-number v-model:value="modelForm.temperature" :min="0" :max="2" :step="0.1" placeholder="温度（可选）" style="width: 100%" />
          </div>
          <div class="actions">
            <n-button type="primary" @click="saveModel">{{ editingModelId ? '保存修改' : '添加自定义模型' }}</n-button>
            <n-button v-if="editingModelId" @click="resetModelForm">取消</n-button>
          </div>
        </div>

        <div v-for="model in models" :key="model.entry_id" class="channel-card">
          <div class="channel-head">
            <div>
              <strong>{{ model.label }}</strong>
              <div class="muted">
                {{ model.model_id }} · {{ model.context_window ? `${model.context_window} tokens` : '窗口未知' }}
                · {{ providers.find(p => p.provider_id === model.provider_id)?.name || '未知服务' }}
              </div>
            </div>
            <div class="actions">
              <n-button size="small" @click="editModel(model)">编辑</n-button>
              <n-button size="small" type="error" quaternary @click="removeModel(model)">删除</n-button>
            </div>
          </div>
        </div>
      </template>
    </template>
  </SettingsSection>
</template>

<style scoped>
.model-list { display: grid; gap: 0; max-width: 720px; }
.model-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 14px 0; border-bottom: 1px solid var(--noesis-color-border-subtle, rgba(0,0,0,.08)); }
.muted { margin-top: 4px; color: var(--noesis-color-text-secondary); font-size: 12px; }
.tags { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
.sub-title { margin: 28px 0 12px; font-size: 14px; }
.form { display: grid; gap: 10px; max-width: 640px; margin-bottom: 16px; }
.form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.enabled, .actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.channel-card { padding: 14px 0; border-top: 1px solid var(--noesis-color-border-subtle, rgba(0,0,0,.08)); max-width: 720px; }
.channel-head { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }
.discovery-panel { margin-top: 12px; padding: 10px 12px; border-radius: 8px; background: var(--noesis-color-fill-subtle, rgba(0,0,0,.03)); }
.discovery-list { display: grid; gap: 6px; margin-top: 8px; }
.discovery-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 8px 0; border-top: 1px solid var(--noesis-color-border-subtle, rgba(0,0,0,.06)); }
.discovery-model { display: flex; align-items: baseline; gap: 10px; min-width: 0; flex-wrap: wrap; }
@media (max-width: $bp-md) {
  .channel-head { flex-direction: column; }
  .discovery-row { align-items: flex-start; }
}
</style>
