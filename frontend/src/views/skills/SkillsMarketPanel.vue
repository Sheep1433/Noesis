<script setup lang="ts">
import type { SkillMarketItem, SkillMarketSort } from '@/api/skills'
import { CheckmarkCircleOutline, DownloadOutline, OpenOutline, SearchOutline } from '@vicons/ionicons-v5'
import {
  NButton,
  NEmpty,
  NIcon,
  NInput,
  NSpace,
  NSpin,
  NTag,
  NText,
  useDialog,
  useMessage,
} from 'naive-ui'
import { onMounted, ref, watch } from 'vue'
import {
  browseSkillsMarket,
  getSkillsMarketDetail,
  installSkillsMarketPackage,
  searchSkillsMarket,
} from '@/api/skills'
import FilePreview from '@/components/FilePreview/index.vue'

const props = withDefaults(defineProps<{
  active?: boolean
}>(), {
  active: true,
})

const emit = defineEmits<{
  installed: []
}>()

const message = useMessage()
const dialog = useDialog()

const loading = ref(false)
const installingId = ref<string | null>(null)
const query = ref('')
const items = ref<SkillMarketItem[]>([])
const mode = ref<'browse' | 'search'>('browse')
const browseSort = ref<SkillMarketSort>('trending')
const error = ref<string | null>(null)

const detailLoading = ref(false)
const detailItem = ref<SkillMarketItem | null>(null)
const detailMd = ref('')

onMounted(() => {
  if (props.active) {
    void loadBrowse()
  }
})

watch(
  () => props.active,
  (active, wasActive) => {
    if (active && !wasActive) {
      void refreshCurrentList()
    }
  },
)

function normalizeItem(item: SkillMarketItem): SkillMarketItem {
  return {
    ...item,
    installs: Number(item.installs) || 0,
    installed: Boolean(item.installed),
    install_match: item.install_match || 'none',
  }
}

function formatInstalls(n: number): string {
  if (!n) {
    return ''
  }
  if (n >= 1_000_000) {
    return `${(n / 1_000_000).toFixed(1)}M`
  }
  if (n >= 1_000) {
    return `${(n / 1_000).toFixed(1)}K`
  }
  return String(n)
}

function patchItemStatus(updated: SkillMarketItem) {
  const next = normalizeItem(updated)
  const idx = items.value.findIndex((i) => i.id === next.id)
  if (idx >= 0) {
    items.value[idx] = { ...items.value[idx], ...next }
  }
  if (detailItem.value?.id === next.id) {
    detailItem.value = { ...detailItem.value, ...next }
  }
}

async function refreshCurrentList() {
  if (mode.value === 'search' && query.value.trim().length >= 2) {
    await runSearch({ keepDetail: true })
    return
  }
  await loadBrowse({ keepDetail: true })
}

async function setBrowseSort(sort: SkillMarketSort) {
  if (browseSort.value === sort && mode.value === 'browse' && items.value.length) {
    return
  }
  browseSort.value = sort
  await loadBrowse()
}

async function loadBrowse(opts?: { keepDetail?: boolean }) {
  loading.value = true
  error.value = null
  mode.value = 'browse'
  if (!opts?.keepDetail) {
    detailItem.value = null
    detailMd.value = ''
  }
  try {
    const res = await browseSkillsMarket(browseSort.value)
    items.value = res.items.map(normalizeItem)
    if (opts?.keepDetail && detailItem.value) {
      const latest = items.value.find((i) => i.id === detailItem.value?.id)
      if (latest) {
        detailItem.value = { ...detailItem.value, ...latest }
      }
    }
  } catch (e: any) {
    error.value = e.message || '加载榜单失败'
    items.value = []
  } finally {
    loading.value = false
  }
}

async function runSearch(opts?: { keepDetail?: boolean }) {
  const q = query.value.trim()
  if (q.length < 2) {
    message.warning('请输入至少 2 个字符')
    return
  }
  loading.value = true
  error.value = null
  mode.value = 'search'
  if (!opts?.keepDetail) {
    detailItem.value = null
    detailMd.value = ''
  }
  try {
    const res = await searchSkillsMarket(q)
    items.value = res.items.map(normalizeItem)
    if (!res.items.length) {
      message.info('未找到匹配技能')
    }
    if (opts?.keepDetail && detailItem.value) {
      const latest = items.value.find((i) => i.id === detailItem.value?.id)
      if (latest) {
        detailItem.value = { ...detailItem.value, ...latest }
      }
    }
  } catch (e: any) {
    error.value = e.message || '搜索失败'
    items.value = []
  } finally {
    loading.value = false
  }
}

async function openDetail(item: SkillMarketItem) {
  detailLoading.value = true
  detailItem.value = normalizeItem(item)
  detailMd.value = ''
  try {
    const res = await getSkillsMarketDetail(item.source, item.skill_id)
    detailItem.value = normalizeItem(res.item)
    detailMd.value = res.skill_md
    patchItemStatus(res.item)
  } catch (e: any) {
    message.error(e.message || '加载详情失败')
    detailItem.value = null
  } finally {
    detailLoading.value = false
  }
}

function onInstallClick(item: SkillMarketItem) {
  const normalized = normalizeItem(item)
  if (normalized.install_match === 'exact' || normalized.install_match === 'name_conflict') {
    confirmInstall(normalized, true)
    return
  }
  confirmInstall(normalized, false)
}

function confirmInstall(item: SkillMarketItem, overwrite = false) {
  const normalized = normalizeItem(item)
  let content = `将从 ${normalized.source} 安装「${normalized.skill_id}」到个人技能。`
  let title = '安装技能'
  let positiveText = '安装'
  if (overwrite && normalized.install_match === 'exact') {
    title = '重新安装'
    content = `「${normalized.skill_id}」已从同源安装，确定覆盖重新安装吗？`
    positiveText = '覆盖'
  } else if (overwrite && normalized.install_match === 'name_conflict') {
    title = '同名已占用'
    content = `个人技能中已有同名目录「${normalized.skill_id}」（可能来自 ZIP 或其他来源），确定覆盖吗？`
    positiveText = '覆盖'
  } else if (overwrite) {
    title = '覆盖安装'
    content = `个人技能中已有「${normalized.skill_id}」，确定覆盖吗？`
    positiveText = '覆盖'
  }
  dialog.warning({
    title,
    content,
    positiveText,
    negativeText: '取消',
    // 立即关闭弹窗，避免下载期间确认框一直挂着
    onPositiveClick: () => {
      void doInstall(normalized, overwrite)
      return true
    },
  })
}

async function doInstall(item: SkillMarketItem, overwrite: boolean): Promise<boolean> {
  installingId.value = item.id
  try {
    const r = await installSkillsMarketPackage({
      source: item.source,
      skill_id: item.skill_id,
      overwrite,
    })
    if (r.success) {
      message.success(r.message)
      patchItemStatus({
        ...item,
        installed: true,
        install_match: 'exact',
      })
      await refreshCurrentList()
      emit('installed')
      return true
    }
    message.error(r.message || '安装失败')
    return false
  } catch (e: any) {
    const msg = String(e.message || '安装失败')
    if (!overwrite && (msg.includes('已存在') || msg.includes('409'))) {
      confirmInstall({ ...item, install_match: 'name_conflict' }, true)
      return false
    }
    message.error(msg)
    return false
  } finally {
    installingId.value = null
  }
}

function installsLabel(): string {
  return browseSort.value === 'trending' ? '24h' : 'all'
}

function primaryActionLabel(item: SkillMarketItem): string {
  const match = item.install_match || 'none'
  if (match === 'exact') {
    return '已安装'
  }
  if (match === 'name_conflict') {
    return '同名已占用'
  }
  return '安装'
}
</script>

<template>
  <div class="skills-market">
    <div class="market-toolbar">
      <n-input
        v-model:value="query"
        clearable
        placeholder="搜索 skills.sh（至少 2 字符）"
        @keyup.enter="runSearch()"
      >
        <template #prefix>
          <n-icon :component="SearchOutline" />
        </template>
      </n-input>
      <n-button type="primary" :loading="loading && mode === 'search'" @click="runSearch()">
        搜索
      </n-button>
    </div>

    <div class="market-sort">
      <n-button
        size="small"
        :type="mode === 'browse' && browseSort === 'trending' ? 'primary' : 'default'"
        :secondary="!(mode === 'browse' && browseSort === 'trending')"
        :disabled="loading"
        @click="setBrowseSort('trending')"
      >
        Trending
      </n-button>
      <n-button
        size="small"
        :type="mode === 'browse' && browseSort === 'all_time' ? 'primary' : 'default'"
        :secondary="!(mode === 'browse' && browseSort === 'all_time')"
        :disabled="loading"
        @click="setBrowseSort('all_time')"
      >
        All Time
      </n-button>
    </div>

    <div v-if="loading && !items.length" class="market-state">
      <n-spin size="medium" />
      <span>{{ mode === 'search' ? '搜索中…' : '加载榜单…' }}</span>
    </div>

    <div v-else-if="error" class="market-state">
      <n-empty :description="error">
        <template #extra>
          <n-button @click="mode === 'search' ? runSearch() : loadBrowse()">
            重试
          </n-button>
        </template>
      </n-empty>
    </div>

    <div v-else class="market-body">
      <div class="market-list">
        <div
          v-for="(item, index) in items"
          :key="item.id"
          class="market-card"
          :class="{ active: detailItem?.id === item.id }"
          @click="openDetail(item)"
        >
          <div class="market-card-title">
            <span class="name">
              <span v-if="mode === 'browse'" class="rank">#{{ index + 1 }}</span>
              {{ item.name }}
            </span>
            <div class="market-card-tags">
              <n-tag v-if="item.install_match === 'exact'" size="small" type="success" :bordered="false">
                已安装
              </n-tag>
              <n-tag
                v-else-if="item.install_match === 'name_conflict'"
                size="small"
                type="warning"
                :bordered="false"
              >
                同名占用
              </n-tag>
              <n-tag v-if="item.installs" size="small" :bordered="false">
                {{ formatInstalls(item.installs) }}
                <template v-if="mode === 'browse'"> · {{ installsLabel() }}</template>
                <template v-else> installs</template>
              </n-tag>
            </div>
          </div>
          <n-text depth="3" class="source">
            {{ item.source }}
          </n-text>
          <div class="market-card-actions" @click.stop>
            <n-button
              size="tiny"
              :type="item.install_match === 'none' ? 'primary' : 'default'"
              :secondary="item.install_match !== 'none'"
              :loading="installingId === item.id"
              @click="onInstallClick(item)"
            >
              <template #icon>
                <n-icon
                  :component="item.install_match === 'exact' ? CheckmarkCircleOutline : DownloadOutline"
                />
              </template>
              {{ primaryActionLabel(item) }}
            </n-button>
            <n-button
              v-if="item.install_match === 'exact'"
              size="tiny"
              quaternary
              :loading="installingId === item.id"
              @click="confirmInstall(item, true)"
            >
              重新安装
            </n-button>
            <n-button
              v-if="item.market_url"
              size="tiny"
              tag="a"
              :href="item.market_url"
              target="_blank"
              rel="noopener noreferrer"
              quaternary
            >
              <template #icon>
                <n-icon :component="OpenOutline" />
              </template>
              skills.sh
            </n-button>
          </div>
        </div>
        <n-empty v-if="!items.length" description="暂无结果" />
      </div>

      <div class="market-detail">
        <div v-if="detailLoading" class="market-state">
          <n-spin size="medium" />
          <span>加载 SKILL.md…</span>
        </div>
        <template v-else-if="detailItem">
          <div class="detail-header">
            <div>
              <div class="detail-name">
                {{ detailItem.name }}
              </div>
              <n-text depth="3">
                {{ detailItem.source }}
              </n-text>
              <div class="detail-tags">
                <n-tag
                  v-if="detailItem.install_match === 'exact'"
                  size="small"
                  type="success"
                  :bordered="false"
                >
                  已安装
                </n-tag>
                <n-tag
                  v-else-if="detailItem.install_match === 'name_conflict'"
                  size="small"
                  type="warning"
                  :bordered="false"
                >
                  同名占用
                </n-tag>
                <n-tag v-if="detailItem.installs" size="small" :bordered="false">
                  {{ formatInstalls(detailItem.installs) }} installs
                </n-tag>
              </div>
            </div>
            <n-space>
              <n-button
                v-if="detailItem.install_match === 'exact'"
                size="small"
                :loading="installingId === detailItem.id"
                @click="confirmInstall(detailItem, true)"
              >
                重新安装
              </n-button>
              <n-button
                :type="detailItem.install_match === 'none' ? 'primary' : 'default'"
                size="small"
                :loading="installingId === detailItem.id"
                @click="onInstallClick(detailItem)"
              >
                {{
                  detailItem.install_match === 'exact'
                    ? '已安装'
                    : detailItem.install_match === 'name_conflict'
                      ? '覆盖安装'
                      : '安装到个人技能'
                }}
              </n-button>
            </n-space>
          </div>
          <n-text depth="3" class="detail-hint">
            正文来自 skills.sh；安装后可在「已安装」Tab 查看完整目录结构。
          </n-text>
          <div class="detail-content">
            <div class="detail-preview">
              <FilePreview
                v-if="detailMd"
                path="SKILL.md"
                :content="detailMd"
                :show-path="false"
                density="comfortable"
              />
              <n-empty v-else description="暂无 SKILL.md 内容" />
            </div>
          </div>
        </template>
        <n-empty v-else description="选择左侧技能查看详情" />
      </div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.skills-market {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
  min-height: 0;
  padding: 12px 16px 16px;
}

.market-toolbar {
  display: flex;
  gap: 8px;
  align-items: center;

  .n-input {
    flex: 1;
  }
}

.market-sort {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.rank {
  margin-right: 6px;
  color: var(--noesis-text-3);
  font-variant-numeric: tabular-nums;
}

.market-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 200px;
  color: var(--noesis-text-3);
}

.market-body {
  display: grid;
  grid-template-columns: minmax(260px, 340px) 1fr;
  gap: 12px;
  min-height: 0;
  flex: 1;
}

.market-list {
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 0;
  padding-right: 4px;
}

.market-card {
  border: 1px solid var(--noesis-border);
  border-radius: 8px;
  padding: 10px 12px;
  cursor: pointer;
  background: var(--noesis-bg-elevated, transparent);
  transition: border-color 0.15s ease;

  &:hover,
  &.active {
    border-color: var(--noesis-primary);
  }
}

.market-card-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 4px;

  .name {
    font-weight: 600;
    font-size: 14px;
  }
}

.market-card-tags,
.detail-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}

.detail-tags {
  margin-top: 6px;
}

.source {
  font-size: 12px;
  word-break: break-all;
}

.market-card-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.market-detail {
  min-height: 0;
  overflow: hidden;
  border: 1px solid var(--noesis-border);
  border-radius: 8px;
  padding: 12px;
  display: flex;
  flex-direction: column;
}

.detail-hint {
  display: block;
  font-size: 12px;
  margin-bottom: 8px;
}

.detail-content {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.detail-preview {
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow: auto;
}

.detail-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.detail-name {
  font-size: 16px;
  font-weight: 600;
}

@media (max-width: 900px) {
  .market-body {
    grid-template-columns: 1fr;
  }
}
</style>
