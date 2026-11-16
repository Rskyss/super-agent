// @vitest-environment jsdom
/// <reference types="vite/client" />
import {cleanup,render,screen} from '@testing-library/react'
import{afterEach,expect,it}from'vitest'
const hosts=import.meta.glob('../src/task-tools.ts',{eager:true}) as Record<string,{describeView:(raw:string,plan:string)=>string}>
const describeView=(raw:string,plan:string)=>hosts['../src/task-tools.ts']!.describeView(raw,plan)
import{ProgressPanel}from'../src/client/ProgressPanel.tsx'
const shared=import.meta.glob('../src/progress-model.ts',{eager:true}) as Record<string,any>
afterEach(cleanup)
const labels=['调度员写方案','你批方案','写测试','写代码','跑全部测试','复查','你验收']
for(const [phase,detail,state] of [['plan_waiting','等待接入调度员后开始写方案','wait'],['plan_waiting','排队等待写方案','wait'],['plan_failed','方案没写成：订阅连接超时','fail']]as const){
 it('共享进度模型诚实解释'+detail,()=>{const parse=shared['../src/progress-model.ts']?.parseProgressView;expect(parse,'主机与界面缺共享进度模型').toBeTypeOf('function');const raw=JSON.stringify({card:{id:'CARD-013',state:'待对齐',phase:{code:phase,label:'等待写方案'}},stations:[{key:'disp',role:'调度',who:'Codex',state,detail}]});const p=parse(raw,'CARD-013');expect(p.steps[0].state).toBe(state);expect(p.steps[0].summary).toBe(detail);expect(describeView(raw,'{}')).toContain(detail)})
}
it('右栏七步与card_status说法一致，包含批准和验收，不漏中间步骤',async()=>{const details=['排队等待写方案','等方案写好','等开工','等测试写完','等代码写完','等测试通过','等前面做完'];const raw=JSON.stringify({card:{id:'CARD-013',title:'进度核对',state:'待对齐',phase:{code:'plan_waiting',label:'等待写方案'}},stations:[{key:'disp',role:'调度',who:'Codex',state:'wait',detail:details[0]},...['test','dev','check','rev','accept'].map((key,i)=>({key,who:'程序',state:'idle',detail:details[i+2]}))]});render(<ProgressPanel card="CARD-013" view={async()=>({ok:true,value:raw})}/>);await screen.findByText('排队等待写方案');const text=describeView(raw,'{}');for(const l of labels)expect(text).toContain(l);for(const d of details){expect(screen.getByText(d,{exact:true})).toBeTruthy();expect(text).toContain(d)}})
