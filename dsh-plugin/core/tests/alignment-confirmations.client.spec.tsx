// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ConfirmDock } from '../src/client/ConfirmDock.tsx'
import { TaskStrip } from '../src/client/TaskStrip.tsx'
import { TaskReceiptCardPair } from '../src/client/TaskReceipt.tsx'
const modules=import.meta.glob('../src/client/ChatChrome.tsx',{eager:true}) as Record<string,Record<string,any>>
afterEach(cleanup)
const proposal={id:'p',title:'加问候',do:'增加问候页面',notDo:'不联网',doneWhen:'① 输入名字有问候\n② 空名字也问好\n③ 原测试通过',questions:[{question:'用中文？',suggestion:'中文'}]}
function setup(){const focusInput=vi.fn(),dismiss=vi.fn(async()=>({ok:true as const,value:true}));const props={sessionId:'s',proposal:vi.fn(async()=>({ok:true as const,value:proposal})),confirm:vi.fn(),dismiss,dispatcherStatus:vi.fn(async()=>({ok:true as const,value:'ready' as const})),inputActions:{setDraft:vi.fn(),submit:vi.fn()},useInput:(f:any)=>f({draft:'用户正在输入',attachmentIds:['image'],phase:'plain'}),focusInput};render(<ConfirmDock {...props as any}/>);return props}
it('03c内联回执复用真实等待状态，不重复添加等你处理前缀',()=>{
 const row={id:'CARD-001',title:'加问候',phaseCode:'plan_waiting',phaseLabel:'等你处理 · 接调度员',needsYou:true,flow:'开发软件',createdAt:'2026-11-16T09:18:00',requirement:proposal}
 render(<TaskReceiptCardPair row={row} dispatcherLabel="" expanded={false} onToggleExpand={()=>{}} onOpenTask={()=>{}} variant="inline"/>)
 expect(screen.getByText('等你处理 · 接调度员',{exact:true})).toBeTruthy()
 expect(screen.getByText('加问候',{exact:true})).toBeTruthy()
})
it('03b在确认摘要和任务小条之间说明真实下一步，保留草图的聊天结构',()=>{
 const row={id:'CARD-002',title:'加问候',phaseCode:'planning',phaseLabel:'调度员写方案',needsYou:false,flow:'开发软件',createdAt:'2026-11-16T09:18:00',requirement:proposal}
 render(<TaskReceiptCardPair row={row} dispatcherLabel="Codex" expanded={false} onToggleExpand={()=>{}} onOpenTask={()=>{}} variant="inline"/>)
 const summary=document.querySelector('[aria-label="需求摘要 CARD-002"]')!,strip=document.querySelector('[aria-label="任务卡小条 CARD-002"]')!
 const message=screen.getByText('任务卡建好了。调度员先写一份方案，写好了我在任务卡里拿给你批，你批了才开工。这边我们可以接着聊别的。',{exact:true})
 expect(summary.compareDocumentPosition(message)&Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
 expect(message.compareDocumentPosition(strip)&Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
})
it('03b优先显示这张卡实际调度员，当前全局换成另一个工具也不能改写历史身份',()=>{
 const row={id:'CARD-002',title:'加问候',phaseCode:'plan_ready',phaseLabel:'等你批方案',needsYou:true,flow:'开发软件',dispatcher:'Codex',createdAt:'2026-11-16T09:18:00',requirement:proposal}
 render(<TaskReceiptCardPair row={row} dispatcherLabel="agy" expanded={false} onToggleExpand={()=>{}} onOpenTask={()=>{}} variant="inline"/>)
 expect(screen.getByText('开发软件 · 调度员 Codex · 09:18 建卡',{exact:true})).toBeTruthy()
})
it('03确认卡按图给出类型、分行标准、流程与拍板问题，不露先不做',async()=>{
  setup();await screen.findByRole('group',{name:'确认卡：加问候'})
  expect(screen.getByText('干活 · 开发软件')).toBeTruthy()
  expect(within(screen.getByRole('list',{name:'验收标准'})).getAllByRole('listitem')).toHaveLength(3)
  expect(screen.getByText('？要你拍板的')).toBeTruthy()
  expect(screen.getByText('用哪条流程')).toBeTruthy()
  expect(screen.queryByRole('button',{name:'先不做'})).toBeNull()
  expect(screen.queryByText('问号不答也能直接确认，按建议走。')).toBeNull()
  const change=screen.getByRole('button',{name:'不对，改成…'}),status=screen.getByText('等你确认')
  expect(change.compareDocumentPosition(status)&Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
})
it('我要改只收卡并回输入框，不覆盖已有草稿或附件，不自动发送',async()=>{
  const p=setup();fireEvent.click(await screen.findByRole('button',{name:'我要改'}))
  await act(async()=>{await Promise.resolve();await Promise.resolve()})
  expect(p.dismiss).toHaveBeenCalledWith('s','p')
  expect(p.focusInput).toHaveBeenCalledTimes(1)
  expect(p.inputActions.setDraft).not.toHaveBeenCalled()
  expect(p.inputActions.submit).not.toHaveBeenCalled()
  expect(screen.queryByRole('group',{name:'确认卡：加问候'})).toBeNull()
})
it('19类型菜单保留说明、当前干活与子类，Escape退出不撤卡',async()=>{
  const p=setup();fireEvent.click(await screen.findByRole('button',{name:'不对，改成…'}))
  const menu=screen.getByRole('menu');expect(within(menu).getByText('只是聊聊')).toBeTruthy()
  expect(within(menu).getByText('命令行工具')).toBeTruthy()
  expect(within(menu).getByText('调度员（订阅命令行）')).toBeTruthy()
  expect(within(menu).getByText('选了别的，超级玩家按新类型重写这张卡')).toBeTruthy()
  fireEvent.keyDown(menu,{key:'Escape'});await waitFor(()=>expect(screen.queryByRole('menu')).toBeNull())
  expect(p.dismiss).not.toHaveBeenCalled()
})
it('03b任务条读取真实确认需求与建卡时间，折叠摘要可展开且只留查看任务',async()=>{
  const row={id:'CARD-022',title:'加问候',phaseCode:'plan_ready',phaseLabel:'等你批方案',needsYou:true,flow:'开发软件',createdAt:'2026-11-16T09:18:00',requirement:proposal}
  const openTask=vi.fn(async()=>{})
  render(<TaskStrip {...{sessionId:'s',sessionCards:async()=>({ok:true,value:[row]}),cardOfSession:async()=>({ok:true,value:null}),openTask} as any}/>)
  expect(await screen.findByText('需求 · 22 号 · 已确认')).toBeTruthy()
  expect(screen.getByText('增加问候页面')).toBeTruthy()
  fireEvent.click(screen.getByRole('button',{name:'展开'}))
  expect(screen.getByText('不联网')).toBeTruthy()
  expect(screen.getByText(/09:18.*建卡/)).toBeTruthy()
  expect(screen.queryByRole('button',{name:'收起这条'})).toBeNull()
  fireEvent.click(screen.getByRole('button',{name:'查看任务'}));expect(openTask).toHaveBeenCalledWith('CARD-022')
})
it('消息已完成用时只覆盖turn-process，错误节点与后台统计服务保留',()=>{
  const register=modules['../src/client/ChatChrome.tsx']?.registerSuperagentChatChrome
  expect(register).toBeTypeOf('function')
  const entries:any[]=[]
  register({inject:(_n:string,cb:()=>unknown)=>cb(),register:(def:any,C:any)=>{entries.push({def,C});return()=>{}}})
  const process=entries.find(e=>e.def.name==='conversation.chat.node'&&e.def.key==='turn-process')
  expect(process).toBeTruthy();expect(process.def.priority).toBeLessThan(0)
  expect(entries.some(e=>e.def.key==='turn-error')).toBe(false)
})
