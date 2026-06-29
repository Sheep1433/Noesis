<script setup lang="ts">
import { ColorPaletteOutline } from '@vicons/ionicons-v5'
import type { DropdownOption } from 'naive-ui'
import { NDropdown, NIcon } from 'naive-ui'
import { computed } from 'vue'
import type { ThemePresetId } from '@/config/themePresets'
import { useThemePreset } from '@/hooks/useThemePreset'

const { presetId, presets, applyThemePreset } = useThemePreset()

const dropdownOptions = computed<DropdownOption[]>(() =>
  presets.map(item => ({
    key: item.id,
    label: item.label,
    props: {
      title: item.description,
    },
  })),
)

function handleSelect(key: string | number) {
  applyThemePreset(String(key) as ThemePresetId)
}
</script>

<template>
  <n-dropdown
    trigger="click"
    placement="right-start"
    :options="dropdownOptions"
    @select="handleSelect"
  >
    <button
      type="button"
      class="theme-switcher-trigger"
      :class="{ 'theme-switcher-trigger--active': presetId !== 'light' }"
      aria-label="切换界面风格"
      :title="`界面风格：${presets.find(p => p.id === presetId)?.label ?? '浅色'}`"
    >
      <n-icon :size="20">
        <ColorPaletteOutline />
      </n-icon>
    </button>
  </n-dropdown>
</template>

<style scoped>
.theme-switcher-trigger {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  margin: 0 auto 12px;
  padding: 0;
  border: 1px solid var(--noesis-color-border, #e8eaf3);
  border-radius: 50%;
  background: var(--noesis-color-bg-elevated, #fff);
  color: var(--noesis-color-text-muted, #64748b);
  cursor: pointer;
  transition: color 0.15s ease, border-color 0.15s ease, background-color 0.15s ease;
}

.theme-switcher-trigger:hover,
.theme-switcher-trigger--active {
  color: var(--noesis-color-primary, #5c7cfa);
  border-color: var(--noesis-color-primary-muted, #a48ef4);
  background: var(--noesis-color-primary-bg-subtle, rgb(92 124 250 / 4%));
}
</style>
