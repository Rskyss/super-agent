// @vitest-environment jsdom
/// <reference types="vite/client" />
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { ComponentType } from 'react'
import { afterEach, expect, it, vi } from 'vitest'

const modules = import.meta.glob('../src/client/{ProgressPanel,ChangeDock}.tsx', { eager: true }) as Record<string, Record<string, ComponentType<any>>>
const progress = () => {
  const Component = modules['../src/client/ProgressPanel.tsx']?.ProgressPanel
  expect(Component, '缺少七步进度组件').toBeTypeOf('function')
  return Component!
}
const changeDock = () => {
  const Component = modules['../src/client/ChangeDock.tsx']?.ChangeDock
  expect(Component, '缺少改要求确认条').toBeTypeOf('function')
  return Component!
}
afterEach(() => { cleanup(); vi.useRealTimers() })
const ok = <T,>(value: T) => ({ ok: true as const, value })

it('右栏用施工时长和总览相同格式，绝不把过夜等待当干活',async()=>{
 const Panel=progress();render(<Panel card="CARD-011" view={async()=>ok(JSON.stringify({card:{id:'CARD-011',state:'已合并',phase:{code:'done'}},elapsed:31843,workElapsed:211,stations:[]}))}/> )
 expect(await screen.findByText('干活用时 3 分',{exact:true})).toBeTruthy()
 expect(document.body.textContent).not.toContain('总用时')
 expect(document.body.textContent).not.toContain('530')
})
const view = (code: string, active = 'test') => JSON.stringify({
  card: { id: 'CARD-011', title: '显示版本', state: code === 'plan_ready' ? '待对齐' : '开发中', phase: { code, label: code === 'plan_ready' ? '等你批准方案' : '进行中', actions: code === 'plan_ready' ? ['approve', 'revise', 'discard'] : ['stop'] } },
  stations: [
    { key: 'you', role: '提需求', who: '你', state: 'done' },
    { key: 'disp', role: '调度', who: 'Codex', model: 'gpt-6.1-sol · 高', state: 'done', elapsed: 22 },
    ...['test', 'dev', 'check', 'rev', 'accept'].map(key => ({ key, role: ({test:'写测试',dev:'写代码',check:'跑测试',rev:'复查',accept:'确认'} as Record<string,string>)[key], who: key === 'dev' ? 'agy' : 'Codex', model: key === 'dev' ? 'Gemini 4 Hydrogen · 高' : '', state: key === active ? 'active' : 'idle', elapsed: key === active ? 12 : null, detail: key === active ? '正在检查边界' : '' })),
  ], elapsed: 34,
})

it('右栏显示固定两步及流程五步，显示真实执行者、用时和当前步骤', async () => {
  const ProgressPanel = progress()
  render(<ProgressPanel card="CARD-011" view={vi.fn(async () => ok(view('running')))} />)
  for (const name of ['调度员写方案','你批方案','写测试','写代码','跑全部测试','复查','你验收']) expect(await screen.findByText(name, {exact:true})).toBeTruthy()
  expect(screen.getAllByRole('listitem')).toHaveLength(7)
  expect(screen.getByText(/Gemini 4 Hydrogen/)).toBeTruthy()
  expect(screen.getByText(/22/)).toBeTruthy()
  expect(screen.getByText('正在检查边界')).toBeTruthy()
})

it('进度每两秒读取真实后台，新状态能取代旧状态，失败照实提示', async () => {
  const ProgressPanel = progress()
  vi.useFakeTimers()
  const load = vi.fn().mockResolvedValueOnce(ok(view('running','test'))).mockResolvedValueOnce(ok(view('running','dev'))).mockResolvedValue({ ok:false, error:{message:'断开'} })
  render(<ProgressPanel card="CARD-011" view={load} />)
  await act(async () => { await Promise.resolve() })
  await act(async () => { vi.advanceTimersByTime(2000); await Promise.resolve() })
  expect(load).toHaveBeenCalledTimes(2)
  const rows = screen.getAllByRole('listitem')
  expect(rows[3]?.getAttribute('aria-current')).toBe('step')
  expect(rows[2]?.getAttribute('aria-current')).not.toBe('step')
  await act(async () => { vi.advanceTimersByTime(2000); await Promise.resolve() })
  expect(screen.getByText(/读不到最新进度/)).toBeTruthy()
})

it.each(['ready','running'])('后台仍在执行（%s）但处于两步交接空隙时，保留一个准备中的步骤', async code => {
  const ProgressPanel = progress()
  const {container}=render(<ProgressPanel card="CARD-011" view={vi.fn(async()=>ok(view(code,'')))} />)
  await screen.findByText('写测试',{exact:true})
  expect(container.querySelectorAll('[data-state="ongoing"]')).toHaveLength(1)
  expect(screen.getAllByRole('listitem')[2]?.getAttribute('aria-current')).toBe('step')
})

it.each(['done','dropped','blocked','interrupted'])('结束或停下（%s）不显示仍在干活的转圈', async code => {
  const ProgressPanel = progress()
  const {container}=render(<ProgressPanel card="CARD-011" view={vi.fn(async()=>ok(view(code)))} />)
  await screen.findByText('写测试',{exact:true})
  expect(container.querySelectorAll('[data-state="ongoing"]')).toHaveLength(0)
})

it.each([['算了',false],['确认改',true]] as const)('确认条由用户点“%s”才提交决定', async (label, confirm) => {
  const ChangeDock = changeDock()
  const pending = { id:'c1',card:'CARD-011',summary:'第 3 条：原地显示 → 按钮旁边显示',text:'第3条改为按钮旁边',createdAt:0 }
  const decideChange = vi.fn(async () => ok({ok:true,message:''}))
  render(<ChangeDock card="CARD-011" pendingChange={vi.fn(async()=>ok(pending))} decideChange={decideChange} />)
  expect(await screen.findByText(/第 3 条：原地显示/)).toBeTruthy()
  expect(decideChange).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button',{name:label}))
  await act(async()=>{await Promise.resolve()})
  expect(decideChange).toHaveBeenCalledWith('CARD-011','c1',confirm)
  expect(screen.queryByRole('button',{name:'确认改'})).toBeNull()
})

it('提交中两个按钮都锁定；提交失败保留确认条，可再次确认', async () => {
  const ChangeDock = changeDock()
  let finish: ((v:unknown)=>void)|undefined
  const decideChange=vi.fn(()=>new Promise(resolve=>{finish=resolve}))
  render(<ChangeDock card="CARD-011" pendingChange={vi.fn(async()=>ok({id:'c1',card:'CARD-011',summary:'第1条：旧 → 新',text:'新要求',createdAt:0}))} decideChange={decideChange} />)
  fireEvent.click(await screen.findByRole('button',{name:'确认改'}))
  expect((screen.getByRole('button',{name:'算了'}) as HTMLButtonElement).disabled).toBe(true)
  expect((screen.getByRole('button',{name:'确认改'}) as HTMLButtonElement).disabled).toBe(true)
  await act(async()=>{finish?.(ok({ok:false,message:'这次没交上'}))})
  expect(screen.getByText(/这次没交上/)).toBeTruthy()
  expect((screen.getByRole('button',{name:'确认改'}) as HTMLButtonElement).disabled).toBe(false)
})

it('未接调度员、尚未写方案时，不把你批方案显示成方案已写好',async()=>{const ProgressPanel=progress();render(<ProgressPanel card="CARD-012" view={vi.fn(async()=>ok(JSON.stringify({card:{id:'CARD-012',title:'等接入',state:'待对齐',phase:{code:'plan_waiting',label:'等待写方案',actions:['replan','discard']}},stations:[],elapsed:0})))} />);await screen.findByText('你批方案',{exact:true});expect(screen.queryAllByText(/方案已写好/)).toHaveLength(0)})
