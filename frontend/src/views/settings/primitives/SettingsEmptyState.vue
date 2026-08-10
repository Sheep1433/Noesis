<script setup lang="ts">
defineProps<{
  title: string
  description?: string
}>()
</script>

<template>
  <div class="settings-empty">
    <strong>{{ title }}</strong>
    <p v-if="description">{{ description }}</p>
    <slot></slot>
  </div>
</template>

<style scoped>
.settings-empty {
  padding: 32px 16px;
  text-align: center;
  color: var(--noesis-color-text-secondary);
}

strong {
  color: var(--noesis-color-text-heading);
}

p {
  margin: 6px 0 14px;
  font-size: 13px;
}
</style>
