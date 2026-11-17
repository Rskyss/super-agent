import { expect, it } from 'vitest'
import { isCardRunningNow } from '../src/work-duration.ts'

it.each([
  ['待对齐', 'plan_waiting', 'muted', false, false],
  ['待对齐', 'plan_failed', 'error', false, false],
  ['待对齐', 'planning', 'active', false, true],
  ['开发中', 'running', 'active', false, true],
  ['待验收', 'awaiting_acceptance', 'warning', true, false],
  ['已合并', 'done', 'done', false, false],
] as const)('真实运行判定：%s/%s 不把等待或失败当施工', (state, phase, tone, needsYou, expected) => {
  expect(isCardRunningNow(state, phase, tone, needsYou)).toBe(expected)
})
