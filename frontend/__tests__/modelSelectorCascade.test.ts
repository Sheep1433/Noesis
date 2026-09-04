// @vitest-environment happy-dom

import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ModelSelector from '@/components/Chat/ModelSelector.vue'

vi.mock('@/api/models', () => ({
  getChatModels: vi.fn().mockResolvedValue({
    models: [
      { id: 'v1', label: 'glm-5.3', provider: '火山智谱', model_type: 'chat', is_default: true, supports_vision: true },
      { id: 'v2', label: 'glm-5.3-flash', provider: '火山智谱', model_type: 'chat', is_default: false },
      { id: 'o1', label: 'kimi-k3', provider: 'opencode', model_type: 'chat', is_default: false },
    ],
    default_id: 'v1',
  }),
}))

vi.mock('@/api/chat', () => ({
  ensureSession: vi.fn().mockResolvedValue({}),
}))

const pushSpy = vi.fn()
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: pushSpy }),
}))

function mountSelector(modelValue = '') {
  return mount(ModelSelector, {
    'props': { sessionId: 'sess-1', persistSessionExtra: true, modelValue },
    'onUpdate:modelValue': (value: string) => wrapper.setProps({ modelValue: value }),
  })
}

let wrapper: ReturnType<typeof mountSelector>

function findOption(text: string): HTMLElement | null {
  for (const el of document.body.querySelectorAll<HTMLElement>('.n-dropdown-option')) {
    // 子菜单渲染在父 option 容器内，父容器的 textContent 会含所有叶子文案，须排除
    if (el.textContent?.includes(text) && !el.querySelector('.n-dropdown-option')) {
      return el
    }
  }
  return null
}

afterEach(() => {
  wrapper?.unmount()
  document.body.innerHTML = ''
})

describe('model selector cascade', () => {
  it('trigger shows provider/model and dropdown lists providers with manage entry', async () => {
    wrapper = mountSelector()
    await flushPromises()
    expect(wrapper.find('button').text()).toContain('火山智谱/glm-5.3')

    await wrapper.find('button').trigger('click')
    await flushPromises()

    // 一级列：提供商 + 管理模型
    expect(findOption('opencode')).not.toBeNull()
    expect(findOption('管理模型')).not.toBeNull()
    // 提供商节点渲染为子菜单（带 chevron 后缀）
    const providerOption = findOption('火山智谱')
    expect(providerOption?.querySelector('.n-dropdown-option-body__suffix')).not.toBeNull()
  })

  it('submenu renders models with vision tag and active checks', async () => {
    wrapper = mountSelector()
    await flushPromises()
    await wrapper.find('button').trigger('click')
    await flushPromises()

    const providerOption = findOption('火山智谱')
    // mouseenter 监听绑在 option-body 上，且事件不冒泡，须直接派发
    providerOption?.querySelector('.n-dropdown-option-body')?.dispatchEvent(new Event('mouseenter'))
    // naive-ui 子菜单延迟 300ms 展开
    await new Promise((resolve) => setTimeout(resolve, 400))
    await flushPromises()

    expect(findOption('glm-5.3-flash')).not.toBeNull()
    // 视觉标签只挂在 supports_vision 的模型上
    const visionOption = findOption('glm-5.3')
    expect(visionOption?.textContent).toContain('视觉')
    const flashOption = findOption('glm-5.3-flash')
    expect(flashOption?.textContent).not.toContain('视觉')
    // 当前提供商与当前模型都有勾选
    expect(document.body.querySelectorAll('.composer-model-dropdown__check').length).toBe(2)
  })

  it('selecting manage entry routes to settings models section', async () => {
    wrapper = mountSelector()
    await flushPromises()
    await wrapper.find('button').trigger('click')
    await flushPromises()

    const manageOption = findOption('管理模型')?.querySelector<HTMLElement>('.n-dropdown-option-body')
    manageOption?.click()
    await flushPromises()

    expect(pushSpy).toHaveBeenCalledWith({ name: 'Settings', query: { s: 'models' } })
  })
})
