<script lang="ts" setup>
import type { MentionCandidate } from '@/hooks/useMentionCatalog'
import { fuzzyFilter } from '@/utils/mentionFuzzy'

const props = defineProps<{
  open: boolean
  query: string
  candidates: MentionCandidate[]
  loading?: boolean
}>()

const emit = defineEmits<{
  select: [MentionCandidate]
  close: []
}>()

const activeIndex = ref(0)
const listRef = ref<HTMLElement | null>(null)

const filtered = computed(() =>
  fuzzyFilter(props.candidates, props.query, (c) => `${c.label} ${c.description || ''} ${c.path || ''} ${c.id || ''}`),
)

watch(
  () => [props.open, props.query] as const,
  () => {
    if (props.open) {
      activeIndex.value = 0
    }
  },
)

watch(filtered, (list) => {
  if (activeIndex.value >= list.length) {
    activeIndex.value = Math.max(0, list.length - 1)
  }
})

function kindIcon(kind: string) {
  switch (kind) {
    case 'skill':
      return 'i-carbon:notebook'
    case 'subagent':
      return 'i-carbon:bot'
    case 'folder':
      return 'i-carbon:folder'
    default:
      return 'i-carbon:document'
  }
}

function onSelect(item: MentionCandidate) {
  emit('select', item)
}

function move(delta: number) {
  const len = filtered.value.length
  if (!len) {
    return
  }
  activeIndex.value = (activeIndex.value + delta + len) % len
  nextTick(() => {
    const el = listRef.value?.querySelector<HTMLElement>(`[data-idx="${activeIndex.value}"]`)
    el?.scrollIntoView({ block: 'nearest' })
  })
}

function onKeydown(e: KeyboardEvent) {
  if (!props.open) {
    return
  }
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    e.stopPropagation()
    move(1)
    return
  }
  if (e.key === 'ArrowUp') {
    e.preventDefault()
    e.stopPropagation()
    move(-1)
    return
  }
  if (e.key === 'Enter' && !e.shiftKey) {
    const hit = filtered.value[activeIndex.value]
    if (hit) {
      e.preventDefault()
      e.stopPropagation()
      onSelect(hit)
    }
    return
  }
  if (e.key === 'Tab') {
    const hit = filtered.value[activeIndex.value]
    if (hit) {
      e.preventDefault()
      e.stopPropagation()
      onSelect(hit)
    }
    return
  }
  if (e.key === 'Escape') {
    e.preventDefault()
    e.stopPropagation()
    emit('close')
  }
}

defineExpose({ onKeydown })
</script>

<template>
  <div
    v-if="open"
    class="mention-picker"
    role="listbox"
    @mousedown.prevent
  >
    <div v-if="loading" class="mention-picker__empty">
      加载中…
    </div>
    <div v-else-if="!filtered.length" class="mention-picker__empty">
      无匹配项
    </div>
    <ul
      v-else
      ref="listRef"
      class="mention-picker__list"
    >
      <li
        v-for="(item, idx) in filtered"
        :key="`${item.kind}:${item.id || item.path || item.label}`"
        :data-idx="idx"
        class="mention-picker__item"
        :class="{ 'mention-picker__item--active': idx === activeIndex }"
        role="option"
        :aria-selected="idx === activeIndex"
        @mouseenter="activeIndex = idx"
        @click="onSelect(item)"
      >
        <span
          class="mention-picker__icon"
          :class="kindIcon(item.kind)"
          aria-hidden="true"
        ></span>
        <span class="mention-picker__label">{{ item.label }}</span>
        <span v-if="item.description" class="mention-picker__desc">{{ item.description }}</span>
      </li>
    </ul>
  </div>
</template>

<style scoped lang="scss">
.mention-picker {
  position: absolute;
  left: 12px;
  right: 12px;
  bottom: calc(100% + 6px);
  z-index: 20;
  max-height: 240px;
  overflow: auto;
  border: 1px solid var(--noesis-border, #d0d0d0);
  border-radius: 10px;
  background: var(--noesis-bg-elevated, #fff);
  box-shadow: 0 8px 24px rgb(0 0 0 / 12%);
}

.mention-picker__list {
  margin: 0;
  padding: 4px;
  list-style: none;
}

.mention-picker__item {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 8px 10px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 13px;
  line-height: 1.35;
}

.mention-picker__item--active {
  background: var(--noesis-bg-muted, rgb(0 0 0 / 6%));
}

.mention-picker__icon {
  flex-shrink: 0;
  width: 16px;
  height: 16px;
  color: var(--noesis-text-secondary, #888);
  font-size: 16px;
}

.mention-picker__label {
  flex-shrink: 0;
  font-weight: 560;
  color: var(--noesis-text, #222);
}

.mention-picker__desc {
  overflow: hidden;
  color: var(--noesis-text-secondary, #888);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mention-picker__empty {
  padding: 12px;
  color: var(--noesis-text-secondary, #888);
  font-size: 13px;
}
</style>
