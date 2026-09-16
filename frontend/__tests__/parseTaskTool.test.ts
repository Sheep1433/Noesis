import { describe, expect, it } from 'vitest'
import { parseStartTaskChildSessionId } from '@/utils/parseTaskTool'

describe('parseStartTaskChildSessionId', () => {
  it('解析后台直启回执（含 Command repr 包裹的历史落库形态）', () => {
    const output = [
      'Command(update={\'messages\': [ToolMessage(content=\'子 Agent 已启动：b20326f8-7a39-402b-9c1e-000000000001',
      '无需等待——可继续其他工作，之后用 check_async_task 收结果。\', tool_call_id=\'call_1\')]})',
    ].join('\n')
    expect(parseStartTaskChildSessionId(output)).toBe('b20326f8-7a39-402b-9c1e-000000000001')
  })

  it('解析前台等待超时自动转后台回执', () => {
    const output = [
      '任务运行超过 120s，已自动转为后台：c4d2a48e-295d-42a0-8057-dfa37717dd75',
      '可继续其他工作，之后用 check_async_task 收结果。',
    ].join('\n')
    expect(parseStartTaskChildSessionId(output)).toBe('c4d2a48e-295d-42a0-8057-dfa37717dd75')
  })

  it('解析干净文本形态（Command 解包后的新落库）', () => {
    expect(parseStartTaskChildSessionId('子 Agent 已启动：afd44bfb-90b1-4f14-9987-e52f761081fe\n后续说明')).toBe(
      'afd44bfb-90b1-4f14-9987-e52f761081fe',
    )
  })

  it('解析前台等待内完成回执（任务完成（<uuid>）形态）', () => {
    const output = '任务完成（c4feb144-a516-4424-9959-b6afacfc354d）：\n# 调研笔记\n正文……'
    expect(parseStartTaskChildSessionId(output)).toBe('c4feb144-a516-4424-9959-b6afacfc354d')
  })

  it('解析前台等待内失败/超时回执', () => {
    expect(parseStartTaskChildSessionId('任务failed（b20326f8-7a39-402b-9c1e-000000000002）：执行出错')).toBe(
      'b20326f8-7a39-402b-9c1e-000000000002',
    )
    expect(parseStartTaskChildSessionId('任务timed_out（b20326f8-7a39-402b-9c1e-000000000003）：超时')).toBe(
      'b20326f8-7a39-402b-9c1e-000000000003',
    )
  })

  it('解析前台等待期间任务被取消回执（两种文案）', () => {
    expect(parseStartTaskChildSessionId('子 Agent 任务已终止（超时或取消）：c4d2a48e-295d-42a0-8057-dfa37717dd75\n部分产出仍在回收中')).toBe(
      'c4d2a48e-295d-42a0-8057-dfa37717dd75',
    )
    expect(parseStartTaskChildSessionId('[afd44bfb-90b1-4f14-9987-e52f761081fe] cancelled（user）\n部分产出')).toBe(
      'afd44bfb-90b1-4f14-9987-e52f761081fe',
    )
  })

  it('无匹配回执文案或非字符串输入返回 undefined', () => {
    expect(parseStartTaskChildSessionId('任务完成：结果文本')).toBeUndefined()
    expect(parseStartTaskChildSessionId(undefined)).toBeUndefined()
  })
})
