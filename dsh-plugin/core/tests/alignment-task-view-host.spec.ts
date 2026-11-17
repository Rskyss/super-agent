import {mkdtempSync,mkdirSync,writeFileSync,rmSync} from 'node:fs'
import {tmpdir} from 'node:os'
import {join} from 'node:path'
import {afterEach,expect,it} from 'vitest'
import Gateway from '../src/index.ts'
const dirs:string[]=[]
afterEach(()=>dirs.splice(0).forEach(p=>rmSync(p,{recursive:true,force:true})))
function setup(code='review'){
 const project=mkdtempSync(join(tmpdir(),'sa-task-view-'));dirs.push(project)
 const c=join(project,'.collab/cards/CARD-022');mkdirSync(join(c,'流转'),{recursive:true})
 const g=Object.create(Gateway.prototype) as any;g.config={project}
 g.get=async()=>({card:{id:'CARD-022',title:'加问候',state:code==='review'?'待验收':'需Claude介入',phase:{code,label:'等你',tone:'wait',needs_you:true,actions:[]}},accept:{result:'方案预期：输入名字将显示问候'},stations:[]})
 return {g,c,project}
}
it('05完成摘要优先读最新真实开发汇报，不把旧方案预期冒充结果',async()=>{
 const {g,c}=setup();writeFileSync(join(c,'汇报.md'),'- 2026-11-16 09:20 完成旧内容\n- 2026-11-16 09:21 已实现中文问候。\n')
 const payload=JSON.parse(await g.view('CARD-022'))
 expect(payload.accept.result).toBe('已实现中文问候。')
 expect(payload.isRunningNow).toBe(false)
})
it('14按真实复查回复顺序配后续开发记录，不把相同round当一轮，不读取越界报告',async()=>{
 const {g,c,project}=setup('blocked')
 writeFileSync(join(project,'forbidden.md'),'OUTSIDE_REPORT_MUST_NOT_APPEAR')
 writeFileSync(join(c,'流转/002-回复.md'),'结论：不通过\n问题：会重复操作')
 writeFileSync(join(c,'流转/003-回复.md'),'汇报：在操作前加了检查')
 writeFileSync(join(c,'流转/004-回复.md'),'结论：不通过\n问题：仍可能并发重复')
 const events=[{seq:1,kind:'追加要求',time:'2026-11-16T09:00:00'},
 {seq:2,kind:'回复',src:'复查员',dst:'调度员',step:'复查',round:1,ok:true,text:'流转/002-回复.md',summary:'会重复操作',time:'2026-11-16T09:01:00'},
 {seq:3,kind:'回复',src:'开发员',dst:'调度员',step:'开发',round:1,ok:true,text:'流转/003-回复.md',summary:'在操作前加了检查',time:'2026-11-16T09:02:00'},
 {seq:4,kind:'回复',src:'复查员',dst:'调度员',step:'复查',round:1,ok:true,text:'流转/004-回复.md',summary:'仍可能并发重复',time:'2026-11-16T09:03:00'},
 {seq:5,kind:'回复',src:'复查员',dst:'调度员',step:'复查',round:1,ok:true,text:'../../../forbidden.md',summary:'报告不可读取',time:'2026-11-16T09:04:00'},
 {seq:6,kind:'转人工',src:'调度员',dst:'你',cause:'复查不通过',say:'建议先验收再判断剩余问题',issues:'仍有重复问题',time:'2026-11-16T09:05:00'}]
 writeFileSync(join(c,'流转.jsonl'),events.map(e=>JSON.stringify(e)).join('\n')+'\n')
 const payload=JSON.parse(await g.view('CARD-022'))
 expect(payload.stopReviews.map((r:any)=>r.round)).toEqual([1,2,3])
 expect(payload.stopReviews[0].found).toContain('会重复操作')
 expect(payload.stopReviews[0].changed).toContain('操作前加了检查')
 expect(payload.stopReviews[1].changed).toBe('无修改记录')
 expect(payload.stopReviews[2].found).toContain('无记录')
 expect(payload.stopAdvice).toBe('建议先验收再判断剩余问题')
 expect(JSON.stringify(payload)).not.toContain('OUTSIDE_REPORT_MUST_NOT_APPEAR')
})

it('05缺真实完成汇报时不能把方案预期冒充已完成结果',async()=>{
 const {g}=setup()
 const payload=JSON.parse(await g.view('CARD-022'))
 expect(payload.accept.result).not.toBe('方案预期：输入名字将显示问候')
})
