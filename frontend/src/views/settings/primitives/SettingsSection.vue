<script setup lang="ts">
defineProps<{
  title: string
  description?: string
}>()
</script>

<template>
  <section class="settings-section">
    <header class="settings-section__header">
      <div>
        <h2>{{ title }}</h2>
        <p v-if="description">{{ description }}</p>
      </div>
      <div v-if="$slots.actions" class="settings-section__actions">
        <slot name="actions"></slot>
      </div>
    </header>
    <slot></slot>
  </section>
</template>

<style scoped>
.settings-section {
  max-width: 880px;
}

.settings-section__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}

h2 {
  margin: 0;
  color: var(--noesis-color-text-heading);
  font-size: 18px;
}

p {
  margin: 6px 0 0;
  color: var(--noesis-color-text-secondary);
  font-size: 13px;
}

.settings-section__actions {
  display: flex;
  gap: 8px;
}
</style>
