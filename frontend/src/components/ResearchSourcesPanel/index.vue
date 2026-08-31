<script setup lang="ts">
import type { RetrievalResultUi } from '@/views/chat/messageParts'
import type { ArcSourceEntry } from '@/views/chat/researchArcs'
import { DocumentsOutline, GlobeOutline } from '@vicons/ionicons-v5'
import { NCollapse, NCollapseItem, NDrawer, NDrawerContent, NIcon } from 'naive-ui'
import { computed, nextTick, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useBreakpoint } from '@/hooks/useBreakpoint'
import { safeWebUrl } from '@/views/chat/citationRendering'

const props = defineProps<{
  /** 弧内去重来源（首见序；序号 = index+1，与正文 badge 编号一致） */
  entries: ArcSourceEntry[]
  /** 被交付正文引用（URL 归因）的条目 key 集合 */
  citedKeys: Set<string>
}>()

const router = useRouter()
const { isMobile } = useBreakpoint()
const drawerOpen = ref(false)
const selectedCitationNumber = ref<number | null>(null)
const sourceListRef = ref<HTMLElement | null>(null)

/** 计数为去重数（条目已按 canonical URL 去重） */
const totalCount = computed(() => props.entries.length)
const citedCount = computed(() => props.entries.filter((e) => props.citedKeys.has(e.key)).length)

const citedEntries = computed(() =>
  props.entries.map((entry, index) => ({ entry, number: index + 1 }))
    .filter((item) => props.citedKeys.has(item.entry.key)),
)

interface SourceGroup {
  id: string
  title: string
  items: Array<{ entry: ArcSourceEntry, number: number }>
}

/** 按贡献者分组（主 Agent 检索优先，子 Agent 按首见序）；条目在涉及的组内重复出现 */
const groups = computed<SourceGroup[]>(() => {
  const main: SourceGroup = { id: 'main', title: '主 Agent 检索', items: [] }
  const subagent = new Map<string, SourceGroup>()
  props.entries.forEach((entry, index) => {
    const item = { entry, number: index + 1 }
    for (const origin of entry.origins) {
      if (origin.kind === 'main') {
        main.items.push(item)
      } else {
        const label = origin.label || '子 Agent'
        let group = subagent.get(label)
        if (!group) {
          group = { id: `subagent:${label}`, title: `子 Agent「${label}」`, items: [] }
          subagent.set(label, group)
        }
        group.items.push(item)
      }
    }
  })
  return [main, ...subagent.values()].filter((group) => group.items.length > 0)
})

function kbLocation(result: RetrievalResultUi) {
  return {
    name: 'KnowledgeBaseDetail',
    params: { collectionName: result.collection_name },
    query: { file: result.title },
  }
}

function sourceMeta(source: RetrievalResultUi): string {
  const url = safeWebUrl(source.url)
  return source.source_type === 'web' && url
    ? new URL(url).hostname
    : source.collection_name || '知识库'
}

function open(citationNumber?: number) {
  selectedCitationNumber.value = citationNumber || null
  drawerOpen.value = true
}

function scrollToSelectedCitation() {
  const number = selectedCitationNumber.value
  if (!number) {
    return
  }
  void nextTick(() => {
    sourceListRef.value
      ?.querySelector<HTMLElement>(`[data-citation-number="${number}"]`)
      ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  })
}

defineExpose({ open })
</script>

<template>
  <button type="button" class="source-entry__button" @click="open()">
    <span class="source-entry__icons" aria-hidden="true">
      <span
        v-for="item in citedEntries.length ? citedEntries.slice(0, 3) : entries.slice(0, 3)"
        :key="item.entry.key"
        class="source-entry__icon"
      >
        <n-icon :size="12">
          <GlobeOutline v-if="item.entry.result.source_type === 'web'" />
          <DocumentsOutline v-else />
        </n-icon>
      </span>
    </span>
    <span>来源</span>
    <span class="source-entry__count">{{ totalCount }}</span>
  </button>

  <n-drawer
    v-model:show="drawerOpen"
    class="source-drawer"
    :class="{ 'source-drawer--mobile': isMobile }"
    :placement="isMobile ? 'bottom' : 'right'"
    :width="isMobile ? '100%' : 'min(440px, 92vw)'"
    :height="isMobile ? 'min(78vh, 620px)' : undefined"
    @after-enter="scrollToSelectedCitation"
  >
    <n-drawer-content :title="citedCount ? `引用 ${citedCount} · 共检索 ${totalCount}` : `共检索 ${totalCount}`" closable>
      <div ref="sourceListRef">
        <section v-if="citedEntries.length" class="source-group">
          <h4 class="source-group__title">引用（默认展开）</h4>
          <div class="source-list">
            <article
              v-for="item in citedEntries"
              :key="item.entry.key"
              class="source-card"
              :data-citation-number="item.number"
            >
              <span class="source-card__number">{{ item.number }}</span>
              <div class="source-card__body">
                <a
                  v-if="item.entry.result.source_type === 'web' && safeWebUrl(item.entry.result.url)"
                  class="source-card__title source-card__link"
                  :href="safeWebUrl(item.entry.result.url)!"
                  :title="item.entry.result.title || item.entry.result.url"
                  target="_blank"
                  rel="noopener noreferrer"
                >{{ item.entry.result.title || item.entry.result.url }}</a>
                <a
                  v-else-if="item.entry.result.source_type === 'knowledge_base' && item.entry.result.collection_name"
                  class="source-card__title source-card__link"
                  :href="router.resolve(kbLocation(item.entry.result)).href"
                  :title="item.entry.result.title || '知识库文档'"
                  target="_blank"
                  rel="noopener noreferrer"
                >{{ item.entry.result.title || '知识库文档' }}</a>
                <div v-else class="source-card__title" :title="item.entry.result.title || '来源'">
                  {{ item.entry.result.title || '来源' }}
                </div>
                <div class="source-card__meta">
                  <n-icon :size="13">
                    <GlobeOutline v-if="item.entry.result.source_type === 'web'" />
                    <DocumentsOutline v-else />
                  </n-icon>
                  <span>{{ sourceMeta(item.entry.result) }}</span>
                  <span v-if="item.entry.origins.length > 1" class="source-card__origins">
                    {{ item.entry.origins.length }} 个来源
                  </span>
                </div>
              </div>
            </article>
          </div>
        </section>

        <section class="source-group source-group--contributors">
          <h4 class="source-group__title">按贡献者（{{ totalCount }} 条去重来源）</h4>
          <n-collapse>
            <n-collapse-item
              v-for="group in groups"
              :key="group.id"
              :name="group.id"
              :title="`${group.title} · ${group.items.length}`"
            >
              <div class="source-list">
                <article
                  v-for="item in group.items"
                  :key="`${group.id}:${item.entry.key}`"
                  class="source-card"
                  :data-citation-number="item.number"
                >
                  <span class="source-card__number">{{ item.number }}</span>
                  <div class="source-card__body">
                    <a
                      v-if="item.entry.result.source_type === 'web' && safeWebUrl(item.entry.result.url)"
                      class="source-card__title source-card__link"
                      :href="safeWebUrl(item.entry.result.url)!"
                      :title="item.entry.result.title || item.entry.result.url"
                      target="_blank"
                      rel="noopener noreferrer"
                    >{{ item.entry.result.title || item.entry.result.url }}</a>
                    <a
                      v-else-if="item.entry.result.source_type === 'knowledge_base' && item.entry.result.collection_name"
                      class="source-card__title source-card__link"
                      :href="router.resolve(kbLocation(item.entry.result)).href"
                      :title="item.entry.result.title || '知识库文档'"
                      target="_blank"
                      rel="noopener noreferrer"
                    >{{ item.entry.result.title || '知识库文档' }}</a>
                    <div v-else class="source-card__title" :title="item.entry.result.title || '来源'">
                      {{ item.entry.result.title || '来源' }}
                    </div>
                    <div class="source-card__meta">
                      <n-icon :size="13">
                        <GlobeOutline v-if="item.entry.result.source_type === 'web'" />
                        <DocumentsOutline v-else />
                      </n-icon>
                      <span>{{ sourceMeta(item.entry.result) }}</span>
                    </div>
                    <div v-if="item.entry.origins.length > 1" class="source-card__badges">
                      <span
                        v-for="origin in item.entry.origins"
                        :key="`${item.entry.key}:${origin.kind}:${origin.label || ''}`"
                        class="origin-badge"
                        :class="{ 'origin-badge--main': origin.kind === 'main' }"
                      >
                        {{ origin.kind === 'main' ? '主 Agent' : (origin.label || '子 Agent') }}
                      </span>
                    </div>
                  </div>
                </article>
              </div>
            </n-collapse-item>
          </n-collapse>
        </section>
      </div>
    </n-drawer-content>
  </n-drawer>
</template>

<style scoped>
.source-entry__button {
  display: inline-flex;
  align-items: center;
  width: auto;
  min-height: 26px;
  border: 0;
  padding: 0 4px;
  border-radius: var(--noesis-radius-md);
  background: transparent;
  color: var(--noesis-color-text-secondary);
  cursor: pointer;
  font-size: 11px;
  transition: color 0.15s ease, background-color 0.15s ease;
}

.source-entry__button:hover {
  color: var(--noesis-color-primary);
  background: var(--noesis-color-primary-bg-subtle);
}

.source-entry__icons {
  display: inline-flex;
  margin-right: 3px;
}

.source-entry__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  margin-left: -1px;
  color: var(--noesis-color-text-hint);
}

.source-entry__icon:first-child {
  margin-left: 0;
}

.source-entry__count {
  margin-left: 2px;
  color: var(--noesis-color-text-secondary);
}

.source-list {
  display: grid;
  gap: 2px;
  max-width: 100%;
  overflow-x: hidden;
}

.source-group + .source-group {
  margin-top: 14px;
}

.source-group__title {
  margin: 0 0 5px;
  color: var(--noesis-color-text-secondary);
  font-size: 13px;
  font-weight: 600;
}

.source-card {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  min-width: 0;
  max-width: 100%;
  padding: 6px 8px;
  border-radius: var(--noesis-radius-sm);
}

.source-card:hover,
.source-card--selected {
  background: var(--noesis-color-primary-bg-icon);
}

.source-card__number {
  display: flex;
  flex: 0 0 20px;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  color: var(--noesis-color-primary);
  background: var(--noesis-color-primary-bg-icon);
  border-radius: 50%;
  font-size: 12px;
  font-weight: 600;
}

.source-card__body {
  display: flex;
  flex-direction: column;
  flex: 1;
  align-items: flex-start;
  gap: 3px;
  min-width: 0;
  max-width: 100%;
}

.source-card__title {
  display: block;
  flex: 1;
  min-width: 0;
  max-width: 100%;
  overflow-wrap: anywhere;
  color: var(--noesis-color-text-primary);
  font-size: 13px;
  font-weight: 600;
  line-height: 1.35;
  text-decoration: none;
  white-space: normal;
  overflow: hidden;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.source-card__link {
  cursor: pointer;
}

.source-card__title:hover,
.source-card__link:hover {
  color: var(--noesis-color-primary);
  text-decoration: underline;
}

.source-card__meta {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 5px;
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
  color: var(--noesis-color-text-secondary);
  font-size: 11px;
  white-space: nowrap;
}

.source-card__meta :deep(.n-icon) {
  flex-shrink: 0;
}

.source-card__meta span {
  flex: 1;
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
}

.source-card__origins {
  color: var(--noesis-color-primary);
}

.source-card__badges {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  max-width: 100%;
}

.origin-badge {
  display: inline-flex;
  align-items: center;
  max-width: 100%;
  padding: 1px 6px;
  border-radius: var(--noesis-radius-sm);
  background: var(--noesis-color-primary-bg-subtle);
  color: var(--noesis-color-text-secondary);
  font-size: 10px;
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.origin-badge--main {
  background: var(--noesis-color-primary-bg-icon);
  color: var(--noesis-color-primary);
}

.source-drawer--mobile :deep(.n-drawer-content) {
  border-radius: 16px 16px 0 0;
}
</style>
