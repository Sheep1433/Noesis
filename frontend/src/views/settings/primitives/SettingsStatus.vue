<script setup lang="ts">
withDefaults(defineProps<{
  tone?: 'info' | 'success' | 'warning' | 'error'
  title?: string
}>(), {
  tone: 'info',
  title: undefined,
})
</script>

<template>
  <div class="settings-status" :class="`is-${tone}`" role="status">
    <strong v-if="title">{{ title }}</strong>
    <span><slot></slot></span>
  </div>
</template>

<style scoped>
.settings-status {
  display: flex;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--noesis-color-bg-muted, rgba(0, 0, 0, 0.04));
  color: var(--noesis-color-text-secondary);
  font-size: 12px;
}

.is-success { color: var(--noesis-color-success, #287a45); }
.is-warning { color: var(--noesis-color-warning, #9a6500); }
.is-error { color: var(--noesis-color-danger, #c2413b); }
</style>
