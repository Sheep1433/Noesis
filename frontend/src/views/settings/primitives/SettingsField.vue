<script setup lang="ts">
defineProps<{
  label: string
  description?: string
  error?: string
  required?: boolean
}>()
</script>

<template>
  <label class="settings-field">
    <span class="settings-field__label">
      {{ label }}<b v-if="required" aria-hidden="true"> *</b>
    </span>
    <span v-if="description" class="settings-field__description">{{ description }}</span>
    <slot></slot>
    <span v-if="error" class="settings-field__error" role="alert">{{ error }}</span>
  </label>
</template>

<style scoped>
.settings-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.settings-field__label {
  color: var(--noesis-color-text-heading);
  font-size: 13px;
  font-weight: 600;
}

.settings-field__description {
  color: var(--noesis-color-text-secondary);
  font-size: 12px;
}

.settings-field__error,
b {
  color: var(--noesis-color-danger, #c2413b);
  font-size: 12px;
}
</style>
