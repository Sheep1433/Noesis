<script setup lang="ts">
import type {
  ChunkElementType,
  ChunkSummary,
  ShardDetail,
} from '@/api/knowledgeBase'
import {
  ArrowBack,
  ChevronBack,
  ChevronForward,
  SearchOutline,
} from '@vicons/ionicons-v5'
import {
  NAlert,
  NButton,
  NDrawer,
  NDrawerContent,
  NEmpty,
  NIcon,
  NInput,
  NSelect,
  NSpin,
  NTag,
} from 'naive-ui'
import { computed, ref, watch } from 'vue'
import { getDocumentShards, getShardDetail } from '@/api/knowledgeBase'
import ChunkDetailPanel from '@/components/KnowledgeBase/ChunkDetailPanel.vue'
import { useBreakpoint } from '@/hooks/useBreakpoint'
import { chunkElementLabel, formatChunkLocator } from '@/utils/kbFormat'

const props = defineProps<{
  show: boolean
  collectionName: string
  fileName: string
}>()

const emit = defineEmits<{
  'update:show': [value: boolean]
}>()

const { isMobile } = useBreakpoint()
const { width: windowWidth } = useWindowSize()
const drawerWidth = computed(() => {
  if (isMobile.value) {
    return windowWidth.value
  }
  return Math.min(windowWidth.value - 48, 1180)
})

const loading = ref(false)
const error = ref<string | null>(null)
const items = ref<ChunkSummary[]>([])
const total = ref(0)
const nextCursor = ref<string | null>(null)
const cursorHistory = ref<Array<string | null>>([null])
const pageIndex = ref(0)

const selectedShardId = ref('')
const selectedDetail = ref<ShardDetail | null>(null)
const detailLoading = ref(false)
const detailError = ref<string | null>(null)
const mobilePane = ref<'list' | 'detail'>('list')

const keyword = ref('')
const elementType = ref<ChunkElementType | null>(null)
const sort = ref<'asc' | 'desc'>('asc')

const elementOptions = [
  { label: '全部类型', value: '' },
  { label: '正文', value: 'text' },
  { label: '标题', value: 'title' },
  { label: '表格', value: 'table' },
  { label: '图片', value: 'image' },
]
const sortOptions = [
  { label: '正序', value: 'asc' },
  { label: '倒序', value: 'desc' },
]

const selectedIndex = computed(() =>
  items.value.findIndex((item) => item.id === selectedShardId.value),
)
const pageLabel = computed(() => {
  if (!total.value) {
    return '0 个分块'
  }
  return `第 ${pageIndex.value + 1} 页 · 共 ${total.value} 个分块`
})

watch(
  () => [props.show, props.collectionName, props.fileName],
  async ([show, collectionName, fileName], previous) => {
    if (!show || !collectionName || !fileName) {
      return
    }
    const fileChanged = previous?.[1] !== collectionName || previous?.[2] !== fileName
    if (fileChanged) {
      resetInspector()
    }
    await loadPage('first')
  },
  { immediate: true },
)

function resetInspector() {
  keyword.value = ''
  elementType.value = null
  sort.value = 'asc'
  resetPagination()
}

function resetPagination() {
  cursorHistory.value = [null]
  pageIndex.value = 0
  nextCursor.value = null
  selectedShardId.value = ''
  selectedDetail.value = null
  mobilePane.value = 'list'
}

async function loadPage(select: 'first' | 'last' | 'keep' = 'keep') {
  loading.value = true
  error.value = null
  try {
    const page = await getDocumentShards(props.collectionName, props.fileName, {
      limit: 20,
      cursor: cursorHistory.value[pageIndex.value],
      keyword: keyword.value,
      element_type: elementType.value,
      sort: sort.value,
    })
    items.value = page.items
    total.value = page.total
    nextCursor.value = page.next_cursor
    const selectedStillVisible = items.value.some((item) => item.id === selectedShardId.value)
    if (select === 'keep' && selectedStillVisible) {
      return
    }
    const target = select === 'last' ? items.value.at(-1) : items.value[0]
    if (target) {
      await selectShard(target, false)
    } else {
      selectedShardId.value = ''
      selectedDetail.value = null
    }
  } catch (e: any) {
    error.value = e.message || '分块列表加载失败'
    items.value = []
  } finally {
    loading.value = false
  }
}

async function selectShard(shard: ChunkSummary, revealDetail = true) {
  selectedShardId.value = shard.id
  if (revealDetail && isMobile.value) {
    mobilePane.value = 'detail'
  }
  detailLoading.value = true
  detailError.value = null
  try {
    selectedDetail.value = await getShardDetail(props.collectionName, shard.id)
  } catch (e: any) {
    detailError.value = e.message || '分块详情加载失败'
    selectedDetail.value = null
  } finally {
    detailLoading.value = false
  }
}

async function applyFilters() {
  resetPagination()
  await loadPage('first')
}

async function updateElementType(value: string | null) {
  elementType.value = (value || null) as ChunkElementType | null
  await applyFilters()
}

async function updateSort(value: string) {
  sort.value = value as 'asc' | 'desc'
  await applyFilters()
}

async function previousPage(select: 'first' | 'last' = 'first') {
  if (pageIndex.value === 0) {
    return
  }
  pageIndex.value -= 1
  await loadPage(select)
}

async function nextPage(select: 'first' | 'last' = 'first') {
  if (!nextCursor.value) {
    return
  }
  cursorHistory.value = [
    ...cursorHistory.value.slice(0, pageIndex.value + 1),
    nextCursor.value,
  ]
  pageIndex.value += 1
  await loadPage(select)
}

async function moveSelection(direction: -1 | 1) {
  const nextIndex = selectedIndex.value + direction
  if (items.value[nextIndex]) {
    await selectShard(items.value[nextIndex])
    return
  }
  if (direction === 1 && nextCursor.value) {
    await nextPage('first')
  } else if (direction === -1 && pageIndex.value > 0) {
    await previousPage('last')
  }
}
</script>

<template>
  <n-drawer
    :show="show"
    :width="drawerWidth"
    placement="right"
    :trap-focus="false"
    :block-scroll="true"
    @update:show="emit('update:show', $event)"
  >
    <n-drawer-content :title="`分块浏览 · ${fileName}`" closable class="chunk-drawer">
      <div class="inspector-toolbar">
        <n-input
          v-model:value="keyword"
          clearable
          placeholder="搜索正文"
          @clear="applyFilters"
          @keyup.enter="applyFilters"
        >
          <template #prefix><n-icon><SearchOutline /></n-icon></template>
        </n-input>
        <n-select
          :value="elementType || ''"
          :options="elementOptions"
          @update:value="updateElementType"
        />
        <n-select :value="sort" :options="sortOptions" @update:value="updateSort" />
      </div>

      <div class="inspector-shell" :class="{ 'show-detail': mobilePane === 'detail' }">
        <section class="chunk-list-pane">
          <header class="list-heading">
            <div>
              <strong>分块列表</strong>
              <span>{{ pageLabel }}</span>
            </div>
          </header>

          <div v-if="loading" class="list-state"><n-spin /></div>
          <n-alert v-else-if="error" type="error" :title="error" />
          <n-empty v-else-if="!items.length" description="没有匹配的分块" />
          <div v-else class="chunk-list">
            <button
              v-for="chunk in items"
              :key="chunk.id"
              type="button"
              class="chunk-row"
              :class="{ active: chunk.id === selectedShardId }"
              @click="selectShard(chunk)"
            >
              <span class="chunk-index">#{{ chunk.chunk_index ?? '—' }}</span>
              <span class="chunk-row-main">
                <span class="chunk-path">{{ chunk.header_path || formatChunkLocator(chunk.locator) }}</span>
                <span class="chunk-preview">{{ chunk.content_preview }}</span>
                <span class="chunk-meta">
                  <n-tag size="tiny" :bordered="false">{{ chunkElementLabel(chunk.element_type) }}</n-tag>
                  <span>{{ formatChunkLocator(chunk.locator) }}</span>
                  <span>{{ chunk.char_length }} 字</span>
                  <span v-if="chunk.token_count !== null && chunk.token_count !== undefined">{{ chunk.token_count }} tokens</span>
                </span>
              </span>
              <n-icon><ChevronForward /></n-icon>
            </button>
          </div>

          <footer class="list-pagination">
            <n-button size="small" :disabled="pageIndex === 0 || loading" @click="previousPage()">
              <template #icon><n-icon><ChevronBack /></n-icon></template>
              上一页
            </n-button>
            <n-button size="small" :disabled="!nextCursor || loading" @click="nextPage()">
              下一页
              <template #icon><n-icon><ChevronForward /></n-icon></template>
            </n-button>
          </footer>
        </section>

        <section class="chunk-detail-pane">
          <header class="detail-nav">
            <n-button v-if="isMobile" quaternary size="small" @click="mobilePane = 'list'">
              <template #icon><n-icon><ArrowBack /></n-icon></template>
              返回列表
            </n-button>
            <span class="detail-nav-spacer"></span>
            <n-button
              quaternary
              size="small"
              :disabled="selectedIndex <= 0 && pageIndex === 0"
              @click="moveSelection(-1)"
            >
              上一个
            </n-button>
            <n-button
              quaternary
              size="small"
              :disabled="selectedIndex === items.length - 1 && !nextCursor"
              @click="moveSelection(1)"
            >
              下一个
            </n-button>
          </header>
          <ChunkDetailPanel
            :detail="selectedDetail"
            :loading="detailLoading"
            :error="detailError"
          />
        </section>
      </div>
    </n-drawer-content>
  </n-drawer>
</template>

<style lang="scss" scoped>
.chunk-drawer :deep(.n-drawer-body-content-wrapper) {
  display: flex;
  flex-direction: column;
  padding: 0;
  overflow: hidden;
}

.inspector-toolbar {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 132px 96px;
  gap: 8px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--noesis-color-border-subtle);
  background: var(--noesis-color-bg-muted);
}

.inspector-shell {
  display: grid;
  grid-template-columns: minmax(320px, 0.8fr) minmax(0, 1.2fr);
  flex: 1;
  min-width: 0;
  min-height: 0;
}

.chunk-list-pane,
.chunk-detail-pane {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

.chunk-list-pane {
  border-right: 1px solid var(--noesis-color-border-subtle);
  background: var(--noesis-color-bg-muted);
}

.list-heading {
  padding: 14px 16px 10px;
}

.list-heading div {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}

.list-heading strong {
  font-size: 13px;
}

.list-heading span {
  color: var(--noesis-color-text-muted);
  font-size: 11px;
}

.list-state {
  flex: 1;
  display: grid;
  place-items: center;
}

.chunk-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 4px 10px 12px;
}

.chunk-row {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 10px;
  width: 100%;
  margin-bottom: 6px;
  padding: 12px;
  border: 1px solid transparent;
  border-radius: var(--noesis-radius-sm);
  background: transparent;
  color: var(--noesis-color-text);
  text-align: left;
  cursor: pointer;
}

.chunk-row:hover {
  background: var(--noesis-color-bg-hover);
}

.chunk-row.active {
  border-color: var(--noesis-color-primary);
  background: var(--noesis-color-primary-bg-subtle);
}

.chunk-index {
  color: var(--noesis-color-primary);
  font-family: ui-monospace, monospace;
  font-size: 11px;
  font-weight: 700;
}

.chunk-row-main {
  display: grid;
  min-width: 0;
  gap: 5px;
}

.chunk-path {
  overflow: hidden;
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chunk-preview {
  display: -webkit-box;
  overflow: hidden;
  color: var(--noesis-color-text-body);
  font-size: 12px;
  line-height: 1.55;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.chunk-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 5px 10px;
  color: var(--noesis-color-text-muted);
  font-size: 10px;
}

.list-pagination,
.detail-nav {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 12px;
  border-top: 1px solid var(--noesis-color-border-subtle);
  background: var(--noesis-color-bg-elevated);
}

.detail-nav {
  justify-content: flex-end;
  border-top: none;
  border-bottom: 1px solid var(--noesis-color-border-subtle);
}

.detail-nav-spacer {
  flex: 1;
}

@media (max-width: $bp-lg) {

  .inspector-toolbar {
    grid-template-columns: minmax(0, 1fr) 112px 88px;
  }
}

@media (max-width: $bp-md) {

  .inspector-toolbar {
    grid-template-columns: minmax(0, 1fr) 112px 88px;
    padding: 10px 12px;
  }

  .inspector-shell {
    display: block;
    position: relative;
  }

  .chunk-list-pane,
  .chunk-detail-pane {
    position: absolute;
    inset: 0;
  }

  .chunk-detail-pane {
    visibility: hidden;
    transform: translateX(100%);
    background: var(--noesis-color-bg-elevated);
    transition: transform 0.2s ease;
  }

  .inspector-shell.show-detail .chunk-list-pane {
    visibility: hidden;
  }

  .inspector-shell.show-detail .chunk-detail-pane {
    visibility: visible;
    transform: translateX(0);
  }
}

@media (max-width: $bp-sm) {

  .inspector-toolbar {
    grid-template-columns: minmax(0, 1fr) 104px;
  }

  .inspector-toolbar > :first-child {
    grid-column: 1 / -1;
  }
}
</style>
