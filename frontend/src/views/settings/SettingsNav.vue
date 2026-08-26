<script setup lang="ts">
import type { SettingsSectionId } from './registry'
import { computed, nextTick, ref } from 'vue'
import { useBreakpoint } from '@/hooks/useBreakpoint'
import { filterSettingsSections } from './registry'

const props = defineProps<{
  section: SettingsSectionId
}>()

const emit = defineEmits<{
  (e: 'select', value: SettingsSectionId): void
}>()

const { isMobile } = useBreakpoint()

const query = ref('')
const buttonRefs = ref<HTMLButtonElement[]>([])
const items = computed(() => filterSettingsSections(query.value))

function setButtonRef(el: unknown, index: number) {
  if (el instanceof HTMLButtonElement) {
    buttonRefs.value[index] = el
  }
}

async function onKeydown(event: KeyboardEvent, index: number) {
  if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
    return
  }
  event.preventDefault()
  const last = items.value.length - 1
  const next = event.key === 'Home'
    ? 0
    : event.key === 'End'
      ? last
      : event.key === 'ArrowDown'
        ? (index + 1) % items.value.length
        : (index - 1 + items.value.length) % items.value.length
  await nextTick()
  buttonRefs.value[next]?.focus()
}
</script>

<template>
  <aside class="settings-nav-wrap" :class="{ 'settings-nav-wrap--mobile': isMobile }">
    <label class="settings-nav-search">
      <span class="sr-only">搜索设置</span>
      <input v-model="query" type="search" placeholder="搜索设置" aria-label="搜索设置" autocomplete="off">
    </label>
    <nav class="settings-nav" aria-label="设置导航">
      <button
        v-for="(item, index) in items"
        :key="item.id"
        :ref="el => setButtonRef(el, index)"
        type="button"
        class="settings-nav__item"
        :class="{ 'is-active': props.section === item.id }"
        :aria-current="props.section === item.id ? 'page' : undefined"
        @click="emit('select', item.id)"
        @keydown="onKeydown($event, index)"
      >
        <span>{{ item.label }}</span>
        <small>{{ item.description }}</small>
      </button>
      <p v-if="items.length === 0" class="settings-nav__empty">
        没有匹配的设置
      </p>
    </nav>
  </aside>
</template>

<style lang="scss" scoped>
.settings-nav-wrap {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 180px;
}

.settings-nav-search input {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid var(--noesis-color-border-subtle, rgba(0, 0, 0, 0.1));
  border-radius: 8px;
  background: var(--noesis-color-bg-surface, transparent);
  color: var(--noesis-color-text-heading);
  padding: 8px 10px;
  outline: none;
}

.settings-nav-search input:focus {
  border-color: var(--noesis-color-border-strong, rgba(0, 0, 0, 0.28));
}

.settings-nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.settings-nav__item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  text-align: left;
  border: none;
  background: transparent;
  color: var(--noesis-color-text-secondary);
  padding: 8px 12px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
}

.settings-nav__item small {
  color: var(--noesis-color-text-tertiary, var(--noesis-color-text-secondary));
  font-size: 11px;
  font-weight: 400;
}

.settings-nav__empty {
  margin: 8px 4px;
  color: var(--noesis-color-text-secondary);
  font-size: 12px;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.settings-nav__item:hover {
  background: var(--noesis-color-bg-hover, rgba(0, 0, 0, 0.04));
  color: var(--noesis-color-text-heading);
}

.settings-nav__item.is-active {
  background: var(--noesis-color-bg-muted, rgba(0, 0, 0, 0.06));
  color: var(--noesis-color-text-heading);
  font-weight: 600;
}

.settings-nav-wrap--mobile .settings-nav {
  flex-direction: row;
  flex-wrap: nowrap;
  overflow-x: auto;
  min-width: 0;
  padding-right: 20px;
  scrollbar-width: none;
}

.settings-nav-wrap--mobile .settings-nav::-webkit-scrollbar {
  display: none;
}

.settings-nav-wrap--mobile .settings-nav__item {
  flex: 0 0 auto;
}

.settings-nav-wrap--mobile .settings-nav__item small {
  display: none;
}

@media (max-width: $bp-md) {
  .settings-nav {
    flex-direction: row;
    flex-wrap: nowrap;
    overflow-x: auto;
    min-width: 0;
  }

  .settings-nav-wrap {
    min-width: 0;
  }

  .settings-nav__item {
    flex: 0 0 auto;
  }

  .settings-nav__item small {
    display: none;
  }
}
</style>
