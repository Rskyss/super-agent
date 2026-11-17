import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, expect, it } from 'vitest'
import Gateway from '../src/index.ts'

const dirs: string[] = []
afterEach(() => dirs.splice(0).forEach(p => rmSync(p, { recursive: true, force: true })))
async function readRunning(phase: string, options: { live?: boolean; deferred?: boolean; otherCard?: boolean } = {}) {
  const project = mkdtempSync(join(tmpdir(), 'sa-align-running-')); dirs.push(project)
  const c = join(project, '.collab/cards/CARD-022'); mkdirSync(c, { recursive: true })
  writeFileSync(join(c, '状态.json'), JSON.stringify({ state: '待对齐' }))
  writeFileSync(join(c, '来源.json'), JSON.stringify({ deferred_plan: Boolean(options.deferred) }))
  if (options.live) {
    const b = join(project, '.collab/后台'); mkdirSync(b, { recursive: true })
    writeFileSync(join(b, 'plan.json'), JSON.stringify({ card: options.otherCard ? 'CARD-023' : 'CARD-022', pid: process.pid }))
  }
  const g = Object.create(Gateway.prototype) as any
  g.config = { project }
  g.get = async () => [{ id: 'CARD-022', title: '核对真跑', state: '待对齐', phase: { code: phase, tone: 'run', needs_you: false, actions: [] } }]
  return (await g.cards())[0].isRunningNow
}

it('排队等写方案且无进程时，向左栏明确返回未运行', async () => {
  expect(await readRunning('plan_waiting')).toBe(false)
})
it('阶段写着planning但没有方案进程时仍不转圈', async () => {
  expect(await readRunning('planning')).toBe(false)
})
it('这张卡的方案进程真实存活时才运行', async () => {
  expect(await readRunning('planning', { live: true })).toBe(true)
})
it('来源.json明确暂缓时，即使留下进程记录也不能伪装运行', async () => {
  expect(await readRunning('planning', { live: true, deferred: true })).toBe(false)
})
it('别的卡的方案进程不算本卡在跑', async () => {
  expect(await readRunning('planning', { live: true, otherCard: true })).toBe(false)
})
