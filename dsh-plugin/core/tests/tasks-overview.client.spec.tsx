// @vitest-environment jsdom
import {act,cleanup,fireEvent,render,screen,within} from '@testing-library/react'
import {afterEach,expect,it,vi} from 'vitest'
import {TasksProbe} from '../src/client/TasksProbe.tsx'
afterEach(cleanup)
const ok=(value:any)=>({ok:true as const,value})
const rows=[
{id:'CARD-001',title:'刚做好的待办',state:'已合并',phaseCode:'done',phaseLabel:'已完成',needsYou:false,actions:[],updated:'2026-11-15',kind:'work',flow:'开发软件',reviewRounds:1,elapsed:65,completedAt:'2026-11-15T09:00:00',firstPass:true,archived:false},
{id:'CARD-002',title:'需要看一眼地图',state:'待验收',phaseCode:'review',phaseLabel:'等你验收',needsYou:true,actions:['accept'],updated:'2026-11-15',kind:'connect',flow:'接零件',reviewRounds:2,elapsed:120,archived:false},
{id:'CARD-003',title:'归档的历史',state:'已合并',phaseCode:'done',phaseLabel:'已完成',needsYou:false,actions:[],updated:'2025-11-15',kind:'work',flow:'开发软件',reviewRounds:null,elapsed:null,completedAt:'2025-11-15',firstPass:null,archived:true},
{id:'CARD-004',title:'正在写文章',state:'开发中',phaseCode:'running',phaseLabel:'进行中',needsYou:false,actions:[],updated:'2026-11-15',kind:'ability',flow:'造能力',reviewRounds:1,elapsed:10,archived:false},
]
function setup(data=rows){const face={status:async()=>ok({running:true,project:'/test',port:1}),cards:async()=>ok(data),view:async()=>ok('{}'),plan:async()=>ok('{}'),cardAction:vi.fn(),openTask:vi.fn(async()=>{}),startChat:vi.fn(async()=>{}),now:()=>new Date('2026-11-15T10:00:00')};render(<TasksProbe {...face}/>);return face}
it('任务总览有四个数字，表格和五类标签，行打开自己的任务对话',async()=>{const f=setup();await screen.findByText('刚做好的待办');for(const label of ['今年完成','进行中','等你处理','一次就验收通过'])expect(screen.getAllByText(label).length).toBeGreaterThan(0);for(const name of ['全部','干活','造能力','接零件','已归档'])expect(screen.getByRole('tab',{name})).toBeTruthy();expect(screen.queryByText('归档的历史')).toBeNull();const table=screen.getByRole('table');expect(within(table).getByText('复查轮次')).toBeTruthy();expect(table.textContent).not.toContain('端口');fireEvent.click(screen.getByText('需要看一眼地图'));await act(async()=>{});expect(f.openTask).toHaveBeenCalledWith('CARD-002')})
it('搜索和类型一起筛选，归档只在归档页出现',async()=>{setup();await screen.findByText('刚做好的待办');fireEvent.click(screen.getByRole('tab',{name:'接零件'}));expect(screen.queryByText('刚做好的待办')).toBeNull();expect(screen.getByText('需要看一眼地图')).toBeTruthy();fireEvent.change(screen.getByRole('textbox',{name:'搜索任务'}),{target:{value:'不存在'}});expect(screen.queryByText('需要看一眼地图')).toBeNull();fireEvent.change(screen.getByRole('textbox',{name:'搜索任务'}),{target:{value:''}});fireEvent.click(screen.getByRole('tab',{name:'已归档'}));expect(screen.getByText('归档的历史')).toBeTruthy();expect(screen.getByRole('table').textContent).not.toContain('NaN')})
it('空态不闪成没有任务，加载完才出引导，按钮打开普通新聊天',async()=>{const f=setup([]);expect(screen.queryByText('还没有任务',{exact:true})).toBeNull();await screen.findByText('还没有任务',{exact:true});fireEvent.click(screen.getByRole('button',{name:'去找超级助手聊'}));await act(async()=>{});expect(f.startChat).toHaveBeenCalledTimes(1)})
it('请求失败显示失败，不冒充空任务',async()=>{render(<TasksProbe {...({cards:async()=>({ok:false,error:{message:'服务暂时不可用'}}),startChat:vi.fn(),openTask:vi.fn()} as any)}/>);expect(await screen.findByText(/服务暂时不可用/)).toBeTruthy();expect(screen.queryByText('还没有任务',{exact:true})).toBeNull()})
