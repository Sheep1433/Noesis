<script lang="tsx" setup>
import ThemeSwitcher from '@/components/ThemeSwitcher/index.vue'

const router = useRouter()
const route = useRoute()

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
        'p-10 rounded-50%',
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
        flex="~ col gap-10 items-center"
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
            'size-40 rounded-50%',
            '[&_.sidebar-nav-icon]:size-full',
            '[&_.brand-mark]:size-full',
            this.computedInnerClassName,
          ]}
        >
          {this.$slots.default?.()}
        </div>
        <div class="font-bold">{this.label}</div>
      </div>
    )
  },
})

const sidebarItems = ref([
  {
    label: '智枢',
    key: 'SystemLogo',
    renderIcon() {
      return (
        <div class="brand-mark i-my-svg:system-logo"></div>
      )
    },
    onClick() {
      router.push('/')
    },
    props: {
      fill: true,
    },
  },
  {
    label: '对话',
    key: 'ChatIndex',
    onClick() {
      router.push({
        name: this.key,
      })
    },
    renderIcon() {
      return (
        <div class="sidebar-nav-icon i-my-svg:chat-index"></div>
      )
    },
  },
  {
    label: '知识库',
    key: 'KnowledgeBase',
    onClick() {
      router.push({
        name: this.key,
      })
    },
    renderIcon() {
      return (
        <div class="sidebar-nav-icon i-my-svg:chat-knowledge"></div>
      )
    },
  },
  {
    label: '扩展',
    key: 'Extensions',
    onClick() {
      router.push({
        name: this.key,
      })
    },
    renderIcon() {
      return (
        <div class="sidebar-nav-icon i-mdi:puzzle-outline"></div>
      )
    },
  },
  {
    label: '测试用例',
    key: 'TestCaseGenerate',
    onClick() {
      router.push({
        name: this.key,
      })
    },
    renderIcon() {
      return (
        <div class="sidebar-nav-icon i-mdi:clipboard-text-outline"></div>
      )
    },
  },
])


</script>

<template>
  <section
    flex="~ col justify-between"
    w-70
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
      flex="1 ~ col gap-28"
      pt-24
    >
      <SideBarItem
        v-for="(sidebarItem) in sidebarItems"
        :key="sidebarItem.key"
        :label="sidebarItem.label"
        :active="sidebarItem.key === route.name"
        v-bind="sidebarItem.props"
        @click="sidebarItem.onClick.call(sidebarItem)"
      >
        <component :is="sidebarItem.renderIcon" />
      </SideBarItem>
    </div>

    <div flex="~ col items-center" pb-16>
      <ThemeSwitcher />
      <n-popover
        trigger="hover"
        placement="right-start"
      >
        <template #trigger>
          <SideBarItem
            fill
          >
            <div class="sidebar-nav-icon size-35 i-my-svg:avatar"></div>
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

.sidebar-user-menu {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 120px;
}
</style>
