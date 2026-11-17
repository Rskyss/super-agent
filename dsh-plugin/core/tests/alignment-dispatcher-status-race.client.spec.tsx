// @vitest-environment jsdom
import {act,cleanup,render,screen} from '@testing-library/react'
import {afterEach,expect,it,vi} from 'vitest'
import {ConfirmDock} from '../src/client/ConfirmDock.tsx'
import {createComposerHintRegistry} from '../src/client/HomeChrome.tsx'
afterEach(()=>{cleanup();vi.useRealTimers()})
it('同会话旧调度员状态迟到不能覆盖新就绪、重露缺项或回滚确认提示',async()=>{
 vi.useFakeTimers()
 const snapshot={rows:[],self:null,project:'/project',dispatcherLabel:'Codex',rowsFailed:false,selfFailed:false}
 const feed={getSnapshot:()=>snapshot,subscribe:()=>()=>{},refresh:()=>{},dispose:()=>{}}
 const hints=createComposerHintRegistry({forSession:()=>feed,dispose:()=>{}})
 let resolveEarlier:((value:any)=>void)|undefined
 const dispatcherStatus=vi.fn().mockImplementationOnce(()=>new Promise(r=>{resolveEarlier=r})).mockResolvedValue({ok:true,value:'ready'})
 function Probe(){return <output data-testid="placeholder">{hints.usePlaceholder('s' as any,'composer')}</output>}
 render(<><Probe/><ConfirmDock {...{sessionId:'s',proposal:async()=>({ok:true,value:{id:'p',title:'本轮',do:'问候',notDo:'联网',doneWhen:'可见',questions:[]}}),dispatcherStatus,confirm:vi.fn(),dismiss:vi.fn(),inputActions:{setDraft:vi.fn(),submit:vi.fn()},useInput:(f:any)=>f({draft:'',attachmentIds:[],phase:'plain'}),onComposerState:(v:any)=>hints.setConfirmationState('s',v)} as any}/></>)
 await act(async()=>{await vi.advanceTimersByTimeAsync(10500)})
 expect(dispatcherStatus).toHaveBeenCalledTimes(2)
 const ready='回复超级玩家，比如“没登录的算没通，会花钱的也测”…'
 expect(screen.getByTestId('placeholder').textContent).toBe(ready)
 expect(screen.queryByText('！调度员',{exact:true})).toBeNull()
 await act(async()=>{resolveEarlier?.({ok:true,value:'missing'});for(let i=0;i<5;i++)await Promise.resolve()})
 expect(screen.getByTestId('placeholder').textContent).toBe(ready)
 expect(screen.queryByText('！调度员',{exact:true})).toBeNull()
 await act(async()=>hints.dispose())
})
