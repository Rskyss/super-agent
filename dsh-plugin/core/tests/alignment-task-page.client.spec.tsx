// @vitest-environment jsdom
import {cleanup,fireEvent,render,screen} from '@testing-library/react'
import {afterEach,expect,it,vi} from 'vitest'
const modules=import.meta.glob('../src/client/TaskPageChrome.tsx',{eager:true}) as Record<string,Record<string,any>>
afterEach(cleanup)
function api(name:string){const f=modules['../src/client/TaskPageChrome.tsx']?.[name];expect(f,`缺少任务页公共呈现 ${name}`).toBeTypeOf('function');return f}
it('任务页工厂真正退出hero且保留settling，普通对话输入和原对象不受影响',()=>{
 const transform=api('taskConversationContentInput')
 const original={variant:'main',phase:'hero',hero:true}
 expect(transform(original,true)).toEqual({variant:'main',phase:'active',hero:false})
 expect(original.hero).toBe(true)
 expect(transform(original,false)).toEqual(original)
 expect(transform({variant:'main',phase:'settling',hero:true},true)).toEqual({variant:'main',phase:'settling',hero:false})
})
it('任务标题含真实状态、仅对话轨迹两个标签，切换交给原selectView并保留后台任务座位',()=>{
 const Header=api('TaskPageHeader'),selectView=vi.fn()
 const task={id:'CARD-022',title:'加问候',phaseCode:'review',phaseLabel:'等你验收',needsYou:true,state:'待验收',actions:['accept','revise'],updated:'',isRunningNow:false}
 render(<Header task={task} dispatcherLabel="Codex" view={null} selectView={selectView} jobs={<button>1 个后台任务 · 预览</button>}/> )
 expect(screen.getByText('22 号：加问候')).toBeTruthy()
 expect(screen.getByText(/调度员/)).toBeTruthy();expect(screen.getByText(/等你验收/)).toBeTruthy()
 expect(screen.getByRole('tab',{name:'对话'}).getAttribute('aria-selected')).toBe('true')
 fireEvent.click(screen.getByRole('tab',{name:'轨迹'}))
 expect(selectView).toHaveBeenCalledWith('superagent-trajectory')
 expect(screen.getAllByRole('tab')).toHaveLength(2)
 expect(screen.getByRole('button',{name:'1 个后台任务 · 预览'})).toBeTruthy()
 expect(screen.queryByRole('button',{name:'任务轨迹'})).toBeNull()
})
it('超级玩家字数只数已加载助手正文和当前partial，排除用户、推理和工具且Unicode字符不拆半',()=>{
 const count=api('countAssistantTextCharacters')
 const snapshot={legacy:{nodes:[{kind:'user',text:'不计用户'},{kind:'assistant',blocks:[{kind:'text',text:'你好'},{kind:'reasoning',text:'不计推理'},{kind:'tool-call',text:'不计工具'}]}],partial:{blocks:[{kind:'text',text:'Codex🙂'}]}}}
 expect(count(snapshot)).toBe(8)
 expect(count({legacy:{nodes:[],partial:null}})).toBe(0)
})
