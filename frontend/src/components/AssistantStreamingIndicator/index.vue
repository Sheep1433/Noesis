<script setup lang="ts">
/** 助手回复流式进行中：卡片内工具栏上方(section)、卡片底栏(embedded)、或独立块 */
withDefaults(
  defineProps<{
    embedded?: boolean
    /** 卡片内、用量/工具栏上方 */
    section?: boolean
    label?: string
  }>(),
  {
    embedded: false,
    section: false,
    label: '正在继续生成',
  },
)
</script>

<template>
  <div
    class="assistant-streaming-indicator"
    :class="{
      'assistant-streaming-indicator--embedded': embedded,
      'assistant-streaming-indicator--section': section,
    }"
    role="status"
    aria-live="polite"
    :aria-label="label"
  >
    <span class="assistant-streaming-indicator__dots" aria-hidden="true">
      <span class="assistant-streaming-indicator__dot" />
      <span class="assistant-streaming-indicator__dot" />
      <span class="assistant-streaming-indicator__dot" />
    </span>
    <span class="assistant-streaming-indicator__label">{{ label }}</span>
  </div>
</template>

<style scoped lang="scss">
.assistant-streaming-indicator {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  box-sizing: border-box;
  width: 80%;
  margin: 8px 10% 0;
  padding: 10px 14px;
  overflow: hidden;
  background: linear-gradient(180deg, #fbfcfe 0%, #f4f6fb 100%);
  border: 1px solid #e1e6ef;
  border-radius: 12px;
  border-left: 3px solid #5b8bd9;
  box-shadow: 0 1px 2px rgb(15 23 42 / 5%);
}

.assistant-streaming-indicator::before {
  content: "";
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 2px;
  background: linear-gradient(
    90deg,
    transparent 0%,
    rgb(91 139 217 / 35%) 35%,
    rgb(91 139 217 / 85%) 50%,
    rgb(91 139 217 / 35%) 65%,
    transparent 100%
  );
  background-size: 200% 100%;
  animation: assistant-stream-shimmer 1.6s ease-in-out infinite;
}

.assistant-streaming-indicator--embedded {
  width: 100%;
  margin: 0;
  padding: 10px 16px 12px;
  background: linear-gradient(180deg, rgb(248 250 253 / 0%) 0%, #f6f8fc 100%);
  border: none;
  border-top: 1px solid #eef1f6;
  border-left: none;
  border-radius: 0 0 15px 15px;
  box-shadow: none;
}

.assistant-streaming-indicator--section {
  width: 100%;
  margin: 0;
  padding: 10px 16px;
  background: linear-gradient(180deg, rgb(248 250 253 / 0%) 0%, #f6f8fc 100%);
  border: none;
  border-top: 1px solid #eef1f6;
  border-left: none;
  border-radius: 0;
  box-shadow: none;
}

.assistant-streaming-indicator--section::before {
  display: none;
}

.assistant-streaming-indicator__dots {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  flex-shrink: 0;
}

.assistant-streaming-indicator__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #5b8bd9;
  animation: assistant-stream-dot 1.1s ease-in-out infinite;
}

.assistant-streaming-indicator__dot:nth-child(2) {
  animation-delay: 0.16s;
}

.assistant-streaming-indicator__dot:nth-child(3) {
  animation-delay: 0.32s;
}

.assistant-streaming-indicator__label {
  font-size: 13px;
  line-height: 1.4;
  color: #5c6b82;
  letter-spacing: 0.02em;
}

@keyframes assistant-stream-dot {

  0%,
  70%,
  100% {
    transform: translateY(0);
    opacity: 0.4;
  }

  35% {
    transform: translateY(-3px);
    opacity: 1;
  }
}

@keyframes assistant-stream-shimmer {

  0% {
    background-position: 100% 0;
  }

  100% {
    background-position: -100% 0;
  }
}
</style>
