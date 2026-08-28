<script setup lang="ts">
import type { ChatModelCatalog, ChatModelOption, DiscoveredModelRow, ProviderPreset } from '@/api/models'
import type { UserLLMDiscoveredModel, UserLLMModel, UserLLMProvider } from '@/api/settings'
import { NButton, NInput, NInputNumber, NSelect, NSwitch, NTag, useDialog, useMessage } from 'naive-ui'
import { computed, onMounted, reactive, ref } from 'vue'
import { discoverPlatformModels, getChatModels } from '@/api/models'
import {
  createLLMModel, createLLMProvider, deleteLLMModel, deleteLLMProvider,
  discoverLLMDraft, listLLMModels, listLLMProviders, setLLMDefaultModel,
  updateLLMModel, updateLLMProvider,
} from '@/api/settings'
import { reasoningLevelOptions } from '@/utils/reasoningLevels'
import { SettingsEmptyState, SettingsSection, SettingsStatus } from '../primitives'
import ModelDiscoveryPanel from './ModelDiscoveryPanel.vue'

const message = useMessage()
const dialog = useDialog()
const loading = ref(false)
const saving = ref(false)
const catalog = ref<ChatModelCatalog>()
const providers = ref<UserLLMProvider[]>([])
const models = ref<UserLLMModel[]>([])
/** 草案发现结果（deepseek-harness 机制）：仅前端候选，不落库 */
const draftDiscovery = ref<{ models: UserLLMDiscoveredModel[], message: string } | null>(null)
const discovering = ref(false)

/**
 * 协议收敛（dsh 模式）：自定义 Provider 一律 OpenAI 兼容；
 * 特定提供商由平台预设目录承载（选预设 → base_url 自动填）。
 */
const apiTypeOptions = [{ label: 'OpenAI 兼容', value: 'openai' }]
/** 编辑 legacy 类型 Provider 时追加其现有类型，避免保存时被静默改写成 openai */
const effectiveApiTypeOptions = computed(() => {
  const current = providerForm.api_type
  return apiTypeOptions.some((o) => o.value === current)
    ? apiTypeOptions
    : [...apiTypeOptions, { label: `${current}（历史类型）`, value: current }]
})

/** 平台预设目录（config.yaml model.provider_presets） */
const providerPresets = ref<ProviderPreset[]>([])
/** 内置目录的平台 Provider（按 provider 分组展示的头卡） */
const platformProvider = ref<{ id: string, label: string, base_url: string } | null>(null)
const platformModels = ref<ChatModelOption[]>([])
/** 平台发现结果（多选与筛选在 ModelDiscoveryPanel 内） */
const platformDiscovery = ref<{ models: DiscoveredModelRow[], message: string } | null>(null)
const discoveringPlatform = ref(false)
/** 分组折叠：默认收起，点 Provider 行展开模型列表 */
const expandedGroups = ref(new Set<string>())
const adoptingPlatform = ref(false)

function toggleGroup(key: string) {
  const next = new Set(expandedGroups.value)
  if (next.has(key)) {
    next.delete(key)
  } else {
    next.add(key)
  }
  expandedGroups.value = next
}

/** 平台组承载采纳模型的用户级 Provider（slug = 平台 id；首次采纳时自动创建） */
const platformProviderRow = computed(() =>
  providers.value.find((p) => p.slug === platformProvider.value?.id))

/** 平台组展示行：内置目录条目 + 用户在该组下采纳的模型，合并为一个列表 */
interface PlatformGroupRow {
  key: string
  /** 裸 model_id：发现列表比对与「已下线」判定用 */
  model_id: string
  label: string
  /** 目录身份：内置条目为裸 id，采纳条目为复合 id（设为默认 / 会话切换用） */
  catalog_id: string
  isDefault: boolean
  contextWindow: number
  supportsVision: boolean
  offline: boolean
  /** 推理档位能力声明；空=未声明 */
  reasoningLevels: string[]
}

/** 最近一次发现返回的 model_id 集；null = 本会话未发现过，不做下线判定 */
const platformDiscoveredIds = computed(() =>
  platformDiscovery.value ? new Set(platformDiscovery.value.models.map((m) => m.model_id)) : null)

const mergedPlatformRows = computed<PlatformGroupRow[]>(() => {
  const discovered = platformDiscoveredIds.value
  const yamlRows = platformModels.value.map((m) => ({
    key: `yaml-${m.id}`,
    model_id: m.id,
    label: m.label,
    catalog_id: m.id,
    isDefault: m.id === catalog.value?.default_id,
    contextWindow: m.context_window || 0,
    supportsVision: !!m.supports_vision,
    offline: discovered ? !discovered.has(m.id) : false,
    reasoningLevels: m.reasoning_levels || [],
  }))
  const owner = platformProviderRow.value
  const adoptedRows = owner
    ? models.value
        .filter((m) => m.provider_id === owner.provider_id)
        .map((m) => ({
          key: `adopted-${m.entry_id}`,
          model_id: m.model_id,
          label: m.label,
          catalog_id: compositeModelId(owner, m),
          isDefault: compositeModelId(owner, m) === catalog.value?.default_id,
          contextWindow: m.context_window || 0,
          supportsVision: false,
          offline: discovered ? !discovered.has(m.model_id) : false,
          reasoningLevels: m.reasoning_levels || [],
        }))
    : []
  return [...yamlRows, ...adoptedRows]
})

/** 独立展示的自定义 Provider：其采纳模型已并入平台组的同名 Provider 不再单列 */
const customProviders = computed(() =>
  providers.value.filter((p) => p.slug !== platformProvider.value?.id))

/** 发现面板的去重依据：平台组已有条目（内置 + 已采纳）的 model_id */
const platformExistingIds = computed(() =>
  new Set(mergedPlatformRows.value.map((row) => row.model_id)))

const presetOptions = computed(() => [
  { label: '自定义', value: '' },
  ...providerPresets.value.map((p) => ({ label: p.label, value: p.id })),
])

/**
 * 提供方切换（dsh 机制）：选择是显式动作，选中即载入该提供方的端点，
 * 再选别的就整体换——不存在「只填空字段」导致切不动的问题。
 * 名称仅作建议填入（用户改过则保留）；「自定义」保持现值手填。
 */
const normalizeUrl = (url: string) => url.trim().replace(/\/+$/, '')

function onPresetChange(presetId: string) {
  providerForm.preset_id = presetId
  const preset = providerPresets.value.find((p) => p.id === presetId)
  if (!preset) {
    // 切到「自定义」：清空上一轮预设填入的默认值，从头手填（编辑态保留现值）
    if (!editingProviderId.value) {
      Object.assign(providerForm, { slug: '', name: '', base_url: '' })
      slugTouched.value = false
    }
    return
  }
  providerForm.base_url = normalizeUrl(preset.base_url)
  if (!providerForm.name.trim()) {
    providerForm.name = preset.label
  }
  if (!editingProviderId.value) {
    // 切换提供方 = 换身份：ID 直接跟随预设（编辑已有 Provider 则保留其 ID）；
    // 同时定形（slugTouched），后续显示名编辑不再自动覆盖
    providerForm.slug = preset.id
    slugTouched.value = true
    // 预设声明了推理档位 → 预填到尚未落库的新行（仅预填，可改）
    const presetLevels = preset.reasoning_levels
    if (Array.isArray(presetLevels) && presetLevels.length > 0) {
      for (const row of draftModels.value) {
        if (!row.entry_id && row.reasoning_levels.length === 0) {
          row.reasoning_levels = [...presetLevels]
        }
      }
    }
  }
}

/** 推理档位多选选项（关/低/中/高/最高） */
const reasoningSelectOptions = reasoningLevelOptions()

/** 表单内模型目录行：entry_id 为空 = 尚未落库的新行 */
interface DraftModelRow {
  entry_id: string | null
  model_id: string
  label: string
  context_window: number
  /** 推理档位能力声明（off/low/medium/high/max 子集）；空=未声明 */
  reasoning_levels: string[]
}


const providerForm = reactive({
  /** 提供方选择（dsh 机制：选择即表单状态，'' = 自定义手填） */
  preset_id: '',
  slug: '', name: '', api_type: 'openai', base_url: '', api_key: '', enabled: true,
})
const draftModels = ref<DraftModelRow[]>([])
const removedModelIds = ref<string[]>([])
const editingProviderId = ref<string | null>(null)
/** 表单默认隐藏：由「添加提供方」按钮或编辑操作唤起，避免常驻表单干扰视线 */
const providerFormVisible = ref(false)

function showProviderForm() {
  providerFormVisible.value = true
}

function hideProviderForm() {
  providerFormVisible.value = false
  resetProviderForm()
}

const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$|^[a-z0-9]$/

const slugTouched = ref(false)

/** 从显示名推导 slug 建议：仅取 ASCII 字母数字段，中文等返回空 */
function suggestSlug(name: string): string {
  const ascii = name.toLowerCase().match(/[a-z0-9]+/g)?.join('-') || ''
  if (!ascii) {
    return ''
  }
  return ascii.slice(0, 40).replace(/^-+|-+$/g, '')
}

/** 手动改地址偏离当前预设时，提供方选择回落为「自定义」，展示与实际一致 */
function onBaseUrlInput(value: string) {
  providerForm.base_url = value
  const preset = providerPresets.value.find((p) => p.id === providerForm.preset_id)
  if (preset && normalizeUrl(value) !== normalizeUrl(preset.base_url)) {
    providerForm.preset_id = ''
  }
}

/** 手工编辑过 slug（或进入编辑态）后，显示名不再覆盖 Provider ID */
function onSlugInput(value: string) {
  providerForm.slug = value
  slugTouched.value = true
}

/** 创建/保存按钮的激活条件（对齐 dsh：地址 + Key（新建）+ 至少一个模型） */
const canSubmit = computed(() => {
  if (!SLUG_RE.test(providerForm.slug.trim())) {
    return false
  }
  if (!providerForm.base_url.trim()) {
    return false
  }
  if (!editingProviderId.value && !providerForm.api_key.trim()) {
    return false
  }
  return draftModels.value.length > 0
    && draftModels.value.every((row) => row.model_id.trim())
})

async function refresh() {
  loading.value = true
  try {
    ;[catalog.value, providers.value, models.value] = await Promise.all([
      getChatModels(), listLLMProviders(), listLLMModels(),
    ])
    providerPresets.value = catalog.value?.provider_presets || []
    platformProvider.value = catalog.value?.platform_provider || null
    platformModels.value = (catalog.value?.models || []).filter((m) => !m.custom)
  } catch (error) {
    message.error(error instanceof Error ? error.message : '加载失败')
  } finally {
    loading.value = false
  }
}

function resetProviderForm() {
  editingProviderId.value = null
  slugTouched.value = false
  Object.assign(providerForm, { preset_id: '', slug: '', name: '', api_type: 'openai', base_url: '', api_key: '', enabled: true })
  draftModels.value = []
  removedModelIds.value = []
  draftDiscovery.value = null
}

function editProvider(provider: UserLLMProvider) {
  resetProviderForm()
  providerFormVisible.value = true
  slugTouched.value = true
  editingProviderId.value = provider.provider_id
  Object.assign(providerForm, {
    preset_id: providerPresets.value.find((p) => normalizeUrl(p.base_url) === normalizeUrl(provider.base_url))?.id || '',
    slug: provider.slug || '',
    name: provider.name,
    api_type: provider.api_type,
    base_url: provider.base_url,
    api_key: '',
    enabled: provider.enabled,
  })
  draftModels.value = models.value
    .filter((m) => m.provider_id === provider.provider_id)
    .map((m) => ({
      entry_id: m.entry_id,
      model_id: m.model_id,
      label: m.label,
      context_window: m.context_window,
      reasoning_levels: m.reasoning_levels || [],
    }))
}

/** 显示名变化且 slug 尚未定形时，自动跟随建议；建议为空不清空已填值 */
function onNameInput(value: string) {
  providerForm.name = value
  if (!editingProviderId.value && !slugTouched.value) {
    const suggestion = suggestSlug(value)
    if (suggestion) {
      providerForm.slug = suggestion
    }
  }
}

function addModelRow() {
  draftModels.value.push({ entry_id: null, model_id: '', label: '', context_window: 0, reasoning_levels: [] })
}

function removeModelRow(index: number) {
  const [row] = draftModels.value.splice(index, 1)
  if (row?.entry_id) {
    removedModelIds.value.push(row.entry_id)
  }
}

function addDiscoveredModel(discovered: UserLLMDiscoveredModel) {
  if (draftModels.value.some((row) => row.model_id === discovered.model_id)) {
    message.info(`「${discovered.model_id}」已在模型目录中`)
    return
  }
  draftModels.value.push({
    entry_id: null,
    model_id: discovered.model_id,
    label: discovered.label,
    context_window: discovered.context_window,
    reasoning_levels: [],
  })
}

/** 表单发现面板的去重依据：目录中已有的 model_id */
const draftExistingIds = computed(() =>
  new Set(draftModels.value.map((row) => row.model_id.trim())))

async function saveProvider() {
  if (!canSubmit.value) {
    return
  }
  // 预检：目录内重复 model_id 提前拦截，不产生半成品
  const seen = new Set<string>()
  for (const row of draftModels.value) {
    const id = row.model_id.trim()
    if (seen.has(id)) {
      return message.warning(`模型目录中存在重复的模型 ID「${id}」`)
    }
    seen.add(id)
  }
  saving.value = true
  const originalMode = editingProviderId.value ? 'update' : 'create'
  try {
    // 顺序落库：先 Provider，再同步模型目录（新增/更新/删除）
    const payload = {
      name: providerForm.name, slug: providerForm.slug.trim(), api_type: providerForm.api_type,
      base_url: providerForm.base_url, enabled: providerForm.enabled,
      api_key: providerForm.api_key, api_key_action: providerForm.api_key.trim() ? 'replace' : 'keep',
    }
    const providerId = editingProviderId.value
      ? (await updateLLMProvider(editingProviderId.value, payload)).provider_id
      : (await createLLMProvider(payload)).provider_id
    for (const entryId of removedModelIds.value) {
      await deleteLLMModel(entryId)
    }
    for (const row of draftModels.value) {
      const modelPayload = {
        provider_id: providerId, model_id: row.model_id.trim(), label: row.label || row.model_id.trim(),
        context_window: row.context_window || 0,
        reasoning_levels: row.reasoning_levels,
      }
      if (row.entry_id) {
        await updateLLMModel(row.entry_id, modelPayload)
      } else {
        await createLLMModel(modelPayload)
      }
    }
    message.success(editingProviderId.value ? '提供方已更新' : '提供方已创建')
    hideProviderForm()
    await refresh()
  } catch (error) {
    const detail = error instanceof Error ? error.message : '保存失败'
    // 半失败恢复：create 半途失败时 Provider 可能已建成——切到其编辑态，
    // 重试变为更新而不是 slug 冲突
    await refresh()
    // 半失败恢复（create / update 同一策略）：保留用户草稿，仅把已落库的行
    // 回填 entry_id——重试走更新而非重复创建/冲突；已删除行由后端幂等删除兜底
    let owner = editingProviderId.value
    if (!owner) {
      const partial = providers.value.find((p) => p.slug === providerForm.slug.trim())
      if (partial) {
        owner = partial.provider_id
        editingProviderId.value = owner
        slugTouched.value = true
        message.warning(`${detail}。提供方已创建，修正模型目录后重新保存即可`)
      }
    }
    if (owner) {
      const fresh = models.value.filter((m) => m.provider_id === owner)
      for (const row of draftModels.value) {
        if (!row.entry_id) {
          const match = fresh.find((m) => m.model_id === row.model_id.trim())
          if (match) {
            row.entry_id = match.entry_id
          }
        }
      }
      // update 模式回填后仍报错；create 半失败的 warning 已含 detail
      if (originalMode === 'update') {
        message.error(detail)
      }
    } else {
      message.error(detail)
    }
  } finally {
    saving.value = false
  }
}

async function toggleProvider(provider: UserLLMProvider, enabled: boolean) {
  try {
    await updateLLMProvider(provider.provider_id, {
      name: provider.name, slug: provider.slug, api_type: provider.api_type, base_url: provider.base_url,
      enabled, api_key_action: 'keep',
    })
    await refresh()
  } catch (error) {
    message.error(error instanceof Error ? error.message : '更新失败')
  }
}

async function discoverDraft() {
  // 单一动作：发现即连通测试（deepseek-harness 模式）。请求携带表单草案，
  // 无需先保存 Provider；失败 message 已含具体原因（认证/网络/不支持）。
  if (!providerForm.base_url.trim()) {
    return message.warning('请先填写 API 端点地址，再获取')
  }
  discovering.value = true
  try {
    const result = await discoverLLMDraft({
      api_type: providerForm.api_type,
      base_url: providerForm.base_url.trim(),
      api_key: providerForm.api_key.trim(),
      provider_id: editingProviderId.value,
    })
    draftDiscovery.value = { models: result.models, message: result.message }
    result.ok ? message.success(result.message) : message.warning(result.message)
  } catch (error) {
    message.error(error instanceof Error ? error.message : '发现模型失败')
  } finally {
    discovering.value = false
  }
}

async function discoverPlatform() {
  // 平台 Provider 用部署侧端点 + 平台 Key（opencode 为 public）拉当前模型；
  // OpenCode Zen 免费模型轮换时，这里看到的就是当下真实可用列表
  discoveringPlatform.value = true
  try {
    const result = await discoverPlatformModels()
    platformDiscovery.value = { models: result.models, message: result.message }
    result.ok ? message.success(result.message) : message.warning(result.message)
  } catch (error) {
    message.error(error instanceof Error ? error.message : '发现失败')
  } finally {
    discoveringPlatform.value = false
  }
}

/** 批量采纳勾选的平台模型（来自发现面板）：复用/创建平台 Provider（public key）后逐个落库 */
async function adoptPickedPlatformModels(pickedRows: DiscoveredModelRow[]) {
  if (!platformProvider.value || !pickedRows.length) {
    return
  }
  adoptingPlatform.value = true
  try {
    let target = providers.value.find((p) => p.slug === platformProvider.value!.id)
    if (target && normalizeUrl(target.base_url) !== normalizeUrl(platformProvider.value.base_url)) {
      return message.warning(`已有同名 Provider ID 但端点不同，请在下方表单手动添加`)
    }
    if (!target) {
      target = await createLLMProvider({
        name: platformProvider.value.label,
        slug: platformProvider.value.id,
        api_type: 'openai',
        base_url: platformProvider.value.base_url,
        api_key: 'public',
        enabled: true,
      })
    }
    const picked = pickedRows.filter(
      (discovered) => !platformExistingIds.value.has(discovered.model_id),
    )
    let added = 0
    for (const discovered of picked) {
      await createLLMModel({
        provider_id: target.provider_id,
        model_id: discovered.model_id,
        label: discovered.label,
        context_window: discovered.context_window,
      })
      added++
    }
    message.success(added ? `已添加 ${added} 个模型` : '所选模型均已存在')
    await refresh()
  } catch (error) {
    message.error(error instanceof Error ? error.message : '添加失败')
    await refresh()
  } finally {
    adoptingPlatform.value = false
  }
}

/** 自定义模型的目录复合 id（与后端 public_model_rows 构造一致） */
function compositeModelId(provider: UserLLMProvider, model: UserLLMModel) {
  return `${provider.slug || model.provider_id.slice(0, 8)}/${model.model_id}`
}

/** 设为默认对话模型（用户级偏好，覆盖 yaml 目录默认；null 恢复平台默认） */
async function makeDefault(modelId: string | null) {
  try {
    await setLLMDefaultModel(modelId)
    message.success(modelId ? '已设为默认模型' : '已恢复平台默认')
    await refresh()
  } catch (error) {
    message.error(error instanceof Error ? error.message : '设置失败')
  }
}

/** tokens 千分位 + 等宽数字（列表列对齐用） */
function formatTokens(n: number): string {
  return n.toLocaleString('en-US')
}

function removeProvider(provider: UserLLMProvider) {
  dialog.warning({
    title: `删除「${provider.name}」？`,
    content: '其下的自定义模型会一并移除。',
    positiveText: '删除', negativeText: '取消',
    async onPositiveClick() {
      await deleteLLMProvider(provider.provider_id)
      message.success('已删除')
      if (editingProviderId.value === provider.provider_id) {
        hideProviderForm()
      }
      await refresh()
    },
  })
}

onMounted(() => {
  void refresh()
})
</script>

<template>
  <SettingsSection title="模型" description="查看可用模型，并添加自己的模型服务（API Key 加密存储，仅本人可用）。">
    <SettingsStatus v-if="loading" title="正在加载">
      正在读取模型目录…
    </SettingsStatus>
    <template v-else>
      <!-- Provider 分组列表：平台 Provider + 自定义 Provider，模型挂在各组下 -->
      <div class="provider-list">
        <!-- 平台 Provider：内置默认模型 + 用户采纳的模型合并为一组（同名用户 Provider 不再单列） -->
        <div v-if="platformProvider" class="provider-group">
          <div class="provider-row toggle" @click="toggleGroup('platform')">
            <div class="provider-id">
              <span class="status-dot ok"></span>
              <strong>{{ platformProvider.label }}</strong>
              <n-tag size="small" :bordered="false">平台</n-tag>
              <span class="muted">（{{ mergedPlatformRows.length }} 个模型）</span>
            </div>
            <div class="muted provider-meta" :title="platformProvider.base_url">
              {{ platformProvider.base_url }}
            </div>
            <div class="row-actions">
              <span class="chevron" :class="{ open: expandedGroups.has('platform') }">▾</span>
            </div>
          </div>
          <template v-if="expandedGroups.has('platform')">
            <!-- 展开区工具栏：编辑与发现动作（与自定义组的动作层级一致） -->
            <div class="group-toolbar">
              <span class="muted">免费模型会轮换，可随时获取最新列表</span>
              <div class="toolbar-actions">
                <n-button
                  v-if="platformProviderRow" size="tiny" quaternary
                  @click.stop="platformProviderRow && editProvider(platformProviderRow)"
                >
                  编辑
                </n-button>
                <n-button size="tiny" :loading="discoveringPlatform" @click="discoverPlatform">
                  获取可用模型
                </n-button>
              </div>
            </div>
            <div v-for="row in mergedPlatformRows" :key="row.key" class="grouped-model-row">
              <div class="grouped-model">
                <strong>{{ row.label }}</strong>
                <span class="muted">{{ row.model_id }}</span>
              </div>
              <div class="tags">
                <n-tag v-if="row.offline" size="small" type="warning" :bordered="false">已下线</n-tag>
                <n-tag v-if="row.supportsVision" size="small">视觉</n-tag>
                <n-tag v-if="row.contextWindow" size="small" :bordered="false"><span class="token-num">{{ formatTokens(row.contextWindow) }} tokens</span></n-tag>
                <n-tag v-if="row.isDefault" size="small" type="success">默认</n-tag>
                <n-button
                  v-else size="tiny" quaternary
                  @click.stop="makeDefault(row.catalog_id)"
                >
                  设为默认
                </n-button>
              </div>
            </div>
            <ModelDiscoveryPanel
              v-if="platformDiscovery"
              :models="platformDiscovery.models"
              :existing-ids="platformExistingIds"
              :adopting="adoptingPlatform"
              @adopt="adoptPickedPlatformModels"
            />
          </template>
        </div>
        <SettingsEmptyState v-else title="暂无可用模型" description="可在下方添加自己的模型服务。" />

        <!-- 自定义 Provider：行头只做标识与折叠（与平台组同构）；动作在展开区工具栏 -->
        <div v-for="provider in customProviders" :key="provider.provider_id" class="provider-group">
          <div class="provider-row toggle" @click="toggleGroup(provider.provider_id)">
            <div class="provider-id">
              <span class="status-dot" :class="provider.has_key ? 'ok' : 'missing'"></span>
              <strong>{{ provider.name }}</strong>
              <n-tag size="small" :bordered="false">自定义</n-tag>
              <n-tag v-if="!provider.enabled" size="small" type="warning" :bordered="false">已停用</n-tag>
              <span class="muted">（{{ models.filter(m => m.provider_id === provider.provider_id).length }} 个模型）</span>
            </div>
            <div class="muted provider-meta" :title="provider.base_url">
              {{ provider.base_url }}
            </div>
            <div class="row-actions">
              <span class="chevron" :class="{ open: expandedGroups.has(provider.provider_id) }">▾</span>
            </div>
          </div>
          <template v-if="expandedGroups.has(provider.provider_id)">
            <!-- 展开区工具栏：启用开关 + 编辑/删除（动作层级与平台组一致） -->
            <div class="group-toolbar">
              <span class="muted">{{ provider.slug || provider.provider_id.slice(0, 8) }} · {{ provider.base_url }}</span>
              <div class="toolbar-actions">
                <label class="enable-toggle">
                  <n-switch size="small" :value="provider.enabled" @update:value="value => toggleProvider(provider, value)" />
                  启用
                </label>
                <n-button size="tiny" quaternary @click="editProvider(provider)">
                  编辑
                </n-button>
                <n-button size="tiny" quaternary type="error" @click="removeProvider(provider)">
                  删除
                </n-button>
              </div>
            </div>
            <div v-for="model in models.filter(m => m.provider_id === provider.provider_id)" :key="model.entry_id" class="grouped-model-row">
              <div class="grouped-model">
                <strong>{{ model.label }}</strong>
                <span class="muted">{{ model.model_id }}</span>
              </div>
              <div class="tags">
                <n-tag v-if="model.reasoningLevels && model.reasoningLevels.length" size="small" :bordered="false" type="info">推理</n-tag>
                <n-tag v-if="model.context_window" size="small" :bordered="false"><span class="token-num">{{ formatTokens(model.context_window) }} tokens</span></n-tag>
                <n-tag v-if="compositeModelId(provider, model) === catalog?.default_id" size="small" type="success">默认</n-tag>
                <n-button
                  v-else size="tiny" quaternary
                  @click.stop="makeDefault(compositeModelId(provider, model))"
                >
                  设为默认
                </n-button>
              </div>
            </div>
          </template>
        </div>
      </div>

      <h4 class="sub-title">自定义提供方</h4>

      <!-- 入口按钮：表单隐藏时出现，点击唤起添加表单 -->
      <button v-if="!providerFormVisible" type="button" class="add-provider-btn" @click="showProviderForm">
        + 添加提供方
      </button>

      <!-- 自定义提供方表单（dsh 风格：Provider ID / 显示名称 / API 地址 / 协议 / 密钥 / 模型目录） -->
      <div v-if="providerFormVisible" class="provider-form-card">
        <div class="form-card-head">
          {{ editingProviderId ? `编辑提供方：${providerForm.name || providerForm.slug}` : '新建提供方' }}
        </div>
        <label class="field preset-line">
          <span class="field-label">提供方</span>
          <n-select
            v-model:value="providerForm.preset_id"
            :options="presetOptions" size="small" class="flat-input"
            placeholder="选择提供方或自定义"
            @update:value="onPresetChange"
          />
        </label>
        <div class="field-grid">
          <label class="field">
            <span class="field-label">Provider ID</span>
            <n-input
              :value="providerForm.slug" size="small" class="flat-input"
              placeholder="如 my-deepseek（小写字母、数字、连字符）"
              @update:value="onSlugInput"
            />
          </label>
          <label class="field">
            <span class="field-label">显示名称</span>
            <n-input
              :value="providerForm.name" size="small" class="flat-input"
              placeholder="留空则同 Provider ID" @update:value="onNameInput"
            />
          </label>
          <label class="field span-2">
            <span class="field-label">API 地址</span>
            <n-input
              v-model:value="providerForm.base_url" size="small" class="flat-input"
              placeholder="https://api.deepseek.com/v1"
              @update:value="onBaseUrlInput"
            />
          </label>
          <label class="field">
            <span class="field-label">API 协议</span>
            <n-select v-model:value="providerForm.api_type" :options="effectiveApiTypeOptions" size="small" class="flat-input" />
          </label>
          <label class="field">
            <span class="field-label">API 密钥</span>
            <n-input
              v-model:value="providerForm.api_key" size="small" class="flat-input" type="password" show-password-on="click"
              :input-props="{ autocomplete: 'new-password' }"
              :placeholder="editingProviderId ? '已配置——输入新值可替换' : 'sk-…（加密存储）'"
            />
          </label>
          <label class="field span-2 enabled-line">
            <n-switch v-model:value="providerForm.enabled" size="small" /> 启用服务
          </label>
        </div>

        <!-- 模型目录：获取可用模型（草案发现）+ 行编辑 + 空态虚线框 -->
        <div class="model-catalog">
          <div class="catalog-head">
            <span class="field-label">模型目录</span>
            <div class="catalog-actions">
              <n-button size="tiny" :loading="discovering" :disabled="!providerForm.base_url.trim()" @click="discoverDraft">
                获取可用模型
              </n-button>
              <n-button size="tiny" @click="addModelRow">添加模型</n-button>
            </div>
          </div>

          <div v-if="!draftModels.length" class="catalog-empty">
            暂无模型——可「获取可用模型」拉取候选，或「添加模型」手动填写
          </div>

          <div v-for="(row, index) in draftModels" :key="row.entry_id || `new-${index}`" class="catalog-row">
            <n-input v-model:value="row.model_id" size="small" class="flat-input" placeholder="模型 ID（如 deepseek-chat）" />
            <n-input v-model:value="row.label" size="small" class="flat-input" placeholder="显示名称（可选）" />
            <n-input-number v-model:value="row.context_window" size="small" class="flat-input" :min="0" placeholder="上下文窗口（tokens，可选）" />
            <n-select v-model:value="row.reasoning_levels" size="small" class="flat-input" multiple :options="reasoningSelectOptions" max-tag-count="responsive" placeholder="推理档位（可选）" />
            <n-button size="tiny" quaternary type="error" @click="removeModelRow(index)">删除</n-button>
          </div>

          <div v-if="draftDiscovery" class="discovery-block">
            <div class="muted">{{ draftDiscovery.message }}。发现结果不会自动保存，勾选后加入下方目录随表单一起保存。</div>
            <ModelDiscoveryPanel
              :models="draftDiscovery.models"
              :existing-ids="draftExistingIds"
              adopt-label="添加到目录"
              @adopt="rows => rows.forEach(addDiscoveredModel)"
            />
          </div>
        </div>

        <div class="form-footer">
          <n-button size="small" round @click="hideProviderForm">取消</n-button>
          <n-button
            size="small" round type="primary" class="submit-btn"
            :disabled="!canSubmit" :loading="saving" @click="saveProvider"
          >
            {{ editingProviderId ? '保存修改' : '创建提供方' }}
          </n-button>
        </div>
      </div>
    </template>
  </SettingsSection>
</template>

<style scoped lang="scss">
.muted { margin-top: 4px; color: var(--noesis-color-text-secondary); font-size: 12px; }
.tags { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
.sub-title { margin: 28px 0 12px; font-size: 14px; }

/* ── Provider 分组列表 ── */
.provider-list { display: grid; gap: 4px; max-width: 720px; }
.provider-group { display: grid; gap: 0; }
.provider-group .provider-row { border-bottom: none; }
.provider-group:not(:last-of-type) { margin-bottom: 10px; border-bottom: 1px solid var(--noesis-color-border-subtle, rgba(0,0,0,.08)); padding-bottom: 4px; }
.grouped-model-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 7px 0 7px 16px; border-top: 1px dashed var(--noesis-color-border-subtle, rgba(0,0,0,.06)); }
.group-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 8px 0 2px 16px; }
.group-toolbar .muted { margin-top: 0; min-width: 0; word-break: break-all; }
.toolbar-actions { display: flex; align-items: center; gap: 8px; margin-left: auto; flex-shrink: 0; }
.enable-toggle { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--noesis-color-text-secondary); }
/* tokens 固定宽度右对齐：各行数值列竖向对齐（256,000 与 1,048,576 同一终止线） */
.token-num { display: inline-block; min-width: 96px; text-align: right; font-variant-numeric: tabular-nums; }
.grouped-model { display: flex; align-items: baseline; gap: 10px; min-width: 0; flex-wrap: wrap; }
.provider-row.toggle { cursor: pointer; user-select: none; }
.chevron { color: var(--noesis-color-text-muted); font-size: 11px; transition: transform 0.15s; display: inline-block; margin-left: 2px; }
.chevron.open { transform: rotate(180deg); }
.provider-row { display: flex; align-items: center; gap: 12px; padding: 12px 0; border-bottom: 1px solid var(--noesis-color-border-subtle, rgba(0,0,0,.08)); }
.provider-row .muted { margin-top: 0; }
.provider-id { display: flex; align-items: center; gap: 8px; min-width: 0; }
.provider-meta { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.status-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.status-dot.ok { background: var(--noesis-color-success, #34c759); }
.status-dot.missing { background: var(--noesis-color-border, #d4d0c8); }
.row-actions { display: flex; align-items: center; gap: 2px; flex-shrink: 0; }

/* 入口按钮：虚线极简风，与模型目录空态一致 */
.add-provider-btn {
  display: block;
  width: 100%;
  max-width: 720px;
  padding: 10px;
  border: 1.5px dashed var(--noesis-color-border-subtle, rgba(0,0,0,.14));
  border-radius: 10px;
  background: transparent;
  color: var(--noesis-color-text-secondary);
  font-size: 13px;
  cursor: pointer;
  transition: border-color .15s, color .15s;

  &:hover {
    border-color: var(--noesis-color-primary);
    color: var(--noesis-color-primary);
  }
}

/* ── 表单卡片（dsh 极简风：浅色卡片 + 细边框 + 无阴影，颜色走主题 token） ── */
.provider-form-card {
  max-width: 720px;
  padding: 16px;
  border: 1px solid var(--noesis-color-border-subtle, rgba(0,0,0,.1));
  border-radius: 10px;
  background: var(--noesis-color-bg-elevated);
  display: grid;
  gap: 14px;
}
.form-card-head { font-size: 13px; font-weight: 600; color: var(--noesis-color-text); }
.preset-line { max-width: 360px; }
.field-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.field { display: grid; gap: 6px; min-width: 0; }
.field.span-2 { grid-column: span 2; }
.field-label { font-size: 12px; color: var(--noesis-color-text-secondary); }
.enabled-line { flex-direction: row; align-items: center; display: flex; gap: 8px; font-size: 12px; color: var(--noesis-color-text-secondary); }

/* 输入框：32px 高、8px 圆角、聚焦黑色细边框（无蓝色光圈） */
.flat-input { --n-height: 32px; --n-border-radius: 8px; font-size: 13px; }
.flat-input :deep(.n-input__input-el), .flat-input :deep(.n-input__textarea-el) { font-size: 13px; }
.flat-input:focus-within { --n-box-shadow: none !important; --n-border-color: var(--noesis-color-text) !important; --n-border-hover: var(--noesis-color-text) !important; --n-border-focus: var(--noesis-color-text) !important; }

/* ── 模型目录 ── */
.model-catalog { display: grid; gap: 10px; }
.catalog-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.catalog-actions { display: flex; gap: 6px; }
.catalog-empty {
  padding: 22px 12px;
  border: 1.5px dashed var(--noesis-color-border-subtle, rgba(0,0,0,.14));
  border-radius: 10px;
  text-align: center;
  color: var(--noesis-color-text-secondary);
  font-size: 12px;
}
.catalog-row { display: grid; grid-template-columns: 1.4fr 1fr 0.9fr auto; gap: 8px; align-items: center; }
.discovery-block { margin-top: 8px; }

.form-footer { display: flex; justify-content: flex-end; gap: 8px; }
.submit-btn { min-width: 96px; }

@media (max-width: $bp-md) {
  .field-grid { grid-template-columns: 1fr; }
  .field.span-2 { grid-column: span 1; }
  .catalog-row { grid-template-columns: 1fr; }
  .provider-row { flex-wrap: wrap; }
  .group-toolbar { flex-wrap: wrap; }

  /* 分组模型行：名称 + 标签 + 操作窄屏换行后，操作组靠右（与桌面一致） */
  .grouped-model-row { flex-wrap: wrap; padding-left: 8px; }
  .tags { justify-content: flex-end; margin-left: auto; }
  /* Provider 行换行后：元信息占满一行，操作组独立一行右对齐 */
  .provider-meta { flex-basis: 100%; order: 3; white-space: normal; }
  .row-actions { margin-left: auto; }
  /* 分组头卡在窄屏的计数徽标换行 */
  .provider-id { flex-wrap: wrap; }
}
</style>
