<script setup lang="ts">
withDefaults(defineProps<{
  side?: 'left' | 'right'
}>(), {
  side: 'right',
})

const emit = defineEmits<{
  resizeStart: [event: PointerEvent]
}>()
</script>

<template>
  <div
    class="resize-divider"
    :class="`resize-divider--${side}`"
    role="separator"
    aria-orientation="vertical"
    aria-label="调节面板宽度"
    @pointerdown="emit('resizeStart', $event)"
  ></div>
</template>

<style scoped>
.resize-divider {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 6px;
  cursor: col-resize;
  z-index: 12;
  touch-action: none;
}

.resize-divider--right {
  right: -3px;
}

.resize-divider--left {
  left: -3px;
}

.resize-divider::after {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: 50%;
  width: 1px;
  transform: translateX(-50%);
  background: var(--noesis-color-border-aside);
  transition: background 0.15s ease, width 0.15s ease;
}

.resize-divider:hover::after,
.resize-divider:active::after {
  width: 2px;
  background: var(--noesis-color-primary-muted);
}
</style>
