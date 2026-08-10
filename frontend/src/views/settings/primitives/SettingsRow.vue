<script setup lang="ts">
defineProps<{
  title: string
  description?: string
  disabled?: boolean
}>()
</script>

<template>
  <div class="settings-row" :class="{ 'is-disabled': disabled }">
    <div class="settings-row__main">
      <strong>{{ title }}</strong>
      <span v-if="description">{{ description }}</span>
      <slot></slot>
    </div>
    <div v-if="$slots.actions" class="settings-row__actions">
      <slot name="actions"></slot>
    </div>
  </div>
</template>

<style scoped>
.settings-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 13px 0;
  border-bottom: 1px solid var(--noesis-color-border-subtle, rgba(0, 0, 0, 0.08));
}

.settings-row__main {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
  gap: 3px;
}

strong {
  color: var(--noesis-color-text-heading);
  font-size: 14px;
}

span {
  color: var(--noesis-color-text-secondary);
  font-size: 12px;
}

.settings-row__actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 8px;
}

.is-disabled {
  opacity: 0.55;
}
</style>
