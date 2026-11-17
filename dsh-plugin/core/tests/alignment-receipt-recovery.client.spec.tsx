// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ConfirmDock } from '../src/client/ConfirmDock.tsx'
import { TaskReceipt } from '../src/client/TaskReceipt.tsx'
afterEach(() => { cleanup(); vi.useRealTimers() })
it.each([false, true])('成功建卡后元数据短暂失败仍恢复原轮回执，离开确认组件=%s', async (leaveDock) => {
 vi.useFakeTimers()
 const proposal={id:'p',title:'本轮测试',do:'增加问候',notDo:'不联网',doneWhen:'有问候',questions:[]}
 const row={id:'CARD-002',title:'本轮测试',phaseCode:'planning',phaseLabel:'调度员写方案',needsYou:false,state:'待对齐',createdAt:'2026-11-16T09:18:20Z',requirement:proposal}
 const start={seq:7,time:Date.parse('2026-11-16T09:18:00Z')}
 const timeline={turnOrder:[1],turns:new Map([[1,{start,end:{seq:23,time:Date.parse('2026-11-16T09:18:20.100Z')}}]])}
 const useChat=(select:any)=>select({timeline})
 const records:any={}
 const remember=vi.fn((project:string,card:string,anchor:any)=>{records[project]={[card]:anchor}})
 const sessionCards=vi.fn().mockRejectedValueOnce(new Error('temporary connection loss')).mockResolvedValue({ok:true,value:[row]})
 let feedSnap:any={rows:[],self:null,dispatcherLabel:'Codex',project:'/project',selfFailed:false,rowsFailed:false}
 const feed={getSnapshot:()=>feedSnap,subscribe:()=>()=>{},refresh:()=>{},dispose:()=>{}}
 const dock={sessionId:'s',proposal:async()=>({ok:true,value:proposal}),confirm:vi.fn(async()=>({ok:true,value:{ok:true,card:'CARD-002',planning:true,message:''}})),dismiss:vi.fn(),dispatcherStatus:async()=>({ok:true,value:'ready'}),inputActions:{setDraft:vi.fn(),submit:vi.fn()},useInput:(f:any)=>f({draft:'',attachmentIds:[],phase:'plain'}),useChat,actions:{remember},sessionCards,projectStatus:async()=>({ok:true,value:{project:'/project'}})} as any
 const tree=(showDock:boolean)=><>{showDock?<ConfirmDock {...dock}/>:null}<TaskReceipt {...{sessionId:'s',turn:{turn:1,start},feed,useChat,useStore:(f:any)=>f({records}),openTask:vi.fn()} as any}/></>
 const view=render(tree(true))
 await act(async()=>{await Promise.resolve();await Promise.resolve()})
 fireEvent.click(screen.getByRole('button',{name:'确认，建任务'}))
 await act(async()=>{await Promise.resolve();await Promise.resolve();await Promise.resolve()})
 expect(dock.confirm).toHaveBeenCalledTimes(1)
 expect(screen.queryByRole('group',{name:'确认卡：本轮测试'})).toBeNull()
 feedSnap={...feedSnap,rows:[row]}
 view.rerender(tree(!leaveDock))
 await act(async()=>{await vi.advanceTimersByTimeAsync(10000)})
 view.rerender(tree(!leaveDock))
 expect(sessionCards.mock.calls.length).toBeGreaterThanOrEqual(2)
 expect(remember).toHaveBeenCalledWith('/project','CARD-002',{startSeq:7,createdAt:'2026-11-16T09:18:20Z'})
 expect(screen.queryByText('需求 · 2 号',{exact:true})).not.toBeNull()
 expect(dock.confirm).toHaveBeenCalledTimes(1)
})
