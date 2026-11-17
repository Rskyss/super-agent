// @vitest-environment jsdom
import {cleanup,render,screen} from '@testing-library/react'
import {afterEach,expect,it,vi} from 'vitest'
import {TaskPageHeader} from '../src/client/TaskPageChrome.tsx'
import {ProgressPanel} from '../src/client/ProgressPanel.tsx'
afterEach(cleanup)
it.each([
 ['plan_ready','等你批准方案',false,'等你批方案'],
 ['running','进行中',true,'运行中'],
 ['running','进行中',false,'暂停'],
 ['blocked','需Claude介入',false,'停下了 · 需要你决定'],
] as const)('任务标题状态按图且不把静止装作运行：%s',(phaseCode,phaseLabel,isRunningNow,label)=>{
 render(<TaskPageHeader task={{id:'CARD-022',title:'加问候',phaseCode,phaseLabel,isRunningNow} as any} dispatcherLabel="Codex" view={null} selectView={vi.fn()}/> )
 expect(screen.getByText(`调度员 Codex · ${label}`,{exact:true})).toBeTruthy()
 expect(document.body.textContent).not.toContain('Claude')
})
it('右栏等批方案状态按图称呼，与标题一致',async()=>{
 render(<ProgressPanel card="CARD-022" view={async()=>({ok:true,value:JSON.stringify({isRunningNow:false,card:{id:'CARD-022',title:'加问候',state:'待对齐',phase:{code:'plan_ready',label:'等你批准方案',needs_you:true}},stations:[]})})}/> )
 expect(await screen.findByText('等你批方案',{exact:true})).toBeTruthy()
 expect(screen.queryByText('等你批准方案',{exact:true})).toBeNull()
})
