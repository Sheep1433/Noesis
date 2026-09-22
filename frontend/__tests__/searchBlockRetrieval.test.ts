import type { MessageContent } from '@/api/chat'
// @vitest-environment happy-dom
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import ConversationPartsRenderer from '@/components/ConversationPartsRenderer/index.vue'

// 回归：web_search 卡片展开内容为空。
// 根因：web 检索结果无 score 字段（归一化后为 null），SearchBlock 的
// `item.score !== undefined` 判空挡不住 null，`null.toFixed(2)` 抛
// TypeError 使整个卡片渲染树崩溃——展开后只见空白。
// 夹具取自真实 run 的落库 parts（tool output 摘要 + 同 tool_call_id 的
// retrieval part 3 条 web 结果）。
vi.hoisted(() => {
  const values = new Map<string, string>()
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: {
      clear: () => values.clear(),
      getItem: (key: string) => values.get(key) ?? null,
      key: (index: number) => [...values.keys()][index] ?? null,
      get length() {
        return values.size
      },
      removeItem: (key: string) => values.delete(key),
      setItem: (key: string, value: string) => values.set(key, String(value)),
    },
  })
})

vi.mock('@/components/MarkdownPreview/index.vue', () => ({
  default: {
    props: ['content'],
    template: '<div class="markdown-preview-stub">{{ content }}</div>',
  },
}))

const fixture = JSON.parse(
  readFileSync(path.resolve(__dirname, 'fixtures/web-search-retrieval-parts.json'), 'utf-8'),
) as MessageContent

const WEB_RESULT_TITLE = '探寻人工智能 2026重磅启幕，顶尖专家共探AI产业真实趋势 _TOM科技'

async function mountAndExpandWebSearchCard(props: Record<string, unknown>) {
  const wrapper = mount(ConversationPartsRenderer, { props })
  await wrapper.vm.$nextTick()
  const rows = wrapper.findAll('.disclosure-row')
  const webRow = rows.find((r: { text: () => string }) => r.text().includes('网页搜索'))
  expect(webRow, '夹具中应存在 网页搜索 工具卡').toBeTruthy()
  await webRow!.trigger('click')
  await wrapper.vm.$nextTick()
  return wrapper
}

describe('searchBlock 渲染 retrieval part 检索结果', () => {
  it('compact 模式展开渲染全部结果条目', async () => {
    const wrapper = await mountAndExpandWebSearchCard({ content: fixture, compactTools: true })
    expect(wrapper.find('.search-block').exists()).toBe(true)
    expect(wrapper.find('.search-block').text()).toContain('来源（3 条）')
    expect(wrapper.find('.search-block').text()).toContain(WEB_RESULT_TITLE)
    expect(wrapper.findAll('.result-item').length).toBe(3)
  })

  it('detail 模式展开渲染全部结果条目（与 compact 同构）', async () => {
    const wrapper = await mountAndExpandWebSearchCard({ content: fixture, compactTools: false })
    expect(wrapper.find('.search-block').exists()).toBe(true)
    expect(wrapper.find('.search-block').text()).toContain(WEB_RESULT_TITLE)
    expect(wrapper.findAll('.result-item').length).toBe(3)
  })

  it('结果条目超过 8 条时行内全部展示，不出现截断提示', async () => {
    const many = Array.from({ length: 12 }, (_, i) => ({
      evidence_id: `w${i}`,
      source_type: 'web',
      url: `https://example.com/r${i}`,
      title: `结果 ${i}`,
      excerpt: `摘要 ${i}`,
    }))
    const content = JSON.stringify({
      version: 1,
      parts: [{
        type: 'tool',
        tool_call_id: 'call-web',
        name: 'web_search',
        input: { query: 'q' },
        output: '检索到 12 条来源',
        status: 'success',
        state: 'succeeded',
      }, {
        type: 'retrieval',
        tool_call_id: 'call-web',
        query: 'q',
        results: many,
      }],
    })
    const wrapper = await mountAndExpandWebSearchCard({ content, compactTools: true })
    expect(wrapper.findAll('.result-item').length).toBe(12)
    expect(wrapper.find('.capped-hint').exists()).toBe(false)
  })

  it('无 score 的 web 结果不渲染分数（null 不触发分数位）', async () => {
    const wrapper = await mountAndExpandWebSearchCard({ content: fixture, compactTools: true })
    expect(wrapper.find('.search-block').find('.result-item__score').exists()).toBe(false)
  })

  it('长 excerpt 折叠为预览，可展开/收起；短 excerpt 不出现展开按钮', async () => {
    const long = '长'.repeat(400)
    const content = JSON.stringify({
      version: 1,
      parts: [{
        type: 'tool',
        tool_call_id: 'call-web',
        name: 'web_search',
        input: { query: 'q' },
        output: '检索到 2 条来源',
        status: 'success',
        state: 'succeeded',
      }, {
        type: 'retrieval',
        tool_call_id: 'call-web',
        query: 'q',
        results: [
          { evidence_id: 'w0', source_type: 'web', url: 'https://example.com/a', title: '长条目', excerpt: long },
          { evidence_id: 'w1', source_type: 'web', url: 'https://example.com/b', title: '短条目', excerpt: '很短的摘要' },
        ],
      }],
    })
    const wrapper = await mountAndExpandWebSearchCard({ content, compactTools: true })
    const block = wrapper.find('.search-block')
    expect(block.find('.result-item__excerpt.is-clamped').exists()).toBe(true)
    expect(block.findAll('.result-item__toggle').length).toBe(1)

    await block.find('.result-item__toggle').trigger('click')
    await wrapper.vm.$nextTick()
    expect(block.find('.result-item__excerpt.is-clamped').exists()).toBe(false)
    expect(block.find('.result-item__toggle').text()).toBe('收起')

    await block.find('.result-item__toggle').trigger('click')
    await wrapper.vm.$nextTick()
    expect(block.find('.result-item__excerpt.is-clamped').exists()).toBe(true)
    expect(block.find('.result-item__toggle').text()).toBe('展开全文')
  })
})
