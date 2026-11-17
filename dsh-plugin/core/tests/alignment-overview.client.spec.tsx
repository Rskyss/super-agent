// @vitest-environment jsdom
import {act,cleanup,fireEvent,render,screen,within} from '@testing-library/react'
import {afterEach,expect,it,vi} from 'vitest'
import {TasksProbe} from '../src/client/TasksProbe.tsx'
afterEach(cleanup)
const rows=[
 {id:'CARD-022',title:'正在改问候',state:'开发中',phaseCode:'running',phaseLabel:'写代码',needsYou:false,actions:[],updated:'2026-11-16',kind:'work',flow:'开发软件',reviewRounds:1,elapsed:30,isRunningNow:true,archived:false},
 {id:'CARD-023',title:'排队的卡',state:'待对齐',phaseCode:'plan_waiting',phaseLabel:'等待写方案',needsYou:false,actions:[],updated:'2026-11-16',kind:'work',flow:'开发软件',isRunningNow:false,archived:false},
 {id:'CARD-024',title:'等我验收的卡',state:'待验收',phaseCode:'review',phaseLabel:'等你验收',needsYou:true,actions:['accept'],updated:'2026-11-16',kind:'work',flow:'开发软件',isRunningNow:false,archived:false},
 {id:'CARD-025',title:'做好了的卡',state:'已合并',phaseCode:'done',phaseLabel:'已完成',needsYou:false,actions:[],updated:'2026-11-16',kind:'work',flow:'开发软件',completedAt:'2026-11-16T09:00:00',firstPass:true,isRunningNow:false,archived:false},
 {id:'CARD-027',title:'等我决定的卡',state:'需Claude介入',phaseCode:'blocked',phaseLabel:'卡住了',needsYou:true,actions:['revise','resume','discard'],updated:'2026-11-16',kind:'work',flow:'开发软件',isRunningNow:false,archived:false},
 {id:'CARD-026',title:'归档旧卡',state:'已合并',phaseCode:'done',phaseLabel:'已完成',needsYou:false,actions:[],updated:'2025-11-16',kind:'work',flow:'开发软件',completedAt:'2025-11-16T09:00:00',firstPass:null,isRunningNow:false,archived:true},
]
function setup(){const openTask=vi.fn(async()=>{});render(<TasksProbe cards={async()=>({ok:true,value:rows as any})} openTask={openTask} startChat={async()=>{}} now={()=>new Date('2026-11-16T10:00:00')}/>);return openTask}
it('09只出全部干活已归档三个标签，未来分类入口隐藏',async()=>{
 setup();await screen.findByText('正在改问候');expect(screen.getAllByRole('tab').map(e=>e.textContent)).toEqual(['全部','干活','已归档'])
 expect(screen.queryByRole('tab',{name:'造能力'})).toBeNull();expect(screen.queryByRole('tab',{name:'接零件'})).toBeNull()
})
it('09进行中只计真实运行，暂停不装转圈，等待及完成数字仍取真实状态',async()=>{
 setup();await screen.findByText('正在改问候');const stats=screen.getByRole('region',{name:'任务统计'})
 expect(within(within(stats).getByText('进行中',{exact:true}).parentElement!).getByText('1',{exact:true})).toBeTruthy()
 expect(within(stats).getByText('1 / 1',{exact:true})).toBeTruthy()
 const paused=screen.getByText('排队的卡').closest('tr')!;expect(within(paused).getByText('暂停',{exact:true})).toBeTruthy()
 const blocked=screen.getByText('等我决定的卡').closest('tr')!;expect(blocked.textContent).toContain('需要你决定');expect(blocked.textContent).not.toContain('Claude')
})
it('09编号简写为22仍能按完整编号搜索、键盘打开真实卡，归档只在归档页',async()=>{
 const open=setup();await screen.findByText('正在改问候');const row=screen.getByText('正在改问候').closest('tr')!
 expect(within(row).getByText('22',{exact:true})).toBeTruthy();expect(within(row).queryByText('CARD-022',{exact:true})).toBeNull()
 fireEvent.change(screen.getByRole('textbox',{name:'搜索任务'}),{target:{value:'CARD-022'}});expect(screen.queryByText('排队的卡')).toBeNull()
 fireEvent.keyDown(screen.getByText('正在改问候').closest('tr')!,{key:'Enter'});await act(async()=>{});expect(open).toHaveBeenCalledWith('CARD-022')
 fireEvent.change(screen.getByRole('textbox',{name:'搜索任务'}),{target:{value:''}});fireEvent.click(screen.getByRole('tab',{name:'已归档'}));expect(screen.getByText('归档旧卡')).toBeTruthy();expect(screen.queryByText('正在改问候')).toBeNull()
})
it('17空任务时在标题前显示DSH任务图标，文案和主动作保留',async()=>{
 render(<TasksProbe cards={async()=>({ok:true,value:[]})} openTask={async()=>{}} startChat={async()=>{}}/> )
 const title=await screen.findByText('还没有任务',{exact:true});const icon=title.parentElement!.querySelector('svg')
 expect(icon,'17缺少标题前任务图标').not.toBeNull();expect(icon!.compareDocumentPosition(title)&Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
 expect(screen.getByRole('button',{name:'去找超级玩家聊'})).toBeTruthy()
})
