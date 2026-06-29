<script setup lang="ts">
import type { Todo } from './types'
import { ChevronDown, ChevronUp, ListOutline } from '@vicons/ionicons-v5'
import { NBadge, NIcon } from 'naive-ui'
import { useNaivePresetColors } from '@/hooks/useThemePreset'

import { computed, ref } from 'vue'

const props = defineProps<{
  todos: Todo[]
}>()

const naivePresetColors = useNaivePresetColors()

const collapsed = ref(false)

const pendingCount = computed(() =>
  props.todos.filter((t) => t.status === 'pending').length,
)

const inProgressCount = computed(() =>
  props.todos.filter((t) => t.status === 'in_progress').length,
)

// 根据 PRD：隐藏条件为 Todo 列表为空（length === 0）
const shouldShow = computed(() => props.todos.length > 0)

// 分别获取各状态的 todo
const pendingTodos = computed(() => props.todos.filter((t) => t.status === 'pending'))
const inProgressTodos = computed(() => props.todos.filter((t) => t.status === 'in_progress'))
const completedTodos = computed(() => props.todos.filter((t) => t.status === 'completed'))
</script>

<template>
  <div v-if="shouldShow" class="todo-list">
    <div class="todo-header" @click="collapsed = !collapsed">
      <n-space align="center" :size="8">
        <n-icon :size="16" :color="naivePresetColors.primary">
          <ListOutline />
        </n-icon>
        <span class="todo-title">待处理事项</span>
        <n-badge
          v-if="pendingCount > 0"
          :value="pendingCount"
          :max="99"
          type="warning"
        />
        <n-badge
          v-if="inProgressCount > 0"
          :value="inProgressCount"
          :max="99"
          type="info"
        />
      </n-space>
      <n-icon :size="14" class="collapse-icon" :class="{ rotated: collapsed }">
        <ChevronUp v-if="!collapsed" />
        <ChevronDown v-else />
      </n-icon>
    </div>

    <transition name="collapse">
      <div v-show="!collapsed" class="todo-body">
        <!-- 进行中 -->
        <div v-if="inProgressCount > 0" class="todo-section in-progress">
          <div class="section-label">进行中</div>
          <div
            v-for="(todo, idx) in inProgressTodos"
            :key="`ip-${idx}`"
            class="todo-item in-progress"
          >
            <span class="todo-dot in-progress">◐</span>
            <span class="todo-text">{{ todo.content }}</span>
          </div>
        </div>

        <!-- 待处理 -->
        <div v-if="pendingCount > 0" class="todo-section pending">
          <div class="section-label">待处理</div>
          <div
            v-for="(todo, idx) in pendingTodos"
            :key="`p-${idx}`"
            class="todo-item pending"
          >
            <span class="todo-dot pending">○</span>
            <span class="todo-text">{{ todo.content }}</span>
          </div>
        </div>

        <!-- 已完成 -->
        <div v-if="completedTodos.length > 0" class="todo-section completed">
          <div class="section-label">已完成 ({{ completedTodos.length }})</div>
          <div
            v-for="(todo, idx) in completedTodos"
            :key="`c-${idx}`"
            class="todo-item completed"
          >
            <span class="todo-dot completed">✓</span>
            <span class="todo-text">{{ todo.content }}</span>
          </div>
        </div>
      </div>
    </transition>
  </div>
</template>

<style scoped>
.todo-list {
  width: 100%;
  box-sizing: border-box;
  background: rgba(92, 124, 250, 0.06);
  border: 1px solid rgba(92, 124, 250, 0.15);
  border-radius: 10px;
  margin: 0 0 8px;
  overflow: hidden;
  backdrop-filter: blur(8px);
}

.todo-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  cursor: pointer;
  user-select: none;
  transition: background 0.15s;
}

.todo-header:hover {
  background: rgba(92, 124, 250, 0.08);
}

.todo-title {
  font-size: 13px;
  font-weight: 600;
  color: #495057;
}

.collapse-icon {
  color: #999;
  transition: transform 0.2s ease;
}

.collapse-icon.rotated {
  transform: rotate(180deg);
}

.todo-body {
  padding: 0 14px 12px;
}

.todo-section {
  margin-bottom: 8px;
}

.todo-section:last-child {
  margin-bottom: 0;
}

.section-label {
  font-size: 11px;
  color: #999;
  margin-bottom: 4px;
  padding-left: 2px;
}

.todo-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
}

.todo-dot {
  font-size: 14px;
  flex-shrink: 0;
  line-height: 1;
}

.todo-dot.pending {
  color: #bbb;
}

.todo-dot.in_progress {
  color: var(--noesis-color-primary);
  animation: pulse 1.5s ease-in-out infinite;
}

.todo-text {
  font-size: 13px;
  color: #333;
  line-height: 1.4;
}

.todo-item.completed .todo-text {
  text-decoration: line-through;
  color: #bbb;
}

/* 折叠动画 */
.collapse-enter-active,
.collapse-leave-active {
  transition: max-height 0.2s ease, opacity 0.2s ease;
  max-height: 500px;
  overflow: hidden;
}

.collapse-enter-from,
.collapse-leave-to {
  max-height: 0;
  opacity: 0;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}
</style>
