<script setup lang="ts">
import type { SkillMarketItem, SkillMarketSort } from '@/api/skills'
import { DownloadOutline, OpenOutline, SearchOutline } from '@vicons/ionicons-v5'
import {
  NButton,
  NDrawer,
  NDrawerContent,
  NEmpty,
  NIcon,
  NInput,
  NPagination,
  NSpace,
  NSpin,
  NTag,
  NText,
  useDialog,
  useMessage,
} from 'naive-ui'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useBreakpoint } from '@/hooks/useBreakpoint'
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
const { isMobile } = useBreakpoint()

/** 主从分栏所需最小内容宽度；低于此值改列表 + Drawer（按容器宽度，非整页视口） */
const MARKET_SPLIT_MIN_WIDTH = 720

const marketRootRef = ref<HTMLElement | null>(null)
const marketWidth = ref(0)

let marketResizeObserver: ResizeObserver | null = null

const useStackedLayout = computed(() => {
  if (isMobile.value) {
    return true
  }
  return marketWidth.value < MARKET_SPLIT_MIN_WIDTH
})

const detailDrawerOpen = computed({
  get: () => useStackedLayout.value && (!!detailItem.value || detailLoading.value),
  set: (open) => {
    if (!open) {
      closeMobileDetail()
    }
  },
})

const PAGE_SIZE = 12

const loading = ref(false)
const installingId = ref<string | null>(null)
const query = ref('')
const items = ref<SkillMarketItem[]>([])
const total = ref(0)
const currentPage = ref(1)
const mode = ref<'browse' | 'search'>('browse')
const browseSort = ref<SkillMarketSort>('trending')
const error = ref<string | null>(null)

const detailLoading = ref(false)
const detailItem = ref<SkillMarketItem | null>(null)
const detailMd = ref('')

onMounted(() => {
  if (marketRootRef.value) {
    marketWidth.value = marketRootRef.value.getBoundingClientRect().width
    marketResizeObserver = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width
      if (width != null) {
        marketWidth.value = width
      }
    })
    marketResizeObserver.observe(marketRootRef.value)
  }
  if (props.active) {
    void loadBrowse()
  }
})

onUnmounted(() => {
  marketResizeObserver?.disconnect()
  marketResizeObserver = null
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
    await runSearch({ keepDetail: true, page: currentPage.value })
    return
  }
  await loadBrowse({ keepDetail: true, page: currentPage.value })
}

async function setBrowseSort(sort: SkillMarketSort) {
  if (browseSort.value === sort && mode.value === 'browse' && items.value.length) {
    return
  }
  browseSort.value = sort
  currentPage.value = 1
  await loadBrowse()
}

async function loadBrowse(opts?: { keepDetail?: boolean; page?: number }) {
  const page = opts?.page ?? currentPage.value
  loading.value = true
  error.value = null
  mode.value = 'browse'
  if (!opts?.keepDetail) {
    detailItem.value = null
    detailMd.value = ''
  }
  try {
    const res = await browseSkillsMarket(
      browseSort.value,
      PAGE_SIZE,
      (page - 1) * PAGE_SIZE,
    )
    items.value = res.items.map(normalizeItem)
    total.value = res.total ?? res.items.length
    currentPage.value = page
    if (opts?.keepDetail && detailItem.value) {
      const latest = items.value.find((i) => i.id === detailItem.value?.id)
      if (latest) {
        detailItem.value = { ...detailItem.value, ...latest }
      }
    }
  } catch (e: any) {
    error.value = e.message || '加载榜单失败'
    items.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

async function runSearch(opts?: { keepDetail?: boolean; page?: number }) {
  const q = query.value.trim()
  if (q.length < 2) {
    message.warning('请输入至少 2 个字符')
    return
  }
  const page = opts?.page ?? 1
  loading.value = true
  error.value = null
  mode.value = 'search'
  if (!opts?.keepDetail) {
    detailItem.value = null
    detailMd.value = ''
  }
  try {
    const res = await searchSkillsMarket(q, PAGE_SIZE, (page - 1) * PAGE_SIZE)
    items.value = res.items.map(normalizeItem)
    total.value = res.total ?? res.items.length
    currentPage.value = page
    if (!res.items.length && page === 1) {
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
    total.value = 0
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

function closeMobileDetail() {
  detailItem.value = null
  detailMd.value = ''
  detailLoading.value = false
}

function formatRank(index: number): string {
  return String((currentPage.value - 1) * PAGE_SIZE + index + 1).padStart(2, '0')
}

async function onPageChange(page: number) {
  if (mode.value === 'search') {
    await runSearch({ keepDetail: true, page })
    return
  }
  await loadBrowse({ keepDetail: true, page })
}

function listActionLabel(item: SkillMarketItem): string {
  const match = item.install_match || 'none'
  if (match === 'exact') {
    return '重新安装'
  }
  if (match === 'name_conflict') {
    return '覆盖安装'
  }
  return '安装'
}

function onListActionClick(item: SkillMarketItem) {
  const match = item.install_match || 'none'
  if (match === 'exact' || match === 'name_conflict') {
    confirmInstall(item, true)
    return
  }
  confirmInstall(item, false)
}

function detailPrimaryLabel(item: SkillMarketItem): string {
  const match = item.install_match || 'none'
  if (match === 'name_conflict') {
    return '覆盖安装'
  }
  if (match === 'none') {
    return isMobile.value ? '安装' : '安装到个人技能'
  }
  return ''
}
</script>

<template>
  <div
    ref="marketRootRef"
    class="skills-market"
    :class="{ 'skills-market--stacked': useStackedLayout }"
  >
    <div class="market-toolbar">
      <n-input
        v-model:value="query"
        clearable
        :placeholder="isMobile ? '搜索 skills.sh' : '搜索 skills.sh（至少 2 字符）'"
        @keyup.enter="runSearch()"
      >
        <template #prefix>
          <n-icon :component="SearchOutline" />
        </template>
      </n-input>
      <n-button
        type="primary"
        :loading="loading && mode === 'search'"
        :circle="isMobile"
        @click="runSearch()"
      >
        <template v-if="isMobile" #icon>
          <n-icon :component="SearchOutline" />
        </template>
        <span v-if="!isMobile">搜索</span>
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
      <div class="market-list-col">
      <div class="market-list">
        <div
          v-for="(item, index) in items"
          :key="item.id"
          class="market-card"
          :class="{ active: detailItem?.id === item.id }"
        >
          <button
            type="button"
            class="market-card-main"
            @click="openDetail(item)"
          >
            <div class="market-card-title">
              <span class="name">
                <span
                  v-if="mode === 'browse'"
                  class="rank-badge"
                  :class="{ 'rank-badge--top': (currentPage - 1) * PAGE_SIZE + index < 3 }"
                >{{ formatRank(index) }}</span>
                {{ item.name }}
              </span>
              <div class="market-card-tags">
                <n-tag
                  v-if="item.install_match === 'name_conflict'"
                  size="small"
                  type="warning"
                  :bordered="false"
                >
                  同名占用
                </n-tag>
                <n-tag v-if="item.installs" size="small" :bordered="false">
                  {{ formatInstalls(item.installs) }}
                  <template v-if="mode === 'browse'"> · {{ installsLabel() }}</template>
                </n-tag>
              </div>
            </div>
            <n-text v-if="useStackedLayout" depth="3" class="source source--stacked">
              {{ item.source }}
            </n-text>
            <n-text v-else depth="3" class="source">
              {{ item.source }}
            </n-text>
          </button>
          <div v-if="!useStackedLayout" class="market-card-actions">
            <n-button
              size="tiny"
              :type="item.install_match === 'none' ? 'primary' : 'default'"
              :secondary="item.install_match !== 'none'"
              :loading="installingId === item.id"
              @click.stop="onListActionClick(item)"
            >
              <template v-if="item.install_match === 'none'" #icon>
                <n-icon :component="DownloadOutline" />
              </template>
              {{ listActionLabel(item) }}
            </n-button>
            <n-button
              v-if="!isMobile && item.market_url"
              size="tiny"
              tag="a"
              :href="item.market_url"
              target="_blank"
              rel="noopener noreferrer"
              quaternary
              @click.stop
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
      <div v-if="total > PAGE_SIZE" class="market-pagination">
        <n-pagination
          :page="currentPage"
          :page-size="PAGE_SIZE"
          :item-count="total"
          :disabled="loading"
          size="small"
          :page-slot="isMobile ? 5 : 7"
          @update:page="onPageChange"
        />
      </div>
      </div>

      <div v-if="!useStackedLayout" class="market-detail">
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
                  v-if="detailItem.install_match === 'name_conflict'"
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
                v-else-if="detailItem.install_match !== 'exact'"
                :type="detailItem.install_match === 'none' ? 'primary' : 'default'"
                size="small"
                :loading="installingId === detailItem.id"
                @click="onInstallClick(detailItem)"
              >
                {{ detailPrimaryLabel(detailItem) }}
              </n-button>
            </n-space>
          </div>
          <div class="detail-content">
            <FilePreview
              v-if="detailMd"
              path="SKILL.md"
              :content="detailMd"
              :show-path="false"
              density="comfortable"
              download-title="下载"
            />
            <n-empty v-else description="暂无 SKILL.md 内容" />
          </div>
        </template>
        <n-empty v-else description="选择左侧技能查看详情" />
      </div>
    </div>

    <n-drawer
      v-if="useStackedLayout"
      v-model:show="detailDrawerOpen"
      placement="right"
      :width="'100%'"
      :trap-focus="false"
      :block-scroll="true"
    >
      <n-drawer-content
        :title="detailItem?.name ?? '加载中…'"
        closable
        body-content-style="padding: 0 12px 16px; height: 100%; display: flex; flex-direction: column; min-height: 0;"
        @close="closeMobileDetail"
      >
        <div v-if="detailLoading" class="market-state market-state--drawer">
          <n-spin size="medium" />
          <span>加载 SKILL.md…</span>
        </div>
        <template v-else-if="detailItem">
          <div class="detail-content detail-content--drawer">
            <FilePreview
              v-if="detailMd"
              path="SKILL.md"
              :content="detailMd"
              :show-path="false"
              density="comfortable"
              download-title="下载"
            >
              <template v-if="detailItem.install_match === 'exact'" #header-extra>
                <n-button
                  size="tiny"
                  :loading="installingId === detailItem.id"
                  @click="confirmInstall(detailItem, true)"
                >
                  重新安装
                </n-button>
              </template>
              <template v-else-if="detailPrimaryLabel(detailItem)" #header-extra>
                <n-button
                  :type="detailItem.install_match === 'none' ? 'primary' : 'default'"
                  size="tiny"
                  :loading="installingId === detailItem.id"
                  @click="onInstallClick(detailItem)"
                >
                  {{ detailPrimaryLabel(detailItem) }}
                </n-button>
              </template>
            </FilePreview>
            <n-empty v-else description="暂无 SKILL.md 内容" />
          </div>
        </template>
      </n-drawer-content>
    </n-drawer>
  </div>
</template>

<style scoped lang="scss">
.skills-market {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 1;
  min-height: 0;
  padding: 8px 0 12px;
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

.rank-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 1.6rem;
  height: 1.25rem;
  margin-right: 8px;
  padding: 0 6px;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.04em;
  color: var(--noesis-text-3);
  background: rgb(0 0 0 / 5%);
}

.rank-badge--top {
  color: var(--noesis-primary, #297ce9);
  background: rgb(41 124 233 / 12%);
}

.market-state--drawer {
  min-height: 160px;
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
  grid-template-columns: minmax(220px, min(32%, 320px)) minmax(0, 1fr);
  grid-template-rows: minmax(0, 1fr);
  gap: 12px;
  flex: 1;
  min-height: 0;
  min-width: 0;
  overflow: hidden;
}

.market-list-col {
  display: flex;
  flex-direction: column;
  min-height: 0;
  min-width: 0;
  height: 100%;
  overflow: hidden;
  gap: 8px;
}

.market-list {
  flex: 1;
  overflow-x: clip;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 0;
  min-width: 0;
  padding-right: 4px;
  -webkit-overflow-scrolling: touch;
}

.market-pagination {
  flex-shrink: 0;
  display: flex;
  justify-content: center;
  padding: 4px 0 8px;
}

.market-card {
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  gap: 0;
  border: 1px solid var(--noesis-border);
  border-radius: 8px;
  overflow: hidden;
  background: var(--noesis-bg-elevated, transparent);
  transition: border-color 0.15s ease;

  &:hover,
  &.active {
    border-color: var(--noesis-primary);
  }
}

.market-card-main {
  display: block;
  width: 100%;
  margin: 0;
  padding: 10px 12px 8px;
  border: none;
  background: transparent;
  text-align: left;
  cursor: pointer;
  color: inherit;
  font: inherit;

  &:hover {
    background: rgb(0 0 0 / 2%);
  }
}

.market-card.active .market-card-main {
  background: rgb(41 124 233 / 4%);
}

.market-card-title {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 4px;

  .name {
    min-width: 0;
    flex: 1 1 10rem;
    font-weight: 600;
    font-size: 14px;
    line-height: 1.35;
    word-break: break-word;
  }
}

.market-card-tags,
.detail-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
  flex-shrink: 0;
  margin-left: auto;
}

.detail-tags {
  margin-top: 6px;
  margin-left: 0;
}

.source {
  font-size: 12px;
  word-break: break-all;
}

.source--stacked {
  display: block;
  margin-top: 6px;
  font-size: 11px;
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.market-card-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 0 12px 10px;
}

.market-detail {
  display: flex;
  flex-direction: column;
  min-height: 0;
  min-width: 0;
  height: 100%;
  overflow: hidden;
  border: 1px solid var(--noesis-border);
  border-radius: 8px;
  padding: 12px;
}

.detail-header {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  flex-shrink: 0;
  margin-bottom: 12px;
}

.detail-hint {
  display: block;
  flex-shrink: 0;
  font-size: 12px;
  margin-bottom: 8px;
}

.detail-content {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
}

.detail-content :deep(.file-preview__markdown),
.detail-content :deep(.file-preview__code),
.detail-content :deep(.file-preview__editor),
.detail-content :deep(.file-preview__image-wrap) {
  max-height: none;
  overflow: visible;
}

.detail-name {
  font-size: 16px;
  font-weight: 600;
}

.detail-content--drawer {
  flex: 1;
  min-height: 0;
  padding: 0;
}

.skills-market--stacked {
  .market-body {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 0;
    overflow: hidden;
  }

  .market-list-col {
    flex: 1;
    min-height: 0;
    height: auto;
    overflow: hidden;
  }

  .market-list {
    gap: 10px;
    padding-right: 0;
    overflow-x: clip;
    overflow-y: auto;
    -webkit-overflow-scrolling: touch;
  }

  .market-card-main {
    padding: 12px 14px;
  }

  .market-card-title {
    flex-direction: column;
    align-items: flex-start;
    gap: 6px;
    margin-bottom: 0;

    .name {
      font-size: 15px;
      line-height: 1.35;
      word-break: break-word;
    }
  }

  .market-card-tags {
    width: 100%;
  }
}
</style>
