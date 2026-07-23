<script setup lang="ts">
export type SettingsSection =
  | 'overview'
  | 'profile'
  | 'memory'
  | 'capabilities'
  | 'automation'
  | 'channels'
  | 'account'

const props = defineProps<{
  section: SettingsSection
}>()

const emit = defineEmits<{
  (e: 'update:section', value: SettingsSection): void
}>()

const items: { key: SettingsSection, label: string }[] = [
  { key: 'overview', label: '概览' },
  { key: 'profile', label: '画像' },
  { key: 'memory', label: '记忆' },
  { key: 'capabilities', label: '扩展' },
  { key: 'automation', label: '自动化' },
  { key: 'channels', label: '通讯' },
  { key: 'account', label: '账户' },
]
</script>

<template>
  <nav class="settings-nav" aria-label="设置导航">
    <button
      v-for="item in items"
      :key="item.key"
      type="button"
      class="settings-nav__item"
      :class="{ 'is-active': props.section === item.key }"
      @click="emit('update:section', item.key)"
    >
      {{ item.label }}
    </button>
  </nav>
</template>

<style scoped>
.settings-nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 140px;
}

.settings-nav__item {
  text-align: left;
  border: none;
  background: transparent;
  color: var(--noesis-color-text-secondary);
  padding: 8px 12px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
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

@media (max-width: 768px) {
  .settings-nav {
    flex-direction: row;
    flex-wrap: wrap;
    min-width: 0;
  }
}
</style>
