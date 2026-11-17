// @vitest-environment jsdom
/// <reference types="vite/client" />
import { expect, it, vi } from 'vitest'
const modules=import.meta.glob('../src/client/chat-navigation.ts',{eager:true}) as Record<string,any>
function install(nav:any,fresh?:()=>unknown){const f=modules['../src/client/chat-navigation.ts']?.installChatNavigationDefaults;expect(f,'缺少原生新建入口的聊天模式边界').toBeTypeOf('function');return f(nav,fresh)}

it.each(['global-shortcut','brand'])('%s不带模式请求新建时不能复用空任务会话',entry=>{
 let selected='task-CARD-015'
 const nav={startSession:vi.fn((_workspace?:string,opts?:{agentPreset?:string})=>{selected=opts?.agentPreset==='superagent-chat'?'chat-new':'task-CARD-015'})}
 const dispose=install(nav)
 try{nav.startSession();expect(selected,entry).toBe('chat-new')}finally{dispose()}
})

it('明确请求任务模式的流程不变，普通请求保留工作区/草稿参数，卸载能恢复原入口',()=>{
 const start=vi.fn((_workspace?:string,_opts?:Record<string,unknown>)=>{})
 const nav={startSession:start};const dispose=install(nav)
 nav.startSession('workspace-1',{agentPreset:'superagent-task',prompt:'保留任务'})
 expect(start).toHaveBeenLastCalledWith('workspace-1',{agentPreset:'superagent-task',prompt:'保留任务'})
 nav.startSession('workspace-2',{prompt:'草稿',whenDraftExists:'append'})
 expect(start).toHaveBeenLastCalledWith('workspace-2',{agentPreset:'superagent-chat',prompt:'草稿',whenDraftExists:'append'})
 dispose();expect(nav.startSession).toBe(start)
})

it('原生无参数的新建入口复用现有明确新建聊天路径，已误标聊天的空任务也不能被拿来用',()=>{
 let selected='task-CARD-015'
 const old=vi.fn(()=>{selected='task-CARD-015'});const fresh=vi.fn(()=>{selected='fresh-chat'})
 const nav={startSession:old};const dispose=install(nav,fresh)
 nav.startSession();expect(selected).toBe('fresh-chat');expect(fresh).toHaveBeenCalledOnce();expect(old).not.toHaveBeenCalled();dispose()
})

it('两个插件安装生命周期交叠，先卸旧实例不能移除新实例的保护',()=>{
 const start=vi.fn();const nav={startSession:start};const a=install(nav);const b=install(nav);a();nav.startSession();expect(start).toHaveBeenLastCalledWith(undefined,{agentPreset:'superagent-chat'});b();expect(nav.startSession).toBe(start)
})

it('新建失败提示用大白话，不把HTTP路径和传输错误原文直接甩给用户',()=>{
 const format=modules['../src/client/chat-navigation.ts']?.formatChatNavigationError
 expect(format,'缺少新建失败的大白话错误边界').toBeTypeOf('function')
 const text=format(new Error('client API: superagent/status failed: transport failure for /api/superagent/status: HTTP 503'))
 expect(text).toMatch(/没建上新对话/);expect(text).toMatch(/连不上|连接/)
 expect(text).not.toMatch(/HTTP|client|transport|superagent\/status|\/api\//)
 expect(format(new Error('请先完成超级助手首次模型配置'))).toContain('请先完成超级玩家首次模型配置')
})
