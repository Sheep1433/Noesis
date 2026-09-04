<script lang="tsx" setup>
import ThemeSwitcher from '@/components/ThemeSwitcher/index.vue'
import { isChatRouteName, mainNavItems } from '@/config/navigation'
import { chatHistorySiderCollapsed, toggleChatHistorySider } from '@/hooks/useChatHistorySider'

const router = useRouter()
const route = useRoute()

/** 聊天页才有历史侧栏：智枢 logo 在该页悬停变形为侧栏开关，其他页保持回首页 */
const isChatRoute = computed(() => isChatRouteName(route.name))
/** 除智枢外的导航项（智枢单独渲染：聊天页与开关合体） */
const railNavItems = mainNavItems.slice(1)

// 侧边栏图标组件
const SideBarItem = defineComponent({
  props: {
    label: {
      type: String,
      default: '',
    },
    fill: {
      type: Boolean,
      default: false,
    },
    active: {
      type: Boolean,
      default: false,
    },
    disabled: {
      type: Boolean,
      default: false,
    },
  },
  emits: ['click'],
  setup(props, { emit }) {
    const computedWrapperClassName = computed(() => {
      if (props.fill) {
        return 'c-[var(--noesis-color-text)]'
      }

      if (props.disabled) {
        return [
          'opacity-50',
        ]
      }

      return [
        'c-[var(--noesis-color-text-secondary)] hover:c-[var(--noesis-color-text)]',
        props.active && 'c-[var(--noesis-color-text)]',
      ]
    })

    const computedInnerClassName = computed(() => {
      if (props.fill) {
        return
      }

      return [
        'p-5 rounded-50%',
        props.active && 'bg-[var(--noesis-color-primary-bg-hover)]',
      ]
    })

    const handleClick = () => {
      if (props.disabled) {
        return
      }
      emit('click')
    }

    return {
      computedWrapperClassName,
      computedInnerClassName,
      handleClick,
    }
  },
  render() {
    return (
      <div
        flex="~ col gap-6 items-center"
        class={[
          'select-none transition-all-260',
          this.disabled
            ? 'cursor-not-allowed'
            : 'cursor-pointer',
          this.computedWrapperClassName,
        ]}
        onClick={this.handleClick}
      >
        <div
          flex="~ justify-center items-center"
          class={[
            'transition-all-260',
            'size-30 rounded-50%',
            '[&_.sidebar-nav-icon]:size-full',
            '[&_.brand-mark]:size-full',
            this.computedInnerClassName,
          ]}
        >
          {this.$slots.default?.()}
        </div>
        <div class="sidebar-item__label">{this.label}</div>
      </div>
    )
  },
})

function navigate(item: (typeof mainNavItems)[number]) {
  if (!item.routeName) {
    void router.push('/')
    return
  }
  void router.push({ name: item.routeName })
}
</script>

<template>
  <section
    flex="~ col justify-between"
    w-56
    h-full
    overflow-x-hidden
    overflow-y-auto
    relative
    :style="{
      background: 'var(--noesis-sidebar-bg)',
    }"
  >
    <!-- 最侧边图标设置 -->
    <div
      flex="1 ~ col gap-16"
      pt-14
    >
      <!-- 智枢：聊天页且侧栏收起时 = 展开开关（logo 悬停变形）；其余保持回首页 -->
      <SideBarItem
        v-if="!isChatRoute || !chatHistorySiderCollapsed"
        label="智枢"
        fill
        @click="navigate(mainNavItems[0])"
      >
        <div class="brand-mark i-my-svg:system-logo"></div>
      </SideBarItem>

      <!-- 智枢（聊天页、侧栏收起，ChatGPT 式）：默认 logo，悬停变形为展开开关；展开态的收起入口在历史侧栏头部 -->
      <div
        v-else
        class="sidebar-sider-toggle"
        :title="chatHistorySiderCollapsed ? '展开对话历史' : '收起对话历史'"
        :aria-label="chatHistorySiderCollapsed ? '展开对话历史' : '收起对话历史'"
        @click="toggleChatHistorySider()"
      >
        <div class="sidebar-sider-toggle__icons">
          <div class="brand-mark sidebar-sider-toggle__logo i-my-svg:system-logo"></div>
          <div
            class="sidebar-nav-icon sidebar-sider-toggle__panel"
            :class="chatHistorySiderCollapsed ? 'i-hugeicons:panel-left-open' : 'i-hugeicons:panel-left-close'"
          ></div>
        </div>
        <div class="sidebar-item__label">智枢</div>
      </div>

      <SideBarItem
        v-for="sidebarItem in railNavItems"
        :key="sidebarItem.key"
        :label="sidebarItem.label"
        :active="sidebarItem.routeName === route.name"
        :fill="sidebarItem.fill"
        @click="navigate(sidebarItem)"
      >
        <div
          :class="[
            sidebarItem.fill ? 'brand-mark' : 'sidebar-nav-icon',
            sidebarItem.iconClass,
          ]"
        ></div>
      </SideBarItem>
    </div>

    <div flex="~ col items-center" pb-14>
      <ThemeSwitcher compact />
      <n-popover
        trigger="hover"
        placement="right-start"
      >
        <template #trigger>
          <SideBarItem
            fill
          >
            <div class="sidebar-nav-icon size-28 i-my-svg:avatar"></div>
          </SideBarItem>
        </template>
        <div class="sidebar-user-menu">
          <n-button
            quaternary
            strong
            block
            @click="router.push({ name: 'Settings' })"
          >
            设置
          </n-button>
        </div>
      </n-popover>
    </div>
  </section>
</template>

<style lang="scss" scoped>
.brand-mark {
  width: 100%;
  height: 100%;
  border-radius: 50%;
  overflow: hidden;
  background-repeat: no-repeat;
  background-position: center;
  background-size: contain;
}

.sidebar-nav-icon {
  display: inline-block;
  flex-shrink: 0;
}

/* 智枢 logo（聊天页）：logo 与侧栏开关图标叠放，悬停交叉淡切 */
.sidebar-sider-toggle {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  color: var(--noesis-color-text);
  cursor: pointer;
  user-select: none;
}

.sidebar-sider-toggle__icons {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 50%;
  transition: background-color 0.18s ease;
}

.sidebar-sider-toggle:hover .sidebar-sider-toggle__icons {
  background: var(--noesis-color-primary-bg-subtle, rgb(0 0 0 / 5%));
}

.sidebar-sider-toggle__logo,
.sidebar-sider-toggle__panel {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  transition:
    opacity 0.18s ease,
    transform 0.18s ease;
}

.sidebar-sider-toggle__panel {
  width: 18px;
  height: 18px;
  font-size: 18px;
  color: var(--noesis-color-text-secondary);
  opacity: 0;
  transform: scale(0.72);
}

.sidebar-sider-toggle:hover .sidebar-sider-toggle__logo {
  opacity: 0;
  transform: scale(0.72);
}

.sidebar-sider-toggle:hover .sidebar-sider-toggle__panel {
  opacity: 1;
  transform: scale(1);
}

.sidebar-user-menu {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 120px;
}
</style>

<style lang="scss">
/* SideBarItem 是 TSX 子组件：其渲染节点不带本组件的 scoped 属性，
   标签字号须走全局样式，否则智枢（模板渲染）与其他项（TSX 渲染）字号不一致 */
.sidebar-item__label {
  font-size: 11px;
  font-weight: 400;
  line-height: 1.3;
  text-align: center;
}
</style>
