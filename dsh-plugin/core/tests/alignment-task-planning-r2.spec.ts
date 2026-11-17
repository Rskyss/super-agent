import {mkdtempSync,mkdirSync,writeFileSync,rmSync} from 'node:fs'
import {tmpdir} from 'node:os'
import {join} from 'node:path'
import {afterEach,expect,it} from 'vitest'
import Gateway from '../src/index.ts'
import {parseProgressView} from '../src/progress-model.ts'
import {readCardTrajectory} from '../src/trajectory.ts'
const dirs:string[]=[];afterEach(()=>dirs.splice(0).forEach(p=>rmSync(p,{recursive:true,force:true})))
function setup(){const project=mkdtempSync(join(tmpdir(),'sa-fourth-r2-review-'));dirs.push(project);const c=join(project,'.collab/cards/CARD-022');mkdirSync(c,{recursive:true});const g=Object.create(Gateway.prototype) as any;g.config={project};return {project,c,g}}
it('rewriting a plan must use current write instead of previous completed version',async()=>{
 const {c,g}=setup()
 writeFileSync(join(c,'流转.jsonl'),[
 {kind:'写方案',dst:'Codex',model:'old-model',summary:'正在写第一版',time:'2026-11-16T09:00:00'},
 {kind:'方案',src:'Codex',summary:'第一版旧目标',time:'2026-11-16T09:01:00'},
 {kind:'追加要求',summary:'改方案',time:'2026-11-16T09:05:00'},
 {kind:'写方案',dst:'agy',model:'new-model',summary:'正在重写第二版',time:'2026-11-16T09:05:01'},
 ].map(e=>JSON.stringify(e)).join('\n'))
 g.get=async()=>({card:{id:'CARD-022',state:'待对齐',phase:{code:'planning',tone:'wait',needs_you:false}},stations:[{key:'disp',state:'active',who:'agy',model:'new-model',detail:'正在重写第二版'}]})
 const payload=JSON.parse(await g.view('CARD-022'))
 const step=parseProgressView(JSON.stringify(payload),'CARD-022').steps.find(s=>s.key==='disp')!
 expect(step).toMatchObject({summary:'正在重写第二版',actor:'agy',model:'new-model',elapsed:null})
})
it('missing matching timestamps must not invent approval waiting time',()=>{
 const {project,c}=setup()
 writeFileSync(join(c,'流转.jsonl'),[{kind:'批准',time:'2026-11-16T09:03:20'}, {kind:'回复',src:'开发员',step:'开发',seconds:60,time:'2026-11-16T09:04:20'}].map(e=>JSON.stringify(e)).join('\n'))
 const d=readCardTrajectory(project,'CARD-022').distribution
 expect(d).toEqual([{actor:'写代码',seconds:60}])
})
it('第二版完成后改用第二版目标、模型和自己的完成耗时',async()=>{
 const {c,g}=setup();const events=[{kind:'写方案',dst:'Codex',model:'old-model',time:'2026-11-16T09:00:00'},{kind:'方案',src:'Codex',summary:'旧目标',time:'2026-11-16T09:01:00'},{kind:'写方案',dst:'agy',model:'new-model',time:'2026-11-16T09:05:01'},{kind:'方案',src:'agy',summary:'第二版新目标',time:'2026-11-16T09:05:31'}];writeFileSync(join(c,'流转.jsonl'),events.map(e=>JSON.stringify(e)).join('\n'))
 g.get=async()=>({card:{phase:{code:'plan_ready'}},stations:[]})
 const result=JSON.parse(await g.view('CARD-022'));expect(result.planning.detail).toBe('第二版新目标');expect(result.planning.model).toBe('new-model');expect(result.planning.elapsed).toBe(30)
})
