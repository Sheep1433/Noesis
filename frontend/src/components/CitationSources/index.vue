<script setup lang="ts">
import type { RetrievalResultUi } from '@/views/chat/messageParts'
import { DocumentsOutline, GlobeOutline } from '@vicons/ionicons-v5'
import { NDrawer, NDrawerContent, NIcon } from 'naive-ui'
import { computed, nextTick, ref } from 'vue'
import { useRouter } from 'vue-router'
import { citationTargets, safeWebUrl } from '@/views/chat/citationRendering'

const props = defineProps<{
  content: string
  results: RetrievalResultUi[]
}>()

const router = useRouter()
const drawerOpen = ref(false)
const selectedCitationNumber = ref<number | null>(null)
const sourceListRef = ref<HTMLElement | null>(null)

const sources = computed(() => {
  const unique = new Map<string, RetrievalResultUi>()
  for (const result of props.results) {
    const key = result.source_type === 'web'
      ? `web:${safeWebUrl(result.url) || result.evidence_id}`
      : `kb:${result.collection_name || ''}:${result.title}`
    if (!unique.has(key)) {
      unique.set(key, result)
    }
  }
  return [...unique.values()]
})

const targets = computed(() => citationTargets(
  props.content,
  props.results,
  (collectionName, fileName) => router.resolve({
    name: 'KnowledgeBaseDetail',
    params: { collectionName },
    query: { file: fileName },
  }).href,
))

function sourceHref(source: RetrievalResultUi): string | null {
  if (source.source_type === 'web') {
    return safeWebUrl(source.url)
  }
  return source.collection_name ? router.resolve(kbLocation(source)).href : null
}

const sourceGroups = computed(() => {
  const citationNumbers = new Map<string, number>()
  for (const [number, target] of targets.value) {
    citationNumbers.set(target.href, number)
  }
  const cited = sources.value
    .filter((source) => citationNumbers.has(sourceHref(source) || ''))
    .map((source) => ({ source, number: citationNumbers.get(sourceHref(source) || '')!, cited: true }))
    .sort((a, b) => a.number - b.number)
  const retrieved = sources.value
    .filter((source) => !citationNumbers.has(sourceHref(source) || ''))
    .map((source, index) => ({ source, number: index + 1, cited: false }))
  return [
    { title: '引用来源', items: cited },
    { title: '其他检索结果', items: retrieved },
  ].filter((group) => group.items.length > 0)
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
        v-for="source in sources.slice(0, 3)"
        :key="source.evidence_id"
        class="source-entry__icon"
      >
        <n-icon :size="12">
          <GlobeOutline v-if="source.source_type === 'web'" />
          <DocumentsOutline v-else />
        </n-icon>
      </span>
    </span>
    <span>来源</span>
    <span class="source-entry__count">{{ sources.length }}</span>
  </button>

  <n-drawer
    v-model:show="drawerOpen"
    placement="right"
    width="min(440px, 92vw)"
    @after-enter="scrollToSelectedCitation"
  >
    <n-drawer-content title="来源" closable>
      <div ref="sourceListRef">
        <section v-for="group in sourceGroups" :key="group.title" class="source-group">
          <h3 class="source-group__title">{{ group.title }}</h3>
          <div class="source-list">
            <article
              v-for="item in group.items"
              :key="item.source.evidence_id"
              class="source-card"
              :class="{ 'source-card--selected': item.cited && item.number === selectedCitationNumber }"
              :data-citation-number="item.cited ? item.number : undefined"
            >
              <span class="source-card__number">{{ item.number }}</span>
              <div class="source-card__body">
                <a
                  v-if="item.source.source_type === 'web' && safeWebUrl(item.source.url)"
                  class="source-card__title"
                  :href="safeWebUrl(item.source.url)!"
                  :title="item.source.title || item.source.url"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {{ item.source.title || item.source.url }}
                </a>
                <a
                  v-else-if="item.source.source_type === 'knowledge_base' && item.source.collection_name"
                  class="source-card__title source-card__link"
                  :href="router.resolve(kbLocation(item.source)).href"
                  :title="item.source.title || '知识库文档'"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {{ item.source.title || '知识库文档' }}
                </a>
                <div v-else class="source-card__title" :title="item.source.title || '来源'">
                  {{ item.source.title || '来源' }}
                </div>
                <div class="source-card__meta">
                  <n-icon :size="13">
                    <GlobeOutline v-if="item.source.source_type === 'web'" />
                    <DocumentsOutline v-else />
                  </n-icon>
                  <span>{{ sourceMeta(item.source) }}</span>
                </div>
              </div>
            </article>
          </div>
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
  align-items: center;
  gap: 8px;
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
  flex: 1;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.source-card__title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  color: var(--noesis-color-text-primary);
  font-size: 13px;
  font-weight: 600;
  line-height: 1.35;
  text-decoration: none;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.source-card__link {
  cursor: pointer;
}

.source-card__title:hover {
  color: var(--noesis-color-primary);
  text-decoration: underline;
}

.source-card__meta {
  display: flex;
  flex: 0 0 120px;
  align-items: center;
  gap: 5px;
  min-width: 0;
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
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
