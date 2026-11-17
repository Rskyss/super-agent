import {mkdtempSync,mkdirSync,writeFileSync,rmSync} from 'node:fs'
import {tmpdir} from 'node:os'
import {join} from 'node:path'
import {afterEach,expect,it} from 'vitest'
import Gateway from '../src/index.ts'
import {readCardTrajectory} from '../src/trajectory.ts'
import {parseProgressView} from '../src/progress-model.ts'
const dirs:string[]=[];afterEach(()=>dirs.splice(0).forEach(p=>rmSync(p,{recursive:true,force:true})))
function setup(code='review'){
 const project=mkdtempSync(join(tmpdir(),'sa-fourth-independent-'));dirs.push(project)
 const c=join(project,'.collab/cards/CARD-022');mkdirSync(join(c,'流转'),{recursive:true})
 const g=Object.create(Gateway.prototype) as any;g.config={project}
 g.get=async()=>({card:{id:'CARD-022',state:code==='review'?'待验收':'需Claude介入',phase:{code,label:'等你',tone:'wait',needs_you:true,actions:[]}},accept:{result:'方案预期'},stations:[]})
 return {project,c,g}
}
it('new acceptance round without a report must not reuse previous round completion claim',async()=>{
 const {c,g}=setup()
 writeFileSync(join(c,'汇报.md'),'- 2026-11-16 09:10 已实现旧版英文问候。\n')
 writeFileSync(join(c,'流转/004.md'),'已处理本次要求，但这一份没有汇报标记。')
 writeFileSync(join(c,'流转.jsonl'),[{seq:1,kind:'开跑',time:'2026-11-16T09:00:00'}, {seq:2,kind:'追加要求',time:'2026-11-16T09:20:00',summary:'改为中文'}, {seq:3,kind:'开跑',time:'2026-11-16T09:21:00'}, {seq:4,kind:'回复',src:'开发员',ok:true,text:'流转/004.md',time:'2026-11-16T09:22:00'}].map(e=>JSON.stringify(e)).join('\n'))
 const result=JSON.parse(await g.view('CARD-022'))
 expect(result.accept.result).not.toContain('旧版英文问候')
})
it('completed planning step must not show later review rejection as the plan result',async()=>{
 const {c,g}=setup('blocked')
 writeFileSync(join(c,'流转.jsonl'),[{seq:1,kind:'写方案',time:'2026-11-16T09:00:00'}, {seq:2,kind:'方案',src:'Codex',summary:'计划补测试并实现中文问候',time:'2026-11-16T09:01:00'}, {seq:3,kind:'批准',src:'你',time:'2026-11-16T09:03:00'}].map(e=>JSON.stringify(e)).join('\n'))
 g.get=async()=>({card:{id:'CARD-022',state:'需Claude介入',phase:{code:'blocked',tone:'wait',needs_you:true,detail:'复查没过'}},stations:[{key:'disp',who:'Codex',state:'wait',detail:'复查员复查不通过（已自动退回2次）'}]})
 const parsed=parseProgressView(await g.view('CARD-022'),'CARD-022')
 expect(parsed.steps.find(s=>s.key==='disp')?.summary).not.toContain('复查不通过')
})
it('05t distribution must include a known approval wait, not only actor runtime',()=>{
 const {project,c}=setup()
 writeFileSync(join(c,'流转.jsonl'),[{seq:1,kind:'方案',src:'调度员',summary:'方案写好',time:'2026-11-16T09:00:00'}, {seq:2,kind:'批准',src:'你',summary:'批准方案',time:'2026-11-16T09:03:20'}, {seq:3,kind:'回复',src:'开发员',seconds:60,time:'2026-11-16T09:04:20'}].map(e=>JSON.stringify(e)).join('\n'))
 const result=readCardTrajectory(project,'CARD-022')
 expect(result.distribution.some(x=>x.actor.includes('等你')&&x.seconds===200)).toBe(true)
})
it('non-review stop reason must not be renamed failed review because earlier review passed',async()=>{
 const {c,g}=setup('blocked')
 writeFileSync(join(c,'流转/001.md'),'结论：通过\n问题：无')
 writeFileSync(join(c,'流转.jsonl'),[{seq:1,kind:'回复',src:'复查员',ok:true,text:'流转/001.md',time:'2026-11-16T09:00:00'}, {seq:2,kind:'转人工',src:'调度员',summary:'有改动越界了且没撤回来',say:'请处理越界问题',time:'2026-11-16T09:01:00'}].map(e=>JSON.stringify(e)).join('\n'))
 g.get=async()=>({card:{id:'CARD-022',state:'需Claude介入',phase:{code:'blocked',tone:'wait',needs_you:true,issues:'有改动越界了且没撤回来',actions:['revise','resume','discard']}}})
 const payload=JSON.parse(await g.view('CARD-022'))
 expect(payload.stopReviews).toEqual([])
 expect(payload.card.phase.issues).toContain('有改动越界了且没撤回来')
})
it('positive control: new round explicit developer report wins over old fallback',async()=>{
 const {c,g}=setup()
 writeFileSync(join(c,'汇报.md'),'- 2026-11-16 09:10 已实现旧版英文问候。\n')
 writeFileSync(join(c,'流转/003.md'),'汇报：已实现本轮中文问候。')
 writeFileSync(join(c,'流转.jsonl'),[{seq:1,kind:'追加要求',time:'2026-11-16T09:20:00'}, {seq:2,kind:'开跑',time:'2026-11-16T09:21:00'}, {seq:3,kind:'回复',src:'开发员',ok:true,text:'流转/003.md',time:'2026-11-16T09:22:00'}].map(e=>JSON.stringify(e)).join('\n'))
 expect(JSON.parse(await g.view('CARD-022')).accept.result).toBe('已实现本轮中文问候。')
})
it('positive control: explicit runtime remains real and unknown usage is not invented',()=>{
 const {project,c}=setup()
 writeFileSync(join(c,'流转.jsonl'),JSON.stringify({seq:1,kind:'回复',src:'开发员',seconds:60,time:'2026-11-16T09:04:20'}))
 const result=readCardTrajectory(project,'CARD-022')
 expect(result.distribution).toEqual([{actor:'开发员',seconds:60}])
 expect(result.rows).toHaveLength(1)
 expect(result.rows[0]!.usage).toBeNull()
})
it('05t阶段分布覆盖有明确信息的写方案、等你、程序测试与各工作步骤',()=>{
 const {project,c}=setup();const events=[
  {kind:'写方案',time:'2026-11-16T09:00:00'},
  {kind:'方案',time:'2026-11-16T09:00:58'},
  {kind:'批准',time:'2026-11-16T09:04:18'},
  {kind:'回复',src:'测试员',step:'写测试',seconds:190,time:'2026-11-16T09:07:28'},
  {kind:'回复',src:'开发员',step:'开发',seconds:400,time:'2026-11-16T09:14:08'},
  {kind:'全量',phase:'开始',time:'2026-11-16T09:14:08'},
  {kind:'全量',phase:'结果',result:'过',time:'2026-11-16T09:16:30'},
  {kind:'回复',src:'复查员',step:'复查',seconds:181,time:'2026-11-16T09:19:31'},
 ];writeFileSync(join(c,'流转.jsonl'),events.map(e=>JSON.stringify(e)).join('\n'))
 const d=readCardTrajectory(project,'CARD-022').distribution
 for(const [name,seconds] of [['写方案',58],['等你',200],['写测试',190],['写代码',400],['复查',181]] as const)expect(d.some(x=>x.actor.includes(name)&&x.seconds===seconds)).toBe(true)
 expect(d.some(x=>/跑.*测试/.test(x.actor)&&x.seconds===142)).toBe(true)
})
it('再跑一次的新开跑边界后复查轮数重算，不串之前停下的复查',async()=>{
 const {c,g}=setup('blocked');for(const n of [1,2,5])writeFileSync(join(c,`流转/${n}.md`),`结论：不通过\n问题：第${n}份报告`)
 const events=[{seq:1,kind:'回复',src:'复查员',ok:true,text:'流转/1.md',time:'2026-11-16T09:00:00'},
 {seq:2,kind:'回复',src:'复查员',ok:true,text:'流转/2.md',time:'2026-11-16T09:01:00'},
 {seq:3,kind:'转人工',cause:'复查不通过',time:'2026-11-16T09:02:00'},
 {seq:4,kind:'开跑',time:'2026-11-16T09:20:00'},
 {seq:5,kind:'回复',src:'复查员',ok:true,text:'流转/5.md',time:'2026-11-16T09:21:00'},
 {seq:6,kind:'转人工',cause:'复查不通过',time:'2026-11-16T09:22:00'}]
 writeFileSync(join(c,'流转.jsonl'),events.map(e=>JSON.stringify(e)).join('\n'))
 const result=JSON.parse(await g.view('CARD-022'));expect(result.stopReviews).toHaveLength(1);expect(result.stopReviews[0].round).toBe(1);expect(result.stopReviews[0].found).toContain('第5份报告')
})
