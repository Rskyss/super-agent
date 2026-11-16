// @vitest-environment jsdom
/// <reference types="vite/client" />
import { expect, it } from 'vitest'
import { parseProgressView } from '../src/client/progress-view.ts'

const host = import.meta.glob('../src/task-tools.ts', { eager: true }) as Record<string, { describeView: (raw: string, plan: string) => string }>
const describeView = (raw: string, plan: string) => host['../src/task-tools.ts']!.describeView(raw, plan)

const shared = import.meta.glob('../src/progress-model.ts', { eager: true }) as Record<string, Record<string, any>>
const fixture = (code: string, detail: string, state: string) => JSON.stringify({
  card: { id: 'CARD-013', title: '等待调度', state: '待对齐', phase: { code, label: code === 'plan_failed' ? '方案没写成' : '等待写方案', detail } },
  stations: [
    { key: 'disp', role: '调度', who: 'Codex', state, detail },
    { key: 'test', who: 'Codex', state: 'idle', detail: '等开工' },
    { key: 'dev', who: 'agy', state: 'idle', detail: '等测试写完' },
    { key: 'check', who: '程序', state: 'idle', detail: '等代码写好' },
    { key: 'rev', who: 'Codex', state: 'idle', detail: '等测试通过' },
    { key: 'accept', who: '你', state: 'idle', detail: '等交付' },
  ],
})

it('Host可直接使用不依赖界面的纯进度模型，和右栏解释相同', () => {
  const model = shared['../src/progress-model.ts']
  expect(model?.parseProgressView, '缺少Host与界面共用的进度解释').toBeTypeOf('function')
  const raw = fixture('plan_waiting', '等待接入调度员后开始写方案', 'wait')
  expect(model!.parseProgressView(raw, 'CARD-013')).toEqual(parseProgressView(raw, 'CARD-013'))
})

it.each([
  ['plan_waiting', '等待接入调度员后开始写方案', 'wait'],
  ['plan_waiting', '排队等待写方案', 'wait'],
  ['plan_failed', '方案没写成：订阅连接超时', 'fail'],
] as const)('card_status与右栏七步逐条一致：%s / %s', (code, detail, state) => {
  const raw = fixture(code, detail, state)
  const model = parseProgressView(raw, 'CARD-013')
  const lines = describeView(raw, '{}').split('\n').filter(line => line.startsWith('- '))
  expect(lines).toHaveLength(7)
  model.steps.forEach((step, index) => {
    expect(lines[index]).toContain(`- ${step.label}（${step.actor}）：${step.state}`)
    if (step.summary) expect(lines[index]).toContain(step.summary)
  })
  expect(lines[0]).not.toContain('：active')
  expect(lines[0]).toContain(detail)
})
