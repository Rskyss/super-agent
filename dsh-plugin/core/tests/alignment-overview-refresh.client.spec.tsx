// @vitest-environment jsdom
import {act,cleanup,render,screen,within} from '@testing-library/react'
import {afterEach,expect,it,vi} from 'vitest'
import {TasksProbe} from '../src/client/TasksProbe.tsx'
import type {CardRow} from '../src/types.ts'
afterEach(()=>{cleanup();vi.useRealTimers()})
const row:CardRow={id:'CARD-001',title:'真实卡',state:'已合并',phaseCode:'done',phaseLabel:'已完成',needsYou:false,actions:[],updated:'2026-11-17',kind:'work',flow:'开发软件',reviewRounds:1,elapsed:65,completedAt:'2026-11-17T09:00:00',firstPass:true,archived:false}
async function flush(){await act(async()=>{for(let i=0;i<6;i++)await Promise.resolve()})}
it.each([{empty:true,thrown:false},{empty:false,thrown:false},{empty:true,thrown:true},{empty:false,thrown:true}])('a later refresh failure must be visible: empty=$empty thrown=$thrown',async({empty,thrown})=>{
 vi.useFakeTimers()
 const cards=vi.fn().mockResolvedValueOnce({ok:true,value:empty?[]:[row]})
 if(thrown)cards.mockRejectedValue(new Error('服务暂时不可用'))
 else cards.mockResolvedValue({ok:false,error:{message:'服务暂时不可用'}})
 render(<TasksProbe cards={cards} openTask={async()=>{}} startChat={async()=>{}}/>)
 await flush()
 if(empty)expect(screen.getByText('还没有任务',{exact:true})).toBeTruthy()
 else expect(screen.getByText('真实卡',{exact:true})).toBeTruthy()
 await act(async()=>{await vi.advanceTimersByTimeAsync(3000)})
 expect(screen.queryByRole('alert')).not.toBeNull()
 expect(screen.getByRole('alert').textContent).toContain('服务暂时不可用')
})
it('newer successful list remains after an earlier failed reply arrives late',async()=>{
 vi.useFakeTimers();let finishOld:any
 const cards=vi.fn().mockImplementationOnce(()=>new Promise(r=>{finishOld=r})).mockResolvedValue({ok:true,value:[row]})
 render(<TasksProbe cards={cards} openTask={async()=>{}} startChat={async()=>{}}/>)
 await act(async()=>{await vi.advanceTimersByTimeAsync(3000)})
 expect(screen.getByText('真实卡',{exact:true})).toBeTruthy()
 await act(async()=>{finishOld({ok:false,error:{message:'迟到旧错'}})})
 expect(screen.queryByRole('alert')).toBeNull()
 expect(screen.getByText('真实卡',{exact:true})).toBeTruthy()
})
it('year stats reject impossible dates and unknown firstPass stays unknown',async()=>{
 const rows=[{...row,id:'CARD-001',completedAt:'2026-02-30',firstPass:null},{...row,id:'CARD-002',completedAt:'2025-12-31',firstPass:null},{...row,id:'CARD-003',completedAt:'2026-01-01',firstPass:null}]
 render(<TasksProbe cards={async()=>({ok:true,value:rows as any})} openTask={async()=>{}} startChat={async()=>{}} now={()=>new Date('2026-11-17T12:00:00')}/>)
 await flush()
 const stats=screen.getByRole('region',{name:'任务统计'})
 expect(within(within(stats).getByText('今年完成',{exact:true}).parentElement!).getByText('1',{exact:true})).toBeTruthy()
 expect(within(within(stats).getByText('一次就验收通过',{exact:true}).parentElement!).getByText('—',{exact:true})).toBeTruthy()
})
// 第七批 7-4：任务总览排序——等你处理的排最前，再进行中 / 没开工的，最后已完成；组内新的在前（对照 09）。
it('table order: needs-you first, then active, then finished; newest first inside each group',async()=>{
 const rows=[
  {...row,id:'CARD-001',title:'早完成',updated:'2026-11-10'},
  {...row,id:'CARD-002',title:'在跑',state:'开发中',phaseCode:'running',phaseLabel:'进行中',isRunningNow:true,completedAt:null,updated:'2026-11-11'},
  {...row,id:'CARD-003',title:'等你批',state:'待对齐',phaseCode:'plan_ready',phaseLabel:'等你批准方案',needsYou:true,completedAt:null,updated:'2026-11-12'},
  {...row,id:'CARD-004',title:'晚完成',updated:'2026-11-15'},
  {...row,id:'CARD-005',title:'等你验收',state:'待验收',phaseCode:'review',phaseLabel:'等你验收',needsYou:true,completedAt:null,updated:'2026-11-16'},
 ]
 render(<TasksProbe cards={async()=>({ok:true,value:rows as any})} openTask={async()=>{}} startChat={async()=>{}}/>)
 await flush()
 const titles=within(screen.getByRole('table')).getAllByRole('row').slice(1).map(r=>r.querySelector('td:nth-child(2)')?.textContent)
 expect(titles).toEqual(['等你验收','等你批','在跑','晚完成','早完成'])
})
