// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { StrictMode, createElement, type ComponentType } from 'react'
const modules = import.meta.glob('../src/client/HomeChrome.tsx', { eager: true }) as Record<string, Record<string, any>>
afterEach(cleanup)

function harness(options?: Record<string,unknown>) {
  const lists = new Map<string, any[]>()
  const listeners = new Map<string, Set<() => void>>()
  const declared = new Set(['conversation.composer.bar', 'conversation.hero.frame', 'conversation.hero.workspace', 'conversation.hero.agentPreset'])
  let revision = 0
  const attachment = { component: () => <span>原附件轨道</span>, options: {}, registrant: 'dsh-attachment' }
  const overlay = { component: () => <span>原指令菜单</span>, options: { id: 'slash-menu' }, registrant: 'dsh-input-trigger' }
  const Original = (p: any) => <div data-original-editor="preserved"><textarea placeholder={p.placeholder} onChange={p.onChange} />{p.renderSlot('conversation.input.attachments', { attachments: [], canAcceptDrop: p.canAcceptDrop !== false, onAddFiles: p.onFiles })}{p.renderSlot('conversation.input.overlay', {})}{p.renderSlot('conversation.input.model', {})}<span data-menu-source="">{String(p.useMenuLauncher?.((source: unknown)=>source)??'closed')}</span><button aria-haspopup="listbox" aria-expanded={p.useMenuLauncher?.((source: unknown)=>source==='command')??false} onClick={() => p.toggleCommandMenu?.({ start: 0, end: 0 })}>添加文件或调用指令</button><button onClick={p.onSend}>原发送</button></div>
  const original = { component: Original, inject: vi.fn(() => ({ keyboard: 'original-controller' })), locale: 'conversation', options: {}, registrant: 'dsh-conversation', children: Object.fromEntries(['attachments','overlay','permission','left','plan','right','model','activity'].map(x => ['conversation.input.'+x, { kind: x === 'overlay' || x === 'left' || x === 'right' ? 'list' : 'single', scope: x === 'attachments' ? 'session-maybe' : 'session' }])) }
  Object.keys(original.children).forEach(x => declared.add(x))
  declared.add('conversation.composer.dock')
  lists.set('conversation.composer.bar', [original])
  lists.set('conversation.input.attachments', [attachment])
  lists.set('conversation.input.overlay', [overlay])
  const pending = new Map<string, Array<() => unknown>>()
  const notify = (name: string) => { revision++; for (const cb of [...(listeners.get(name) ?? [])]) cb() }
  const slots = {
    entries: (name: string) => lists.get(name) ?? [],
    entriesOfSlot: (name: string) => lists.get(name) ?? [],
    getVersion: () => revision,
    subscribe: (name: string, cb: () => void) => { const set = listeners.get(name) ?? new Set(); set.add(cb); listeners.set(name,set); return () => set.delete(cb) },
    inject: (name: string, cb: () => unknown) => { if(declared.has(name)) return cb(); const a=pending.get(name)??[];a.push(cb);pending.set(name,a);return () => {} },
    register: (options: any, component: ComponentType<any>) => {
      for(const name of Object.keys(options.children ?? {})) { if(declared.has(name)) throw new Error('duplicate child declaration: '+name) }
      const entry = { ...options, component, options, registrant: 'superagent-home' }
      const a=lists.get(options.name)??[];a.push(entry);lists.set(options.name,a)
      for(const name of Object.keys(options.children ?? {})) { declared.add(name); for(const cb of pending.get(name)??[])cb(); pending.delete(name) }
      notify(options.name)
      return () => { lists.set(options.name,(lists.get(options.name)??[]).filter(x=>x!==entry));notify(options.name) }
    },
  }
  const register = modules['../src/client/HomeChrome.tsx']?.registerSuperagentHomeSlots
  expect(register, '缺少复用DSH输入框的首页插槽组合').toBeTypeOf('function')
  register(slots,options)
  const entry = slots.entries('conversation.composer.bar').find(e => e !== original)!
  expect(entry).toBeTruthy()
  const renderSlot = (name: string, owner: any) => <>{slots.entries(name).map((e,i) => createElement(e.component,{...owner,key:i}))}</>
  const runtime = { useMenuLauncher: (selector: any) => selector(null), toggleCommandMenu: vi.fn(), onFiles: vi.fn() }
  return { entry, original, renderSlot, lists, slots, runtime }
}

it('首页提示按图覆盖，保留原编辑器、发送和输入回调', () => {
  const h=harness(), send=vi.fn(), change=vi.fn()
  render(createElement(h.entry.component,{...h.runtime,variant:'hero',placeholder:'原DSH提示',renderSlot:h.renderSlot,onSend:send,onChange:change}))
  const input=screen.getByPlaceholderText('跟超级玩家说你想做什么…')
  fireEvent.change(input,{target:{value:'需求'}})
  fireEvent.click(screen.getByRole('button',{name:'原发送'}))
  expect(send).toHaveBeenCalledTimes(1)
  expect(change).toHaveBeenCalledTimes(1)
  expect(input.closest('[data-original-editor="preserved"]')).toBeTruthy()
})

it('普通对话也不露原DSH创建任务与指令入口提示，阻塞原因仍保留', () => {
  const h=harness()
  const view=render(createElement(h.entry.component,{...h.runtime,variant:'composer',placeholder:'发消息或创建任务, / 调用指令, @ 文件或对话',renderSlot:h.renderSlot}))
  expect(screen.getByPlaceholderText('回复超级玩家…')).toBeTruthy()
  view.rerender(createElement(h.entry.component,{...h.runtime,variant:'composer',blocked:{reason:'尚未就绪'},placeholder:'尚未就绪',renderSlot:h.renderSlot}))
  expect(screen.getByPlaceholderText('尚未就绪')).toBeTruthy()
})

it('附件和指令菜单通过新子槽复用，原注入与字典不重写', () => {
  const h=harness()
  expect(h.entry.inject).toBe(h.original.inject)
  expect(h.entry.locale).toBe(h.original.locale)
  render(createElement(h.entry.component,{...h.runtime,variant:'hero',renderSlot:h.renderSlot}))
  expect(screen.getByText('原附件轨道')).toBeTruthy()
  expect(screen.getByText('原指令菜单')).toBeTruthy()
  const preset=h.slots.entries('conversation.hero.agentPreset')[0]
  expect(preset).toBeTruthy()
  expect(render(createElement(preset.component,{})).container.textContent).toBe('')
})

it('加号只露附件截图，选文件仍走原附件owner校验入口，Escape可关菜单', () => {
  const h=harness(), changed=vi.fn()
  const view=render(createElement(StrictMode,null,createElement(h.entry.component,{...h.runtime,variant:'hero',renderSlot:h.renderSlot,onChange:changed})))
  const plus=screen.getByRole('button',{name:'添加文件或调用指令'})
  fireEvent.click(plus)
  expect(plus.getAttribute('aria-expanded')).toBe('true')
  expect(view.container.querySelector('[data-menu-source]')?.textContent).toBe('command')
  expect(screen.getByRole('listbox')).toBeTruthy()
  expect(screen.getAllByRole('option')).toHaveLength(1)
  const option=screen.getByRole('option',{name:/附件.*截图/})
  expect(screen.queryByText('反馈',{exact:true})).toBeNull()
  expect(screen.queryByText('权限',{exact:true})).toBeNull()
  expect(screen.queryByText('下载日志',{exact:true})).toBeNull()
  fireEvent.keyDown(option,{key:'Escape',code:'Escape'})
  expect(screen.queryByRole('listbox')).toBeNull()
  fireEvent.click(plus)
  fireEvent.click(screen.getByRole('option',{name:/附件.*截图/}))
  const picker=view.container.querySelector('input[type="file"]')!
  expect(picker).toBeTruthy()
  const file=new File(['test'],'附件走查.txt',{type:'text/plain'})
  // Browser FileList is live: resetting input.value empties it. Snapshot before clearing.
  const live = { 0:file, length:1, *[Symbol.iterator]() { if(this.length) yield this[0] } }
  Object.defineProperty(picker,'files',{configurable:true,get:()=>live})
  Object.defineProperty(picker,'value',{configurable:true,get:()=>'',set:()=>{live.length=0}})
  fireEvent.change(picker)
  expect(h.runtime.onFiles).toHaveBeenCalledWith([file])
  expect(changed).not.toHaveBeenCalled()
})

it('原附件入口不允许添加文件时，新菜单也禁用该选项',()=>{
  const h=harness()
  render(createElement(h.entry.component,{...h.runtime,canAcceptDrop:false,variant:'hero',renderSlot:h.renderSlot}))
  fireEvent.click(screen.getByRole('button',{name:'添加文件或调用指令'}))
  const option=screen.getByRole('option',{name:/附件.*截图/}) as HTMLButtonElement
  expect(option.disabled).toBe(true)
  fireEvent.click(option)
  expect(h.runtime.onFiles).not.toHaveBeenCalled()
})

const readySessions=(select:any)=>select({phase:'ready',ids:[],byId:{}})
it('已配置且没有会话的冷启动自动建立默认对话，后续渲染不重复触发',async()=>{
  const startChat=vi.fn(async()=>{})
  const h=harness({startChat,useSetupLocked:()=>false})
  const props={...h.runtime,variant:'hero',occurrence:'main',disabled:true,renderSlot:h.renderSlot,useSessions:readySessions}
  const v=render(createElement(h.entry.component,props))
  await act(async()=>{await Promise.resolve()})
  expect(startChat).toHaveBeenCalledTimes(1)
  v.rerender(createElement(h.entry.component,{...props}))
  await act(async()=>{await Promise.resolve()})
  expect(startChat).toHaveBeenCalledTimes(1)
})
it('首配锁定时不通过首页自动建立会话',async()=>{
  const startChat=vi.fn(async()=>{})
  const h=harness({startChat,useSetupLocked:()=>true})
  render(createElement(h.entry.component,{...h.runtime,variant:'hero',occurrence:'main',disabled:true,renderSlot:h.renderSlot,useSessions:readySessions}))
  await act(async()=>{await Promise.resolve()})
  expect(startChat).not.toHaveBeenCalled()
})
it('已有会话时冷启动不抢走它另开对话',async()=>{
  const startChat=vi.fn(async()=>{})
  const h=harness({startChat,useSetupLocked:()=>false})
  render(createElement(h.entry.component,{...h.runtime,sessionId:'existing',variant:'hero',occurrence:'main',renderSlot:h.renderSlot,useSessions:readySessions}))
  await act(async()=>{await Promise.resolve()})
  expect(startChat).not.toHaveBeenCalled()
})
it('嵌入式输入框不触发首页自动补建会话',async()=>{
  const startChat=vi.fn(async()=>{})
  const h=harness({startChat,useSetupLocked:()=>false})
  render(createElement(h.entry.component,{...h.runtime,variant:'hero',occurrence:'embedded',renderSlot:h.renderSlot,useSessions:readySessions}))
  await act(async()=>{await Promise.resolve()})
  expect(startChat).not.toHaveBeenCalled()
})

it.each([
 [{variant:'hero',hasProposal:false,dispatcher:'ready',menuOpen:false,hasConfirmedTask:false},'跟超级玩家说你想做什么…'],
 [{variant:'composer',hasProposal:true,dispatcher:'ready',menuOpen:false,hasConfirmedTask:false},'回复超级玩家，比如“没登录的算没通，会花钱的也测”…'],
 [{variant:'composer',hasProposal:true,dispatcher:'missing',menuOpen:false,hasConfirmedTask:false},'回复超级玩家…'],
 [{variant:'composer',hasProposal:true,dispatcher:'ready',menuOpen:true,hasConfirmedTask:false},'回复超级玩家…'],
 [{variant:'composer',hasProposal:false,dispatcher:'ready',menuOpen:false,hasConfirmedTask:true},'接着聊…'],
 [{variant:'composer',hasProposal:true,dispatcher:'ready',menuOpen:false,hasConfirmedTask:true},'回复超级玩家，比如“没登录的算没通，会花钱的也测”…'],
 [{variant:'composer',hasProposal:false,dispatcher:'unknown',menuOpen:false,hasConfirmedTask:false},'回复超级玩家…'],
])('01/03/03b/03c/19输入提示取对应真实状态：%s',(context,text)=>{
 const resolve=modules['../src/client/HomeChrome.tsx']?.resolveComposerPlaceholder
 expect(resolve,'缺少实际使用的上下文提示解析').toBeTypeOf('function')
 expect(resolve(context)).toBe(text)
})
it('原DSH输入框接收注入的上下文提示，阻塞原因优先且编辑器不重建',()=>{
 const usePlaceholder=vi.fn(()=> '接着聊…'),h=harness({usePlaceholder})
 const p={...h.runtime,sessionId:'s',variant:'composer',placeholder:'原DSH指令提示',renderSlot:h.renderSlot}
 const v=render(createElement(h.entry.component,p));const editor=screen.getByPlaceholderText('接着聊…')
 expect(usePlaceholder).toHaveBeenCalledWith('s','composer')
 v.rerender(createElement(h.entry.component,{...p,blocked:{reason:'尚未就绪'},placeholder:'尚未就绪'}))
 expect(screen.getByPlaceholderText('尚未就绪')).toBe(editor)
})
