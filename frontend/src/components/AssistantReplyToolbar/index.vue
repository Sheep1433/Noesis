<script lang="ts" setup>
import { computed } from 'vue'
import QatypeIcon from '@/components/IconFont/QatypeIcon.vue'
import { copyToClipboard } from '@/utils/copy'

const props = withDefaults(defineProps<{
  qaType?: string
  copyText?: string
  /** 与 SSE message-start.langfuse_session_id 一致 */
  langfuse_session_id?: string
  /** VITE_LANGFUSE_UI_ORIGIN，非空时显示「观测」 */
  langfuseUiOrigin?: string
}>(), {
  qaType: 'COMMON_QA',
  copyText: '',
  langfuse_session_id: '',
  langfuseUiOrigin: '',
})

const emit = defineEmits<{
  praiseFeadBack: []
  belittleFeedback: []
  recycleQa: []
}>()

const showLangfuse = computed(
  () => Boolean(props.langfuse_session_id?.trim() && props.langfuseUiOrigin?.trim()),
)

function openLangfuseUi() {
  const origin = String(props.langfuseUiOrigin || '').replace(/\/$/, '')
  if (!origin) {
    return
  }
  window.open(origin, '_blank', 'noopener,noreferrer')
}

const handlePassClip = async () => {
  const text = props.copyText || ''
  if (!text.trim()) {
    window.$ModalMessage.destroyAll()
    window.$ModalMessage.warning('暂无可复制内容')
    return
  }
  try {
    await copyToClipboard(text)
    window.$ModalMessage.destroyAll()
    window.$ModalMessage.success('已复制')
  } catch {
    window.$ModalMessage.destroyAll()
    window.$ModalMessage.error('复制失败')
  }
}
</script>

<template>
  <div class="assistant-reply-toolbar">
    <div class="assistant-reply-toolbar__left">
      <QatypeIcon :qa_type="qaType" />
      <n-tooltip v-if="showLangfuse" placement="top">
        <template #trigger>
          <n-button
            type="default"
            ghost
            size="tiny"
            :bordered="false"
            @click="openLangfuseUi"
          >
            观测
          </n-button>
        </template>
        <div style="max-width: 280px; font-size: 12px; line-height: 1.5">
          Langfuse 会话 ID：<span style="word-break: break-all">{{ langfuse_session_id }}</span>
          。点击打开控制台后在 Session / Traces 中检索。
        </div>
      </n-tooltip>
    </div>
    <div class="assistant-reply-toolbar__actions">
      <n-button
        icon-placement="left"
        type="default"
        ghost
        size="tiny"
        :bordered="false"
        class="assistant-reply-toolbar__btn"
        @click="emit('praiseFeadBack')"
      >
        <template #icon>
          <n-icon size="20" class="assistant-reply-toolbar__icon">
            <svg t="1734514601988" class="icon" viewBox="0 0 1024 1024" version="1.1" xmlns="http://www.w3.org/2000/svg" p-id="13495" width="200" height="200">
              <path d="M757.76 852.906667c36.906667-0.021333 72.832-30.208 79.296-66.56l51.093333-287.04c10.069333-56.469333-27.093333-100.522667-84.373333-100.096l-10.261333 0.085333a19972.266667 19972.266667 0 0 1-52.842667 0.362667 3552.853333 3552.853333 0 0 1-56.746667 0l-30.997333-0.426667 11.498667-28.8c10.24-25.642667 21.76-95.744 21.504-128.021333-0.618667-73.045333-31.36-114.858667-69.290667-114.410667-46.613333 0.554667-69.461333 23.466667-69.333333 91.136 0.213333 112.661333-102.144 226.112-225.130667 225.109333a1214.08 1214.08 0 0 0-20.629333 0l-3.52 0.042667c-0.192 0 0.64 409.109333 0.64 409.109333 0-0.085333 459.093333-0.490667 459.093333-0.490666z m-17.301333-495.914667a15332.288 15332.288 0 0 0 52.693333-0.362667l10.282667-0.085333c84.010667-0.618667 141.44 67.52 126.72 150.250667L879.061333 793.813333c-10.090667 56.661333-63.68 101.696-121.258666 101.76l-458.922667 0.384A42.666667 42.666667 0 0 1 256 853.546667l-0.853333-409.173334a42.624 42.624 0 0 1 42.346666-42.730666l3.669334-0.042667c5.909333-0.064 13.12-0.064 21.333333 0 98.176 0.789333 182.293333-92.437333 182.144-182.378667C504.469333 128.021333 546.24 86.186667 616.106667 85.333333c65.173333-0.768 111.68 62.506667 112.448 156.714667 0.256 28.48-6.848 78.826667-15.701334 115.050667 8.021333 0 17.28-0.042667 27.584-0.106667zM170.666667 448v405.333333h23.466666a21.333333 21.333333 0 0 1 0 42.666667H154.837333A26.709333 26.709333 0 0 1 128 869.333333v-437.333333c0-14.784 12.074667-26.666667 26.773333-26.666667h38.912a21.333333 21.333333 0 0 1 0 42.666667H170.666667z" fill="currentColor" p-id="13496" />
            </svg>
          </n-icon>
        </template>
      </n-button>
      <n-button
        icon-placement="left"
        type="default"
        ghost
        size="tiny"
        :bordered="false"
        class="assistant-reply-toolbar__btn"
        @click="emit('belittleFeedback')"
      >
        <template #icon>
          <n-icon size="20" class="assistant-reply-toolbar__icon">
            <svg t="1734514913827" class="icon" viewBox="0 0 1024 1024" version="1.1" xmlns="http://www.w3.org/2000/svg" p-id="22122" width="200" height="200">
              <path d="M936.6784 451.56352c21.48352 80.11776-26.24 162.75456-106.40896 184.23808-11.78624 3.1488-24.13056 4.62336-38.81984 4.62336l-100.63872 0.41472c12.50304 56.94976 14.55104 142.49472 14.55104 193.44896 0 59.30496-48.16896 107.4944-107.40224 107.4944-59.21792 0-107.4944-48.18944-107.4944-107.4944v-21.44256c0-118.49728-96.34816-214.89664-214.89664-214.89664a21.46816 21.46816 0 0 1-21.44256-21.4528 21.41696 21.41696 0 0 1 21.44256-21.51936c142.2592 0 257.87392 115.61472 257.87392 257.8688v21.44256c0 35.59424 28.92288 64.52224 64.51712 64.52224 35.54304 0 64.41984-28.92288 64.41984-64.52224 0-96.77824-7.46496-174.21312-20.0192-207.17056a21.44256 21.44256 0 0 1 2.31936-19.86048 21.51936 21.51936 0 0 1 17.66912-9.30304l128.96768-0.512c10.97216 0 19.82464-1.00352 27.83744-3.1488 57.24672-15.36512 91.33056-74.36288 76.01664-131.56864-17.1264-63.9744-104.93952-285.7472-113.2032-306.56-11.62752-19.13856-32.1536-30.56128-55.1168-30.61248-1.29536 0-2.59072-0.1536-3.80416-0.36352H404.51072c-11.86304 0-21.44256-9.65632-21.44256-21.53472a21.4528 21.4528 0 0 1 21.44256-21.44256h322.39104c1.46944 0 2.88768 0.15872 4.28032 0.4096 36.7616 1.50528 70.47168 21.65248 88.77568 53.30944 0.49664 0.91136 0.91648 1.83808 1.34656 2.80064 3.94752 9.91744 96.77824 243.67104 115.37408 312.832zM275.56864 82.21696c11.8784 0 21.53984 9.64608 21.53984 21.44256v472.84736c0 11.8016-9.66144 21.4528-21.53984 21.4528H168.17152c-47.42144 0-85.95456-38.5792-85.95456-85.95456V168.17664c0-47.44192 38.53312-85.95968 85.95456-85.95968h107.39712z m-21.43744 472.77056V125.19424H168.17152c-23.71072 0-42.97728 19.2512-42.97728 42.9824v343.82848c0 23.71072 19.26656 42.9824 42.97728 42.9824h85.95968z" fill="currentColor" p-id="22123" />
            </svg>
          </n-icon>
        </template>
      </n-button>
      <n-button ghost size="tiny" icon-placement="left" type="default" :bordered="false" class="assistant-reply-toolbar__btn" @click="handlePassClip()">
        <template #icon>
          <n-icon size="20" class="assistant-reply-toolbar__icon">
            <svg t="1734515176870" class="icon" viewBox="0 0 1024 1024" version="1.1" xmlns="http://www.w3.org/2000/svg" p-id="26346" width="200" height="200">
              <path d="M955.85804 265.068028l0 595.439364L195.018625 860.507392 195.018625 728.187761 62.698994 728.187761 62.698994 132.748397l760.839415 0 0 132.319631L955.85804 265.068028zM195.018625 695.108365 195.018625 265.068028l595.439364 0 0-99.240235L95.779414 165.827793l0 529.279548L195.018625 695.107341zM922.778644 298.148447 228.099045 298.148447 228.099045 827.427996l694.679599 0L922.778644 298.148447z" fill="currentColor" p-id="26347" />
            </svg>
          </n-icon>
        </template>
      </n-button>
      <n-button ghost :bordered="false" icon-placement="left" type="default" size="tiny" @click="emit('recycleQa')">
        <template #icon>
          <n-icon size="22" class="assistant-reply-toolbar__icon">
            <svg t="1734598608672" class="icon" viewBox="0 0 1024 1024" version="1.1" xmlns="http://www.w3.org/2000/svg" p-id="60134" width="256" height="256">
              <path d="M934.625972 772.390495l-66.949808-130.025379C814.153156 797.841144 670.155555 903.623375 505.826905 903.623375c-174.766372 0-327.506079-120.400161-371.418194-292.809859l27.833929-7.085372c40.686654 159.656233 181.963285 271.148513 343.584266 271.148513 153.86125 0 288.48946-100.409874 336.555176-247.354598l-137.110751 51.179636-10.044774-26.907836 175.818331-65.659419 89.115644 173.096337L934.625972 772.390495zM89.766978 234.477312l-25.927509 12.339026 81.259722 170.634262 176.954201-48.850591-7.631818-27.694759-139.03252 38.356586c53.03182-138.688689 182.650947-230.154867 330.437851-230.154867 156.344814 0 292.572452 102.429881 339.010087 254.889201l27.497261-8.361435c-50.155307-164.636664-197.436698-275.259134-366.507348-275.259134-157.678182 0-296.178583 96.150874-354.877473 242.543012L89.766978 234.477312z" fill="currentColor" p-id="60135" />
            </svg>
          </n-icon>
        </template>
      </n-button>
    </div>
  </div>
</template>

<style scoped>
.assistant-reply-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
  margin-top: 0;
  padding: 18px 15px;
  border-top: 1px solid var(--noesis-color-border-subtle);
  border-bottom-right-radius: 15px;
  border-bottom-left-radius: 15px;
  background-color: transparent;
  color: var(--noesis-color-text-secondary);
}

.assistant-reply-toolbar__left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.assistant-reply-toolbar__actions {
  display: flex;
}

.assistant-reply-toolbar__btn {
  margin-right: 15px;
}

.assistant-reply-toolbar__icon {
  color: var(--noesis-color-text-secondary);
}
</style>
