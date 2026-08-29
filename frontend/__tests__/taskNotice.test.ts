import { describe, expect, it } from 'vitest'
import { taskNoticeMeta } from '@/utils/taskNotice'

const singleNotice = [
  '[系统通知] 子 Agent「调研Agent Benchmark全景」已完成 · 1 轮 · 63 步 · 2m 30s（结果预览：我已完成了对主流 Agent Benchmark 的深度调研。…），可打开任务详情查看完整过程。',
  '以上是后台任务终态通知。用 check_task 收取结果，继续完成此前对用户承诺的交付（补充摘要/汇报结论）。',
].join('\n')

const multiNotice = [
  '[系统通知] 子 Agent「调研Agent Benchmark全景」已完成 · 1 轮 · 63 步 · 2m 30s（结果预览：…），可打开任务详情查看完整过程。',
  '[系统通知] 子 Agent「调研Agent评测理论与Taxonomy」已完成 · 1 轮 · 58 步 · 2m 37s（结果预览：…），可打开任务详情查看完整过程。',
  '[系统通知] 子 Agent「调研Judge、Online评测与Observability」已完成 · 1 轮 · 51 步 · 3m 30s（结果预览：…），可打开任务详情查看完整过程。',
  '以上是后台任务终态通知。用 check_task 收取结果，继续完成此前对用户承诺的交付（补充摘要/汇报结论）。',
].join('\n')

describe('taskNoticeMeta 续跑通知解析', () => {
  it('单条完成通知：标题带标签与终态，明细携带指标', () => {
    const meta = taskNoticeMeta(singleNotice)
    expect(meta.title).toBe('子 Agent「调研Agent Benchmark全景」已完成')
    expect(meta.detail).toContain('执行结果已收到')
    expect(meta.detail).toContain('63 步')
    expect(meta.tone).toBe('success')
  })

  it('多条合并通知：不再只显示第一条，全部标签可见', () => {
    const meta = taskNoticeMeta(multiNotice)
    expect(meta.title).toBe('3 个子 Agent 已完成')
    expect(meta.detail).toContain('调研Agent Benchmark全景')
    expect(meta.detail).toContain('调研Agent评测理论与Taxonomy')
    expect(meta.detail).toContain('调研Judge、Online评测与Observability')
    expect(meta.tone).toBe('success')
  })

  it('多条混合终态：标题与色调按最差状态判定', () => {
    const meta = taskNoticeMeta(
      '[系统通知] 子 Agent「任务A」已完成 · 1 轮 · 5 步 · 10s（结果预览：…），可打开任务详情查看完整过程。\n'
      + '[系统通知] 子 Agent「任务B」执行失败 · 1 轮 · 5 步 · 10s（结果预览：…），可打开任务详情查看完整过程。',
    )
    expect(meta.title).toBe('2 个后台任务已结束')
    expect(meta.tone).toBe('error')
  })

  it('单条失败通知：错误色调', () => {
    const meta = taskNoticeMeta('[系统通知] 子 Agent「任务A」执行失败 · 1 轮 · 5 步 · 10s（结果预览：…），可打开任务详情查看完整过程。')
    expect(meta.title).toBe('子 Agent「任务A」执行失败')
    expect(meta.tone).toBe('error')
  })
})
