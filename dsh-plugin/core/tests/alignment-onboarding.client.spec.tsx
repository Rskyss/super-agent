// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { createElement } from 'react'
import { FirstUseSetup } from '../src/client/FirstUseSetup.tsx'
import { OnboardingUiStore } from '../src/client/OnboardingOverlay.tsx'
const modules=import.meta.glob('../src/client/OnboardingPage.tsx',{eager:true}) as Record<string,Record<string,any>>
afterEach(cleanup)
const props=(extra:Record<string,unknown>={})=>({presentation:'page',debounceMs:0,loadModels:vi.fn(async()=>[{id:'test',name:'测试模型'}]),complete:vi.fn(async()=>{}),onDone:vi.fn(),...extra})
it('00a首次页只先露服务商和Key，确认与模型在验证通过后才展开',async()=>{
  const p=props();render(createElement(FirstUseSetup,p as any))
  expect(screen.getByRole('heading',{name:'配置一个大模型来做你的超级玩家吧'})).toBeTruthy()
  expect(screen.getByText('服务商',{exact:true})).toBeTruthy()
  expect(screen.getByText('只存在这台电脑的钥匙串里，不会上传。',{exact:true})).toBeTruthy()
  expect(screen.getByRole('link',{name:'去哪里拿？'}).getAttribute('href')).toContain('deepseek')
  expect(screen.queryByRole('button',{name:'确认'})).toBeNull()
  expect(screen.queryByRole('radiogroup')).toBeNull()
  expect(screen.queryByText(/隐私说明/)).toBeNull()
  fireEvent.change(screen.getByLabelText('API Key'),{target:{value:'alignment-test-only'}})
  expect(await screen.findByText('选一个模型',{exact:true})).toBeTruthy()
  expect(screen.getByText('有效',{exact:true})).toBeTruthy()
  expect(screen.getByText('先选一个模型',{exact:true})).toBeTruthy()
  const confirm=screen.getByRole('button',{name:'确认'}) as HTMLButtonElement
  expect(confirm.disabled).toBe(true)
  fireEvent.click(screen.getByRole('radio',{name:'测试模型'}))
  expect(screen.queryByText('先选一个模型',{exact:true})).toBeNull()
  fireEvent.click(confirm)
  await act(async()=>{await Promise.resolve();await Promise.resolve()})
  expect(p.complete).toHaveBeenCalledTimes(1)
  expect(p.onDone).toHaveBeenCalledTimes(1)
})
it('00d验证错误只在Key下方一行，不展开模型或确认；换服务商清空',async()=>{
  render(createElement(FirstUseSetup,props({loadModels:vi.fn(async()=>{throw new Error('Key 不对或已失效，请重新复制')})}) as any))
  fireEvent.change(screen.getByLabelText('API Key'),{target:{value:'alignment-invalid'}})
  expect(await screen.findByRole('alert')).toHaveProperty('textContent','Key 不对或已失效，请重新复制')
  expect(screen.getByText('未通过',{exact:true})).toBeTruthy()
  const recoveryLinks=screen.getAllByRole('link',{name:'去哪里拿？'})
  expect(recoveryLinks).toHaveLength(2)
  expect(screen.getByRole('alert').compareDocumentPosition(recoveryLinks[1]!)&Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(screen.getByRole('button',{name:'换一家服务商'})).toBeTruthy()
  expect(screen.queryByRole('button',{name:'确认'})).toBeNull()
  expect(screen.queryByRole('radiogroup')).toBeNull()
  fireEvent.click(screen.getByRole('button',{name:'阿里百炼'}))
  expect((screen.getByLabelText('API Key') as HTMLInputElement).value).toBe('')
  expect(screen.queryByRole('alert')).toBeNull()
})
it('网络恢复后重新粘贴同一Key会重新验证，不能清掉错误后停在空白',async()=>{
  const loadModels=vi.fn().mockRejectedValueOnce(new Error('网络暂时不可用')).mockResolvedValue([{id:'test',name:'测试模型'}])
  render(createElement(FirstUseSetup,props({loadModels}) as any))
  const paste=()=>fireEvent.paste(screen.getByLabelText('API Key'),{clipboardData:{getData:()=> 'alignment-same-key'}})
  paste();await screen.findByRole('alert')
  expect(loadModels).toHaveBeenCalledTimes(1)
  paste();await screen.findByRole('radio',{name:'测试模型'})
  expect(loadModels).toHaveBeenCalledTimes(2)
  expect(screen.queryByRole('alert')).toBeNull()
})
it('首次配置由主页面承载，底部输入框灰且不能跳过，应用根不加锁',()=>{
  const C=modules['../src/client/OnboardingPage.tsx']?.OnboardingPage
  expect(C).toBeTypeOf('function')
  const store=new OnboardingUiStore();store.applyHostState({keychainEnabled:true,required:true,configured:false} as any)
  const root=document.createElement('div');root.id='root';document.body.append(root)
  const view=render(createElement(C,{store,loadModels:vi.fn(async()=>[]),complete:vi.fn(async()=>{}),onOnboardingDone:vi.fn(async()=>{})}))
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(screen.getByRole('heading',{name:'配置一个大模型来做你的超级玩家吧'})).toBeTruthy()
  const composer=screen.getByRole('textbox',{name:'接好大模型后就能跟超级玩家说话…'}) as HTMLInputElement
  expect(composer.disabled).toBe(true)
  expect(screen.queryByRole('button',{name:/跳过|取消/})).toBeNull()
  expect(root.hasAttribute('inert')).toBe(false)
  view.unmount();root.remove()
})
it('首配必需时导航到其他面板会回配置，完成与卸载后释放导航',()=>{
  const install=modules['../src/client/OnboardingPage.tsx']?.installOnboardingPageNavigation
  expect(install).toBeTypeOf('function')
  const store=new OnboardingUiStore(),listeners=new Set<()=>void>()
  let activePanelId:string|null=null
  const layout={panelInfo:{getSnapshot:()=>({activePanelId}),subscribe:(cb:()=>void)=>{listeners.add(cb);return()=>listeners.delete(cb)}},selectPanel:vi.fn((id:string|null)=>{activePanelId=id;for(const cb of [...listeners])cb()})}
  const dispose=install(store,layout,'setup')
  expect(activePanelId).toBe('setup')
  store.applyHostState({keychainEnabled:true,required:true,configured:false} as any)
  layout.selectPanel('tasks');expect(activePanelId).toBe('setup')
  store.markCompleted({keychainEnabled:true,required:false,configured:true} as any)
  expect(activePanelId).toBeNull()
  layout.selectPanel('tasks');expect(activePanelId).toBe('tasks')
  dispose();expect(listeners.size).toBe(0)
  store.applyHostState({keychainEnabled:true,required:true,configured:false} as any)
  expect(activePanelId).toBe('tasks')
})
