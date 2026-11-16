// @vitest-environment jsdom
/// <reference types="vite/client" />
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { ComponentType } from 'react'
import { afterEach, expect, it, vi } from 'vitest'

const modules=import.meta.glob('../src/client/DecisionDock.tsx',{eager:true}) as Record<string,Record<string,ComponentType<any>>>
const ok=<T,>(value:T)=>({ok:true as const,value})
afterEach(()=>{cleanup();vi.useRealTimers()})

it.each([null,false])('noopener打开预览返回%s时不误报拦截，保留本机链接',async result=>{
 const f=setup('review',['accept','revise'])
 f.openPreview.mockReturnValue(result)
 fireEvent.click(await screen.findByRole('button',{name:'打开预览'}))
 await screen.findByText(/预览已打开/)
 expect(document.body.textContent).not.toContain('拦截')
 expect(screen.getByRole('link').getAttribute('href')).toBe('http://127.0.0.1:3099/')
})
const plan=JSON.stringify({plan:'# 拆解方案\n\n## 要做什么（一句话）\n给清单显示版本\n\n## 拆成几步，每步做什么、产出什么\n1. 写测试\n2. 写代码\n\n## 你会看到什么结果\n能看见版本\n\n## 明确不做什么\n不改其他功能\n\n## 需要你确认的问题\n是否换行？建议保留换行\n',allowed:['todo.py','tests/test_todo.py'],test_command:'python3 -m unittest discover -s tests'})
const raw=(code:string,actions:string[],detail='')=>JSON.stringify({card:{id:'CARD-011',title:'显示版本',phase:{code,actions,detail,label:code==='blocked'?'需Claude介入':'等你处理'}},elapsed:60,stations:[],accept:{result:'版本已经可以显示',how:'打开结果看看',can_preview:true,copy_there:true,criteria:['只显示一行版本','原功能不变']}})
function setup(code:string,actions:string[],detail='',planReply:unknown=ok(plan),viewText?:string) {
 const Dock=modules['../src/client/DecisionDock.tsx']?.DecisionDock
 expect(Dock,'缺少贴输入框的阶段决定卡').toBeTypeOf('function')
 const face={card:'CARD-011',view:vi.fn(async()=>ok(viewText??raw(code,actions,detail))),plan:vi.fn(async()=>planReply),cardAction:vi.fn(async()=>ok({ok:true,message:''})),preview:vi.fn(async()=>ok({ok:true,mode:'web',url:'http://127.0.0.1:3099/'})),openPreview:vi.fn(),readDraft:vi.fn(()=>''),clearDraft:vi.fn(),hasAttachments:vi.fn(()=>false)}
 const Component=Dock!; render(<Component {...face}/>);return face
}

it('方案卡七行都在，问号不答也能批准，只提交后台允许的操作',async()=>{
 const f=setup('plan_ready',['approve','revise','discard'])
 for(const text of ['要做什么','分几步','做完你会看到','不做什么','只动这些地方','怎么算做好','要你拍板的'])expect(await screen.findByText(text,{exact:true})).toBeTruthy()
 expect(screen.getByText(/是否换行/)).toBeTruthy()
 fireEvent.click(screen.getByRole('button',{name:'批准，开工'}))
 await act(async()=>{await Promise.resolve()})
 expect(f.cardAction).toHaveBeenCalledWith('CARD-011','approve','')
})

it('方案默认用大白话，文件路径和运行命令只在详情中展示',async()=>{
 const payload=JSON.parse(plan)
 payload.plan=payload.plan.replace('给清单显示版本','给 todo.py 增加版本显示').replace('1. 写测试','1. 在 tests/test_todo.py 写测试').replace('能看见版本','运行 python3 todo.py version 会显示版本')
 setup('plan_ready',['approve','discard'],'',ok(JSON.stringify(payload)))
 await screen.findByText('要做什么',{exact:true})
 expect(document.body.textContent).not.toContain('tests/test_todo.py')
 expect(document.body.textContent).not.toContain('python3 todo.py')
 fireEvent.click(screen.getByRole('button',{name:'查看详情'}))
 expect(document.body.textContent).toContain('tests/test_todo.py')
 expect(document.body.textContent).toContain('python3 todo.py')
})

it('后台没给批准操作时不能显示批准按钮，也不自动执行',async()=>{
 const f=setup('plan_ready',['discard'])
 await screen.findByText('给清单显示版本')
 expect(screen.queryByRole('button',{name:'批准，开工'})).toBeNull()
 expect(f.cardAction).not.toHaveBeenCalled()
})

it('读不到方案时不能让用户盲批',async()=>{
 const f=setup('plan_ready',['approve','discard'],'',{ok:false,error:{message:'断开了'}})
 await act(async()=>{await Promise.resolve();await Promise.resolve()})
 const button=screen.queryByRole('button',{name:'批准，开工'})
 expect(button===null || (button as HTMLButtonElement).disabled).toBe(true)
 expect(f.cardAction).not.toHaveBeenCalled()
 expect(screen.getByText(/读不到.*方案/)).toBeTruthy()
})

it('曾读到旧方案，最新方案读取失败后不能继续批准旧内容',async()=>{
 vi.useFakeTimers()
 const f=setup('plan_ready',['approve','discard'])
 await act(async()=>{await Promise.resolve();await Promise.resolve()})
 expect((screen.getByRole('button',{name:'批准，开工'}) as HTMLButtonElement).disabled).toBe(false)
 f.plan.mockResolvedValue({ok:false,error:{message:'新方案没读到'}})
 await act(async()=>{vi.advanceTimersByTime(2000);await Promise.resolve();await Promise.resolve()})
 const button=screen.queryByRole('button',{name:'批准，开工'})
 expect(button===null || (button as HTMLButtonElement).disabled).toBe(true)
})

it('验收卡可打开真实预览，然后确认放进项目',async()=>{
 const f=setup('review',['accept','revise','discard'])
 fireEvent.click(await screen.findByRole('button',{name:'打开预览'}))
 await act(async()=>{await Promise.resolve()})
 expect(f.preview).toHaveBeenCalledWith('CARD-011','start')
 expect(f.openPreview).toHaveBeenCalledWith('http://127.0.0.1:3099/')
 fireEvent.click(screen.getByRole('button',{name:'没问题，放进项目'}))
 await act(async()=>{await Promise.resolve()})
 expect(f.cardAction).toHaveBeenCalledWith('CARD-011','accept','')
})

it('预览启动中不能同时放进项目或继续改',async()=>{
 const f=setup('review',['accept','revise'])
 f.preview.mockImplementationOnce(()=>new Promise(()=>{}))
 fireEvent.click(await screen.findByRole('button',{name:'打开预览'}))
 expect((screen.getByRole('button',{name:'没问题，放进项目'}) as HTMLButtonElement).disabled).toBe(true)
 expect((screen.getByRole('button',{name:'继续改'}) as HTMLButtonElement).disabled).toBe(true)
})

it('继续改没有意见时不提交空请求，有草稿才提交且成功后清草稿',async()=>{
 const f=setup('review',['accept','revise','discard'])
 fireEvent.click(await screen.findByRole('button',{name:'继续改'}))
 expect(f.cardAction).not.toHaveBeenCalled()
 f.readDraft.mockReturnValue('版本号放到第一行')
 fireEvent.click(screen.getByRole('button',{name:'继续改'}))
 await act(async()=>{await Promise.resolve()})
 expect(f.cardAction).toHaveBeenCalledWith('CARD-011','revise','版本号放到第一行')
 expect(f.clearDraft).toHaveBeenCalled()
})

it('验收卡展示逐条标准，预览失败不能假装打开',async()=>{
 const f=setup('review',['accept','revise','discard'])
 expect(await screen.findByText('只显示一行版本')).toBeTruthy()
 expect(screen.getByText('原功能不变')).toBeTruthy()
 f.preview.mockImplementationOnce(async()=>ok({ok:false,mode:'web',url:'',reason:'地址被占用了'}))
 fireEvent.click(screen.getByRole('button',{name:'打开预览'}))
 await act(async()=>{await Promise.resolve()})
 expect(f.openPreview).not.toHaveBeenCalled()
 expect(screen.getByText(/地址被占用了/)).toBeTruthy()
})

it('有追加修改时明确标识原验收标准，以追加要求为准',async()=>{
 const payload=JSON.parse(raw('review',['accept','revise']))
 payload.accept.changeNotes=['你确认把第1条从旧规则改成新规则']
 setup('review',['accept','revise'],'',ok(plan),JSON.stringify(payload))
 expect(await screen.findByText(/原验收标准.*追加要求.*为准/)).toBeTruthy()
 expect(screen.getByText('你确认把第1条从旧规则改成新规则')).toBeTruthy()
})

it('预览回了非本机网页地址时不打开',async()=>{
 const f=setup('review',['accept','revise'])
 f.preview.mockImplementationOnce(async()=>ok({ok:true,mode:'web',url:'javascript:alert(1)'}))
 fireEvent.click(await screen.findByRole('button',{name:'打开预览'}))
 await act(async()=>{await Promise.resolve()})
 expect(f.openPreview).not.toHaveBeenCalled()
})

it('预览只返回文件夹位置时不能声称已打开电脑里的文件夹',async()=>{
 const f=setup('review',['accept','revise'])
 f.preview.mockImplementationOnce(async()=>ok({ok:true,mode:'folder',url:'',path:'/tmp/测试副本'}))
 fireEvent.click(await screen.findByRole('button',{name:'打开预览'}))
 await act(async()=>{await Promise.resolve()})
 expect(f.openPreview).not.toHaveBeenCalled()
 expect(screen.queryByText(/已在电脑里打开/)).toBeNull()
})

it('继续改等待回包时新写的草稿保留，不被成功回包清空',async()=>{
 const f=setup('review',['accept','revise','discard'])
 let finish: ((v:Awaited<ReturnType<typeof f.cardAction>>)=>void)|undefined
 f.readDraft.mockReturnValue('版本号放在第一行')
 f.cardAction.mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve}))
 fireEvent.click(await screen.findByRole('button',{name:'继续改'}))
 f.readDraft.mockReturnValue('我还没说完的下一条意见')
 await act(async()=>{finish?.(ok({ok:true,message:''}))})
 expect(f.clearDraft).not.toHaveBeenCalled()
})

it('当前仍不能转交截图原图时要明确拦住，不能只发送文字悄悄丢掉附件',async()=>{
 const f=setup('review',['accept','revise'])
 f.readDraft.mockReturnValue('看这张截图修改')
 f.hasAttachments.mockReturnValue(true)
 fireEvent.click(await screen.findByRole('button',{name:'继续改'}))
 await act(async()=>{await Promise.resolve()})
 expect(f.cardAction).not.toHaveBeenCalled()
 expect(f.clearDraft).not.toHaveBeenCalled()
 expect(screen.getByText(/截图|附件/)).toBeTruthy()
})

it.each([true,false])('停下卡只按后台允许的按钮显示接受问题（允许=%s），隐藏旧称',async allowed=>{
 const actions=['resume','revise','discard',...(allowed?['accept_issues']:[])]
 const f=setup('blocked',actions,'需Claude介入：复查未通过')
 expect(await screen.findByText('停下了 · 需要你决定',{exact:true})).toBeTruthy()
 expect(document.body.textContent).not.toContain('Claude')
 const accept=screen.queryByRole('button',{name:'接受剩下的问题，去验收'})
 if(allowed){expect(accept).toBeTruthy();fireEvent.click(accept!);await act(async()=>{await Promise.resolve()});expect(f.cardAction).toHaveBeenCalledWith('CARD-011','accept_issues','')}
 else expect(accept).toBeNull()
})

it('一张决定卡能收起和展开，收起后仍能看到主按钮',async()=>{
 setup('plan_ready',['approve','revise','discard'])
 await screen.findByText('给清单显示版本')
 fireEvent.click(screen.getByRole('button',{name:'收起'}))
 expect(screen.queryByText('给清单显示版本')).toBeNull()
 expect(screen.getByRole('button',{name:'批准，开工'})).toBeTruthy()
 fireEvent.click(screen.getByRole('button',{name:'展开'}))
 expect(screen.getByText('给清单显示版本')).toBeTruthy()
})
