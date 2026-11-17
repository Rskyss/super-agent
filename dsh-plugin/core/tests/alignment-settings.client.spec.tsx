// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { BlocksSettingsSection } from '../src/client/BlocksSettingsSection.tsx'
const modules=import.meta.glob('../src/client/SettingsChrome.tsx',{eager:true}) as Record<string,Record<string,any>>
afterEach(cleanup)
const ok=<T,>(value:T)=>({ok:true as const,value})
function setup(extra: Record<string,unknown>={}) {
  const p={close:vi.fn(),dispatcherState:vi.fn(async()=>ok({status:'missing',tool:'',label:'',model:'',reason:'',verified:false})),
    modelCatalog:vi.fn(async()=>ok({groups:[{id:'deepseek',name:'DeepSeek',models:[{id:'deepseek-v4.1-flash',name:'deepseek-v4.1-flash'}]}],default:{provider:'deepseek',model:'deepseek-v4.1-flash'}})),
    openAssistantSetup:vi.fn(()=>true),startDispatcherChat:vi.fn(async()=>{})}
  render(<BlocksSettingsSection {...p as any} {...extra}/>);return p
}
it('06a积木库按大脑、干活的、工具分组，只有图上的内容',async()=>{
  setup()
  expect(await screen.findByText('DeepSeek · deepseek-v4.1-flash',{exact:true})).toBeTruthy()
  expect(screen.getByText('超级玩家在用',{exact:true})).toBeTruthy()
  expect(screen.getByRole('heading',{name:'大脑（按量付费接口）'})).toBeTruthy()
  expect(screen.getByRole('heading',{name:'干活的（订阅的命令行工具）'})).toBeTruthy()
  expect(screen.getByRole('heading',{name:'工具（命令行工具、MCP、接口）'})).toBeTruthy()
  expect(screen.getAllByText('还没有',{exact:true})).toHaveLength(2)
  expect(screen.queryByText('角色',{exact:true})).toBeNull()
  expect(screen.queryByText(/安全说明：/)).toBeNull()
})
it('更换模型走现有可取消引导，接入仍先开对话再确认',async()=>{
  const p=setup()
  await screen.findByText('DeepSeek · deepseek-v4.1-flash',{exact:true})
  fireEvent.click(screen.getByRole('button',{name:'更换'}))
  expect(p.openAssistantSetup).toHaveBeenCalledTimes(1)
  expect(p.close).toHaveBeenCalledTimes(1)
  fireEvent.click(screen.getByRole('button',{name:/让超级玩家帮我接一个/}))
  expect(p.startDispatcherChat).toHaveBeenCalledWith('connect','')
})
it('设置壳保留DSH，仅用积木库标记筛导航并覆盖打开配置文件动作',()=>{
  const register=modules['../src/client/SettingsChrome.tsx']?.registerSuperagentSettingsChrome
  expect(register,'缺少仅覆盖设置导航的公开slot组合').toBeTypeOf('function')
  const entries:any[]=[]
  register({inject:(_name:string,cb:()=>unknown)=>cb(),register:(def:any,C:any)=>{entries.push({def,C});return()=>{}}})
  const icon=entries.find(e=>e.def.name==='settings.section.icon'&&e.def.id==='superagent-blocks')
  expect(icon).toBeTruthy()
  const view=render(<icon.C size={16} active={true}/>)
  expect(view.container.querySelector('[data-superagent-settings-entry]')).toBeTruthy()
  const action=entries.find(e=>e.def.name==='settings.action'&&e.def.id==='open-document')
  expect(action.def.priority).toBeLessThan(0)
  expect(render(<action.C/>).container.children).toHaveLength(0)
})
it('非钥匙串旧兼容分支保留原模型页依赖，正式分支不退回已藏导航',async()=>{
  const p=setup({useRestrictedSettings:()=>false})
  expect(await screen.findByText(/当前默认模型：/)).toBeTruthy()
  expect(screen.getByText('角色',{exact:true})).toBeTruthy()
  expect(p.openAssistantSetup).not.toHaveBeenCalled()
})
it('确认的非钥匙串模式不标记为限制导航',()=>{
  const register=modules['../src/client/SettingsChrome.tsx']?.registerSuperagentSettingsChrome
  expect(register).toBeTypeOf('function')
  const entries:any[]=[]
  register({inject:(_name:string,cb:()=>unknown)=>cb(),register:(def:any,C:any)=>{entries.push({def,C});return()=>{}}},()=>false)
  const icon=entries.find(e=>e.def.name==='settings.section.icon')
  const view=render(<icon.C size={16} active={true}/>)
  expect(view.container.querySelector('[data-superagent-settings-entry]')).toBeNull()
})

// 第六批 6-2：积木库补全（对照 06）——一键检测全部、干活的 / 工具列真实零件；没有就“还没有”。
it('06积木库：真实零件按组列出，被几张能力卡用到；一键检测逐个查、检测中不能再点',async()=>{
  let finish:(v:any)=>void=()=>{}
  const recheckDispatcher=vi.fn(async()=>ok({status:'ready',tool:'codex',label:'Codex',model:'gpt',reason:'',verified:true}))
  const checkBlock=vi.fn(()=>new Promise<any>(r=>{finish=r}))
  setup({
    dispatcherState:vi.fn(async()=>ok({status:'ready',tool:'codex',label:'Codex',model:'gpt',reason:'',verified:true})),
    blocks:vi.fn(async()=>ok([
      {id:'qwen',name:'千问',group:'worker',status:'ok',reason:'',usedBy:1},
      {id:'amap',name:'高德地图',group:'tool',status:'never',reason:'',usedBy:0},
    ])),
    recheckDispatcher,checkBlock,
  })
  expect(await screen.findByText('千问',{exact:true})).toBeTruthy()
  expect(screen.getByText('订阅 · 被 1 张能力卡用到')).toBeTruthy()
  expect(screen.getByText('高德地图',{exact:true})).toBeTruthy()
  expect(screen.getByText('没被用到')).toBeTruthy()
  expect(screen.queryByText('还没有',{exact:true})).toBeNull()
  const btn=screen.getByRole('button',{name:'一键检测全部'})
  fireEvent.click(btn)
  await screen.findAllByText('检测中')
  expect((screen.getByRole('button',{name:/检测中|一键检测全部/}) as HTMLButtonElement).disabled).toBe(true)
  await vi.waitFor(()=>expect(checkBlock).toHaveBeenCalledWith('qwen'))
  finish(ok({ok:false,reason:'要先填密钥'}))
  await vi.waitFor(()=>expect(checkBlock).toHaveBeenCalledWith('amap'))
  finish(ok({ok:true,reason:''}))
  expect(await screen.findByText('要先填密钥')).toBeTruthy()
  expect(recheckDispatcher).toHaveBeenCalledTimes(1)
  await vi.waitFor(()=>expect((screen.getByRole('button',{name:'一键检测全部'}) as HTMLButtonElement).disabled).toBe(false))
})
it('06积木库：大脑那行不写花费',async()=>{
  setup()
  await screen.findByText('DeepSeek · deepseek-v4.1-flash',{exact:true})
  expect(screen.queryByText(/¥|花费|本月/)).toBeNull()
})
// 复查第 1 轮中等 9：一键检测要真测大脑；没测通的调度员那行检测时也显示“检测中”。
it('06积木库一键检测：大脑真测、没测通的调度员也显示检测中',async()=>{
  let finishBrain:(v:any)=>void=()=>{}
  const checkAssistantModel=vi.fn(()=>new Promise<any>(r=>{finishBrain=r}))
  const recheckDispatcher=vi.fn(()=>new Promise<any>(()=>{}))
  setup({
    dispatcherState:vi.fn(async()=>ok({status:'missing',tool:'codex',label:'Codex',model:'',reason:'没登录',verified:false})),
    blocks:vi.fn(async()=>ok([])),recheckDispatcher,checkBlock:vi.fn(),checkAssistantModel,
  })
  await screen.findByText(/没登录/)
  fireEvent.click(screen.getByRole('button',{name:'一键检测全部'}))
  expect((await screen.findAllByText('检测中')).length).toBe(2)
  expect(checkAssistantModel).toHaveBeenCalledTimes(1)
  await act(async()=>{finishBrain(ok({ok:false,reason:'连不上：401'}))})
  expect(await screen.findByText('连不上：401')).toBeTruthy()
})
