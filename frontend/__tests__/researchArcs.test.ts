import type { MessageContentV1, RetrievalResultUi, UiPart } from '@/views/chat/messageParts'
import { describe, expect, it } from 'vitest'
import {
  arcDeliveryText,
  arcMessageKey,
  arcWrittenFiles,
  collectArcSources,
  collectCitationSignals,
  computeArcPanels,
  computeResearchArcs,
  isRealUserMessage,
} from '@/views/chat/researchArcs'

function webResult(evidence_id: string, url: string, title = url): RetrievalResultUi {
  return { evidence_id, source_type: 'web', url, title, excerpt: 'excerpt' }
}

function retrievalPart(results: RetrievalResultUi[], origin?: { kind: 'main' | 'subagent', label?: string }): UiPart {
  return {
    id: `retrieval-${Math.random().toString(36).slice(2, 8)}`,
    type: 'retrieval',
    tool_call_id: `call-${Math.random().toString(36).slice(2, 8)}`,
    query: 'q',
    results,
    ...(origin ? { origin } : {}),
  }
}

function textPart(content: string): UiPart {
  return { id: `text-${Math.random().toString(36).slice(2, 8)}`, type: 'text', content }
}

function writeFilePart(content: string, state: 'succeeded' | 'failed' = 'succeeded'): UiPart {
  return {
    id: `tool-${Math.random().toString(36).slice(2, 8)}`,
    type: 'tool',
    tool_call_id: `call-${Math.random().toString(36).slice(2, 8)}`,
    name: 'write_file',
    input: { file_path: '/workspace/report.md', content },
    output: 'ok',
    status: state === 'failed' ? 'error' : 'success',
    state,
  } as UiPart
}

function content(parts: UiPart[]): MessageContentV1 {
  return { version: 1, parts }
}

interface TestMessage {
  uuid: string
  message_id?: string
  role: 'user' | 'assistant'
  source_kind?: string
  messageContent?: MessageContentV1
}

let seq = 0
function msg(role: 'user' | 'assistant', parts: UiPart[] = [], source_kind?: string): TestMessage {
  seq += 1
  return { uuid: `m${seq}`, message_id: `id${seq}`, role, source_kind, messageContent: content(parts) }
}

describe('弧边界计算', () => {
  it('系统通知注入（bg_task_notice）不构成弧边界：续跑多段消息同属一弧', () => {
    const messages = [
      msg('user'),
      msg('assistant', [textPart('派发中…')]),
      msg('user', [], 'bg_task_notice'),
      msg('assistant', [textPart('进度…')]),
      msg('user'), // 新弧边界
      msg('assistant', [textPart('交付')]),
    ]
    const arcs = computeResearchArcs(messages)
    expect(arcs).toHaveLength(2)
    expect(arcs[0].messages).toHaveLength(4)
    expect(arcMessageKey(arcs[0].terminal!)).toBe(messages[3].message_id)
    expect(arcMessageKey(arcs[1].terminal!)).toBe(messages[5].message_id)
  })

  it('真实用户消息是弧边界；被打断的弧以末条 assistant 消息为聚合位', () => {
    const messages = [
      msg('user'),
      msg('assistant', [textPart('只有过程，无交付')]),
    ]
    const arcs = computeResearchArcs(messages)
    expect(arcs).toHaveLength(1)
    expect(arcs[0].terminal).toBe(messages[1])
  })

  it('isRealUserMessage 排除 bg_task_notice', () => {
    expect(isRealUserMessage(msg('user'))).toBe(true)
    expect(isRealUserMessage(msg('user', [], 'bg_task_notice'))).toBe(false)
    expect(isRealUserMessage(msg('assistant'))).toBe(false)
  })
})

describe('弧聚合面板（纯函数）', () => {
  it('过程消息不渲染面板；末条消息渲染弧内全部来源（含落位在过程消息上的 parts）', () => {
    const process = msg('assistant', [retrievalPart([webResult('w1', 'https://example.com/a')])])
    const dispatch = msg('assistant', [retrievalPart([webResult('w2', 'https://example.com/b')])])
    const delivery = msg('assistant', [textPart('交付：见 [citation:A](https://example.com/a)')])
    const messages = [msg('user'), process, dispatch, delivery]
    const panels = computeArcPanels(messages)

    expect(panels.has(arcMessageKey(process))).toBe(false)
    expect(panels.has(arcMessageKey(dispatch))).toBe(false)
    const panel = panels.get(arcMessageKey(delivery))
    expect(panel).toBeDefined()
    expect(panel!.entries).toHaveLength(2)
    // 引用归因：交付正文含 a 的完整引用标记
    expect(panel!.citedKeys.has('web:https://example.com/a')).toBe(true)
    expect(panel!.citedKeys.has('web:https://example.com/b')).toBe(false)
  })

  it('多子 Agent 同源（同 canonical URL）合并为单条目带多 origin；计数为去重数', () => {
    const process = msg('assistant', [
      retrievalPart(
        [webResult('w1', 'https://example.com/a?utm_source=x')],
        { kind: 'subagent', label: '调研 X' },
      ),
    ])
    const delivery = msg('assistant', [
      retrievalPart([webResult('w2', 'https://example.com/a')], { kind: 'subagent', label: '调研 Y' }),
      retrievalPart([webResult('w3', 'https://example.com/b')]), // 无 origin → main
      textPart('报告正文'),
    ])
    const messages = [msg('user'), process, delivery]
    const entries = collectArcSources(messages.slice(1))
    expect(entries).toHaveLength(2)
    const shared = entries.find((e) => e.key === 'web:https://example.com/a')!
    expect(shared.origins).toEqual([
      { kind: 'subagent', label: '调研 X' },
      { kind: 'subagent', label: '调研 Y' },
    ])
    const mainEntry = entries.find((e) => e.key === 'web:https://example.com/b')!
    expect(mainEntry.origins).toEqual([{ kind: 'main' }])
  })

  it('旧数据兼容：retrieval part 无 origin 按 main 归组，解析不报错', () => {
    const entries = collectArcSources([msg('assistant', [retrievalPart([webResult('w1', 'https://example.com/a')])])])
    expect(entries[0].origins).toEqual([{ kind: 'main' }])
  })

  it('多轮隔离：相邻研究弧面板互不渗透（30/40 不合并）', () => {
    const firstSources = Array.from({ length: 30 }, (_, i) => webResult(`f${i}`, `https://example.com/first/${i}`))
    const secondSources = Array.from({ length: 40 }, (_, i) => webResult(`s${i}`, `https://example.com/second/${i}`))
    const shared = webResult('shared', 'https://example.com/shared')

    const messages = [
      msg('user'),
      msg('assistant', [retrievalPart([...firstSources, shared]), textPart('交付一，结论见 [citation:共享来源](https://example.com/shared)')]),
      msg('user'),
      msg('assistant', [retrievalPart([...secondSources, shared]), textPart('交付二，结论见 [citation:共享来源](https://example.com/shared)')]),
    ]
    const panels = computeArcPanels(messages)
    expect(panels.get(arcMessageKey(messages[1]))!.entries).toHaveLength(31)
    expect(panels.get(arcMessageKey(messages[3]))!.entries).toHaveLength(41)
    // 同一 URL 在两个弧的面板中各出现一次（跨弧不合并）
    expect(panels.get(arcMessageKey(messages[1]))!.citedKeys.has('web:https://example.com/shared')).toBe(true)
    expect(panels.get(arcMessageKey(messages[3]))!.citedKeys.has('web:https://example.com/shared')).toBe(true)
  })

  it('刷新后一致：纯函数同输入必同输出', () => {
    const messages = [
      msg('user'),
      msg('assistant', [retrievalPart([webResult('w1', 'https://example.com/a')]), textPart('见 [citation:A](https://example.com/a)')]),
    ]
    const first = computeArcPanels(messages)
    const second = computeArcPanels(JSON.parse(JSON.stringify(messages)))
    expect([...second.keys()]).toEqual([...first.keys()])
    expect(second.get(arcMessageKey(messages[1]))!.entries.map((e) => e.key)).toEqual(
      first.get(arcMessageKey(messages[1]))!.entries.map((e) => e.key),
    )
  })

  it('文件交付降级：交付正文不含来源 URL 时仅「共检索 N」（无引用子集）', () => {
    const messages = [
      msg('user'),
      msg('assistant', [retrievalPart([webResult('w1', 'https://example.com/a')]), textPart('报告已写入 workspace/report.md')]),
    ]
    const panel = computeArcPanels(messages).get(arcMessageKey(messages[1]))!
    expect(panel.entries).toHaveLength(1)
    expect(panel.citedKeys.size).toBe(0)
    expect(panel.attributionUnavailable).toBe(true)
  })

  it('弧内无 retrieval parts 不渲染面板；无 assistant 消息的弧无聚合位', () => {
    const noSources = computeArcPanels([msg('user'), msg('assistant', [textPart('纯文本回答')])])
    expect(noSources.size).toBe(0)
    const noAssistant = computeArcPanels([msg('user'), msg('user', [], 'bg_task_notice')])
    expect(noAssistant.size).toBe(0)
  })

  it('引用归因只看交付消息顶层正文（子 Agent 嵌套正文不参与）', () => {
    const childText: UiPart = {
      id: 'text-child',
      type: 'text',
      content: '子 Agent 叙述 https://example.com/a',
      parent_task_call_id: 'task-1',
    }
    const delivery = msg('assistant', [
      retrievalPart([webResult('w1', 'https://example.com/a')]),
      childText,
      textPart('交付正文，无 URL'),
    ])
    const messages = [msg('user'), delivery]
    const panel = computeArcPanels(messages).get(arcMessageKey(delivery))!
    expect(panel.citedKeys.size).toBe(0)
  })

  it('裸 URL 不升格为引用（tracking 参数与 fragment 不影响身份，但归因只认标记）', () => {
    const delivery = msg('assistant', [
      retrievalPart([
        webResult('w1', 'https://example.com/a'),
        webResult('w2', 'https://example.com/b'),
      ]),
      textPart('参见 https://Example.com/a?utm_source=x#sec 与 http://example.com/b。'),
    ])
    const panel = computeArcPanels([msg('user'), delivery]).get(arcMessageKey(delivery))!
    expect(panel.citedKeys.size).toBe(0)
    expect(panel.attributionUnavailable).toBe(true)
  })

  it('arcDeliveryText 取顶层 text parts', () => {
    const delivery = msg('assistant', [
      textPart('第一段'),
      { id: 'text-child', type: 'text', content: '嵌套', parent_task_call_id: 'task-1' },
      textPart('第二段'),
    ])
    expect(arcDeliveryText(delivery)).toBe('第一段\n第二段')
  })

  it('引用优先编号：被引用条目按引用首现序编 1..N，未引用按首见序接续排后', () => {
    const delivery = msg('assistant', [
      retrievalPart([
        webResult('w1', 'https://example.com/a'),
        webResult('w2', 'https://example.com/b'),
        webResult('w3', 'https://example.com/c'),
      ]),
      // 首见序为 a,b,c；正文先引 c 再引 b——c=1、b=2，未被引用的 a 排 3
      textPart('结论见 [citation:C](https://example.com/c) 与 [citation:B](https://example.com/b)。'),
    ])
    const panel = computeArcPanels([msg('user'), delivery]).get(arcMessageKey(delivery))!
    expect(panel.numbers.get('web:https://example.com/c')).toBe(1)
    expect(panel.numbers.get('web:https://example.com/b')).toBe(2)
    expect(panel.numbers.get('web:https://example.com/a')).toBe(3)
  })
})

describe('引用判定：完整标记精确命中 + 文件内容归因', () => {
  it('完整 web 标记 ref 精确命中（tracking 参数不影响）', () => {
    const delivery = msg('assistant', [
      retrievalPart([webResult('w1', 'https://example.com/a')]),
      textPart('结论见 [citation:来源 A](https://example.com/a?utm_source=x)。'),
    ])
    const panel = computeArcPanels([msg('user'), delivery]).get(arcMessageKey(delivery))!
    expect(panel.citedKeys.has('web:https://example.com/a')).toBe(true)
  })

  it('完整 kb 标记 ref 命中（kb:Collection/文件名 → citationKey）', () => {
    const kbResult: RetrievalResultUi = {
      evidence_id: 'kb-1',
      source_type: 'knowledge_base',
      collection_name: 'requirement_docs',
      title: '登录需求.md',
      excerpt: '验证码五分钟内有效',
    }
    const delivery = msg('assistant', [
      retrievalPart([kbResult]),
      textPart('要求见 [citation:登录需求.md](kb:requirement_docs/登录需求.md)。'),
    ])
    const panel = computeArcPanels([msg('user'), delivery]).get(arcMessageKey(delivery))!
    expect(panel.citedKeys.has('kb:requirement_docs:登录需求.md')).toBe(true)
  })

  it('残缺标记（无 ref 括号）不升格为引用，来源归入其他检索来源', () => {
    const delivery = msg('assistant', [
      retrievalPart([
        webResult('w1', 'https://github.com/crewAIInc/crewAI'),
        webResult('w2', 'https://docs.crewai.com/overview'),
      ]),
      textPart('见 [citation:github.com] 与 [citation:docs.crewai.com]。'),
    ])
    const panel = computeArcPanels([msg('user'), delivery]).get(arcMessageKey(delivery))!
    expect(panel.citedKeys.size).toBe(0)
    // 残缺标记不构成有效信号：面板降级为仅「共检索 N」
    expect(panel.attributionUnavailable).toBe(true)
  })

  it('文件交付：交付说明无 URL，但报告文件内容（write_file input）里的标记参与归因', () => {
    const report = `# 调研报告\n\n结论一 [citation:来源 A](https://example.com/a)。\n\n结论二 [citation:来源 B](https://example.com/b)。`
    const writer = msg('assistant', [
      retrievalPart([webResult('w1', 'https://example.com/a'), webResult('w2', 'https://example.com/b')]),
      writeFilePart(report),
    ])
    const delivery = msg('assistant', [textPart('报告已写入 workspace/report.md。')])
    const messages = [msg('user'), writer, delivery]
    const panel = computeArcPanels(messages).get(arcMessageKey(delivery))!
    expect(panel.entries).toHaveLength(2)
    expect(panel.citedKeys.has('web:https://example.com/a')).toBe(true)
    expect(panel.citedKeys.has('web:https://example.com/b')).toBe(true)
    expect(panel.attributionUnavailable).toBe(false)
  })

  it('文件交付且文件内无任何信号：仍降级为仅「共检索 N」', () => {
    const writer = msg('assistant', [
      retrievalPart([webResult('w1', 'https://example.com/a')]),
      writeFilePart('# 报告\n\n纯文本结论，无标记无链接。'),
    ])
    const delivery = msg('assistant', [textPart('报告已写入 report.md。')])
    const messages = [msg('user'), writer, delivery]
    const panel = computeArcPanels(messages).get(arcMessageKey(delivery))!
    expect(panel.citedKeys.size).toBe(0)
    expect(panel.attributionUnavailable).toBe(true)
  })

  it('失败的 write_file 不参与归因', () => {
    const writer = msg('assistant', [writeFilePart('[citation:来源 A](https://example.com/a)', 'failed')])
    const delivery = msg('assistant', [textPart('写入失败，正文重述。')])
    const written = arcWrittenFiles([writer, delivery])
    expect(written.contents).toHaveLength(0)
    expect(written.paths).toHaveLength(0)
  })

  it('collectCitationSignals：完整标记精确 key，残缺标记与裸 URL 不产生信号', () => {
    const signal = collectCitationSignals(
      'A [citation:标题](https://example.com/a?utm_source=x) B [citation:github.com] C https://example.com/b D [citation:文件](kb:col/file)',
    )
    expect(signal.exactKeys.has('web:https://example.com/a')).toBe(true)
    expect(signal.exactKeys.has('kb:col:file')).toBe(true)
    expect(signal.exactKeys.size).toBe(2)
    expect(signal.hasAnySignal).toBe(true)
    const empty = collectCitationSignals('见 https://example.com/b 与 [citation:github.com]。')
    expect(empty.exactKeys.size).toBe(0)
    expect(empty.hasAnySignal).toBe(false)
  })

  it('writtenFilePaths 提取弧内写入文件路径，失败写入不计', () => {
    const writer = msg('assistant', [
      writeFilePart('# 报告', 'succeeded'),
      writeFilePart('# 草稿', 'failed'),
    ])
    const written = arcWrittenFiles([writer])
    expect(written.paths).toEqual(['/workspace/report.md'])
  })
})
