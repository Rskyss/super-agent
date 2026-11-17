import {mkdtempSync,mkdirSync,writeFileSync,rmSync} from 'node:fs'
import {tmpdir} from 'node:os'
import {join} from 'node:path'
import {afterEach,expect,it} from 'vitest'
import Gateway from '../src/index.ts'
const modules=import.meta.glob('../src/workflow-info.ts',{eager:true}) as Record<string,Record<string,any>>
const dirs:string[]=[]
afterEach(()=>dirs.splice(0).forEach(p=>rmSync(p,{recursive:true,force:true})))
function temp(){const p=mkdtempSync(join(tmpdir(),'sa-align-meta-'));dirs.push(p);return p}
it('03b刷新后仍从真实需求和首条需求流转恢复，updated不是建卡时间',async()=>{
 const p=temp(),c=join(p,'.collab/cards/CARD-022');mkdirSync(c,{recursive:true})
 writeFileSync(join(c,'需求卡.md'),'# 加问候\n\n## 要做什么\n增加问候页面\n\n## 怎么算做完\n① 有问候\n② 旧测试通过\n\n## 不做什么\n不联网\n')
 writeFileSync(join(c,'流转.jsonl'),JSON.stringify({kind:'需求',time:'2026-11-16T09:18:00',src:'你',dst:'调度员'})+'\n'+JSON.stringify({kind:'方案',time:'2026-11-16T10:20:00'})+'\n')
 const g=Object.create(Gateway.prototype) as any;g.config={project:p}
 g.get=async()=>[{id:'CARD-022',title:'加问候',state:'待确认',updated:'2026-11-16T11:00:00',phase:{code:'plan_ready',needs_you:true}}]
 const row=(await g.cards())[0]
 expect(row.createdAt).toBe('2026-11-16T09:18:00')
 expect(row.requirement).toMatchObject({do:'增加问候页面',notDo:'不联网'})
 expect(row.requirement.doneWhen).toContain('旧测试通过')
})
it('确认卡流程复查上限读实际后台常量，未有版本就不编造第4版',()=>{
 const read=modules['../src/workflow-info.ts']?.readCurrentWorkFlow
 expect(read).toBeTypeOf('function')
 const p=temp();mkdirSync(join(p,'collab'));writeFileSync(join(p,'collab/dispatch.py'),'MAX_REVIEW_RETURNS = 4\n')
 const info=read(p)
 expect(info.name).toBe('开发软件');expect(info.steps).toEqual(['写测试','写代码','跑全部测试','复查','你验收'])
 expect(info.maxReviewRounds).toBe(5)
 expect(info.version??null).toBeNull()
})
it('03c建卡后暂缓写方案明确等你接调度员，软件计划来源可恢复真实流程名',async()=>{
 const p=temp(),c=join(p,'.collab/cards/CARD-001');mkdirSync(c,{recursive:true})
 writeFileSync(join(c,'来源.json'),JSON.stringify({conversation:'s',request_id:'request',deferred_plan:true}))
 writeFileSync(join(c,'需求卡.md'),'# 加问候\n\n## 要做什么\n增加问候\n')
 const g=Object.create(Gateway.prototype) as any;g.config={project:p}
 g.get=async()=>[{id:'CARD-001',title:'加问候',state:'待对齐',phase:{code:'plan_waiting',label:'等待写方案',tone:'run',needs_you:false,actions:[]}}]
 const row=(await g.cards())[0]
 expect(row.needsYou).toBe(true)
 expect(row.phaseLabel).toContain('接调度员')
 expect(row.isRunningNow).toBe(false)
 expect(row.flow).toBe('开发软件')
})
it.each(['Codex','agy'])('03b调度员身份从该卡真实写方案流转恢复（%s），不依赖当前连接检测缓存',async dispatcher=>{
 const p=temp(),c=join(p,'.collab/cards/CARD-022');mkdirSync(c,{recursive:true})
 writeFileSync(join(c,'流转.jsonl'),[{kind:'写方案',src:'程序',dst:dispatcher,time:'2026-11-16T09:18:00'},{kind:'方案',src:dispatcher,dst:'你',time:'2026-11-16T09:18:10'}].map(e=>JSON.stringify(e)).join('\n')+'\n')
 const g=Object.create(Gateway.prototype) as any;g.config={project:p};g.get=async()=>[{id:'CARD-022',title:'加问候',state:'待对齐',phase:{code:'plan_ready',needs_you:true}}]
 expect((await g.cards())[0].dispatcher).toBe(dispatcher)
})
