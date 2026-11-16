// @vitest-environment jsdom
/// <reference types="vite/client" />
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { ComponentType } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import * as sessionClient from '../src/client/task-session-client.ts'

const modules=import.meta.glob('../src/client/TaskSidebar.tsx',{eager:true}) as Record<string,Record<string,ComponentType<any>>>
const ok=<T,>(value:T)=>({ok:true as const,value})
afterEach(cleanup)
const rows=[
 {id:'CARD-001',title:'要你批的任务',phaseCode:'plan_ready',phaseLabel:'等你批准方案',needsYou:true,state:'待对齐',actions:['approve'],updated:'2026-11-15'},
 {id:'CARD-002',title:'正在干的任务',phaseCode:'running',phaseLabel:'进行中',needsYou:false,state:'开发中',actions:['stop'],updated:'2026-11-15'},
 {id:'CARD-003',title:'已经做完的任务',phaseCode:'done',phaseLabel:'已完成',needsYou:false,state:'已合并',actions:[],updated:'2026-11-15'},
]
function setup(data=rows){
 const Sidebar=modules['../src/client/TaskSidebar.tsx']?.TaskSidebar
 expect(Sidebar,'缺少对话与任务两个标签的左栏').toBeTypeOf('function')
 const session=(id:string,title:string)=>({id,title,displayTitle:title,blank:false,running:false,updatedAt:1,cwd:'/test-project',retainedBy:{},projectionValues:{}})
 const snapshot={phase:'ready',ids:['chat1','task-12345678-CARD-002','opaque-task','archived'],byId:{chat1:session('chat1','聊产品方向'),'task-12345678-CARD-002':session('task-12345678-CARD-002','任务专属对话'), 'opaque-task':session('opaque-task','旧任务对话'),archived:session('archived','归档聊天')},projectionsBySession:{}}
 const workspaces={phase:'ready',state:'idle',error:null,items:[{workspaceId:'w1',path:'/test-project',title:'测试项目',sessionIds:snapshot.ids}],archivedSessionIds:['archived'],pinnedSessionIds:[]}
 const face={cards:vi.fn(async()=>ok(data)),taskSessions:vi.fn(async()=>ok({'CARD-001':'opaque-task','CARD-002':'task-12345678-CARD-002'})),openSession:vi.fn(),openTask:vi.fn(async()=>{}),startChat:vi.fn(async()=>{}),renameSession:vi.fn(async()=>ok({})),archiveSession:vi.fn(async()=>ok({})),showOverview:vi.fn(),useSessions:<T,>(f:(s:typeof snapshot)=>T)=>f(snapshot),useWorkspaces:<T,>(f:(s:typeof workspaces)=>T)=>f(workspaces),wide:true,expandSidebar:vi.fn()}
 const Component=Sidebar!;render(<Component {...face}/>);return face
}

it('对话标签不列出任务会话和已归档聊天，普通聊天可以切换',async()=>{
 const f=setup()
 expect(await screen.findByRole('tab',{name:'对话'})).toBeTruthy()
 await act(async()=>{await Promise.resolve()})
 expect(screen.getByText('聊产品方向')).toBeTruthy()
 expect(screen.queryByText('任务专属对话')).toBeNull()
 expect(screen.queryByText('旧任务对话')).toBeNull()
 expect(screen.queryByText('归档聊天')).toBeNull()
 fireEvent.click(screen.getByText('聊产品方向'))
 expect(f.openSession).toHaveBeenCalledWith('chat1')
})

it('任务标签数字只数等你处理，按三组列卡，点卡打开自己的任务对话',async()=>{
 const f=setup()
 await act(async()=>{await Promise.resolve()})
 const tab=screen.getByRole('tab',{name:/^任务/})
 expect(tab.textContent).toMatch(/任务\s*1/)
 fireEvent.click(tab)
 for(const group of ['等你处理','进行中','已完成'])expect(await screen.findByText(group,{exact:true})).toBeTruthy()
 fireEvent.click(screen.getByText('正在干的任务'))
 expect(f.openTask).toHaveBeenCalledWith('CARD-002')
 expect(f.showOverview).toHaveBeenCalledTimes(1)
})

it('普通聊天支持改名和归档，失败不冒充成功',async()=>{
 const f=setup()
 await screen.findByText('聊产品方向')
 fireEvent.click(screen.getByRole('button',{name:'重命名 聊产品方向'}))
 fireEvent.change(screen.getByRole('textbox',{name:'对话名称'}),{target:{value:'新的标题'}})
 fireEvent.click(screen.getByRole('button',{name:'保存'}))
 await act(async()=>{await Promise.resolve()})
 expect(f.renameSession).toHaveBeenCalledWith('chat1','新的标题')
 f.archiveSession.mockResolvedValueOnce({ok:false,error:{message:'暂时不能归档'}} as never)
 fireEvent.click(screen.getByRole('button',{name:'归档 聊产品方向'}))
 await act(async()=>{await Promise.resolve()})
 expect(f.archiveSession).toHaveBeenCalledWith('chat1')
 expect(screen.getByText(/暂时不能归档/)).toBeTruthy()
 expect(screen.getByText('聊产品方向')).toBeTruthy()
})

it('替换新会话按钮，点击不走会复用空任务对话的旧入口',async()=>{
 const NewChatAction=modules['../src/client/TaskSidebar.tsx']?.NewChatAction
 expect(NewChatAction,'缺少独立新聊天入口').toBeTypeOf('function')
 const old=vi.fn(),startChat=vi.fn(async()=>{})
 const Component=NewChatAction!;render(<Component startChat={startChat} wide><button onClick={old}>旧入口</button></Component>)
 expect(screen.queryByText('旧入口')).toBeNull()
 fireEvent.click(screen.getByRole('button',{name:'新会话'}))
 await act(async()=>{await Promise.resolve()})
 expect(startChat).toHaveBeenCalledTimes(1)
 expect(old).not.toHaveBeenCalled()
})

it('已完成组的放弃卡明确标记不做了，不能冒充做完',async()=>{
 setup([...rows,{...rows[2]!,id:'CARD-009',title:'已放弃测试',state:'已放弃',phaseCode:'dropped',phaseLabel:'不做了'}])
 await act(async()=>{await Promise.resolve()});fireEvent.click(screen.getByRole('tab',{name:/^任务/}))
 const label=await screen.findByText('已放弃测试',{exact:true})
 expect(label.closest('button')!.textContent).toContain('不做了')
})

it('主新会话按钮与品牌/快捷键一样，用中文显示连接失败',async()=>{
 const Component=modules['../src/client/TaskSidebar.tsx']!.NewChatAction!
 render(<Component wide startChat={async()=>{throw new Error('client API: superagent/status failed: HTTP 503')}} />)
 fireEvent.click(screen.getByRole('button',{name:'新会话'}))
 expect(await screen.findByText(/暂时连不上助手/)).toBeTruthy()
 expect(document.body.textContent).not.toMatch(/HTTP|client|superagent\/status/)
})

it('新聊天直接向DSH创建聊天模式的全新会话，不传要复用的任务会话编号',async()=>{
 const createChat=(sessionClient as unknown as {createChatSession?:(ports:any,project:string)=>Promise<string>}).createChatSession
 expect(createChat,'缺少新聊天创建策略').toBeTypeOf('function')
 let request:unknown
 const result=await createChat!({ensureWorkspace:async()=>ok({workspaceId:'w1'}),createSession:async(value:unknown)=>{request=value;return ok({sessionId:'new-chat'})}},'/test-project')
 expect(result).toBe('new-chat')
 expect(request).toEqual({workspaceId:'w1',agentPreset:'superagent-chat'})
})

it('已归档任务不占左栏数字或三组，仍可在总览归档页查看',async()=>{setup([...rows,{...rows[0]!,id:'CARD-004',title:'归档的任务',archived:true} as any]);await act(async()=>{await Promise.resolve()});const tab=screen.getByRole('tab',{name:/^任务/});expect(tab.textContent).toMatch(/任务\s*1/);fireEvent.click(tab);expect(screen.queryByText('归档的任务')).toBeNull()})
it('真的没有任务时左栏直说还没有任务，不列三组暂无',async()=>{setup([]);expect(await screen.findByText('还没有任务',{exact:true})).toBeTruthy();expect(screen.queryByText('等你处理',{exact:true})).toBeNull()})
