import { describe, expect, it } from 'vitest'
import { parseQuestions } from '../src/chat-tools.ts'
import { cardOfSession, describeView } from '../src/task-tools.ts'

describe('确认卡的问号解析', () => {
  it('一行一条，问题｜建议答案，全角半角竖线都认', () => {
    expect(parseQuestions('真删还是备份？｜直接删\n要不要确认? | 不用确认\n\n')).toEqual([
      { question: '真删还是备份？', suggestion: '直接删' },
      { question: '要不要确认?', suggestion: '不用确认' },
    ])
  })
  it('建议答案开头多写的“建议：”去掉', () => {
    expect(parseQuestions('格式？｜建议：一行写完')).toEqual([{ question: '格式？', suggestion: '一行写完' }])
  })
  it('最多 5 条；没写建议的保留问题、建议为空；空问题丢掉', () => {
    const text = ['a｜1', 'b', '｜只有建议', 'c｜3', 'd｜4', 'e｜5', 'f｜6'].join('\n')
    const got = parseQuestions(text)
    expect(got.map(q => q.question)).toEqual(['a', 'b', 'c', 'd'])
    expect(got[1]).toEqual({ question: 'b', suggestion: '' })
  })
})

describe('任务卡对话认卡', () => {
  it('会话名 task-<卡号>（含项目指纹的新写法）认出卡号，别的会话不认', () => {
    expect(cardOfSession('task-CARD-001')).toBe('CARD-001')
    expect(cardOfSession('task-0123abcd-CARD-001')).toBe('CARD-001')
    expect(cardOfSession('session-5d92fa74')).toBe('')
    expect(cardOfSession('task-')).toBe('')
    expect(cardOfSession('task-../x')).toBe('')
  })
})

describe('进度整理成大白话', () => {
  const view = JSON.stringify({
    card: { title: '待办清单加删除命令', phase: { label: '等你确认', detail: '' } },
    stations: [
      { key: 'you', role: '提需求', who: '你', state: 'done' },
      { key: 'test', role: '写测试', who: 'Codex', state: 'done', elapsed: 143, detail: '测试已写好' },
      { key: 'review', role: '复查', who: 'Codex', state: 'done', detail: '结论：通过' },
    ],
  })
  it('跳过“你”，每步写谁、状态、用时、汇报；有方案就附上', () => {
    const text = describeView(view, JSON.stringify({ plan: '# 拆解方案\n要做什么：加 delete' }))
    expect(text).toContain('卡片：待办清单加删除命令')
    expect(text).toContain('现在：等你确认')
    expect(text).toContain('- 写测试（Codex）：done，143 秒；测试已写好')
    expect(text).toContain('结论：通过')
    expect(text).not.toContain('提需求')
    expect(text).toContain('方案：')
  })
  it('没有方案时不附方案', () => {
    expect(describeView(view, '{}')).not.toContain('方案：')
  })
})
