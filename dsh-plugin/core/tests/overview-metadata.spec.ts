import {mkdtempSync,mkdirSync,writeFileSync,rmSync} from 'node:fs'
import {tmpdir} from 'node:os'
import {join} from 'node:path'
import {afterEach,expect,it} from 'vitest'
import Gateway from '../src/index.ts'
const dirs:string[]=[]
afterEach(()=>dirs.splice(0).forEach(p=>rmSync(p,{recursive:true,force:true})))
async function metadata(flow:any[],rework=0){const project=mkdtempSync(join(tmpdir(),'sa-metadata-'));dirs.push(project);const c=join(project,'.collab/cards/CARD-001');mkdirSync(c,{recursive:true});writeFileSync(join(c,'状态.json'),JSON.stringify({state:'已合并',rework}));writeFileSync(join(c,'流转.jsonl'),flow.map(x=>JSON.stringify(x)).join('\n'));const g=Object.create(Gateway.prototype) as any;g.config={project};g.get=async()=>[{id:'CARD-001',state:'已合并',rework,phase:{code:'done',tone:'done',needs_you:false}}];return(await g.cards())[0]}
const end={kind:'结束',state:'待验收',time:'2026-11-15T09:01:00'}
const merge={kind:'合并',time:'2026-11-15T09:02:00'}
it('用户第一次验收通过，不因内部测试返工或代码复查两轮被记成验收失败',async()=>{const row=await metadata([{kind:'开工',time:'2026-11-15T09:00:00'},{kind:'回复',src:'复查员',round:1,ok:false,summary:'结论：不通过'},{kind:'回复',src:'复查员',round:2,ok:true,summary:'结论：通过'},end,merge],2);expect(row.firstPass).toBe(true);expect(row.reviewRounds).toBe(2)})
it('首次交付前追加要求不算验收退回，交付后继续改才算',async()=>{const change={kind:'追加要求',src:'你',time:'2026-11-15T09:00:30'};expect((await metadata([change,end,merge])).firstPass).toBe(true);expect((await metadata([end,{...change,time:'2026-11-15T09:01:30'},merge])).firstPass).toBe(false)})
it('总览用时按实际开工到交付的时钟计算，不把并行执行的步骤时长相加',async()=>{const row=await metadata([{kind:'开工',time:'2026-11-15T09:00:00'},{kind:'回复',src:'测试员',seconds:60},{kind:'回复',src:'调度员',seconds:60},end,merge]);expect(row.elapsed).toBe(60)})
it('损坏的完成日期不能当真实完成记录计入统计',async()=>{const row=await metadata([end,{kind:'合并',time:'2026-99-99T99:99:99'}]);expect(row.completedAt).toBeNull();expect(row.firstPass).toBeNull()})

it('右栏进度载荷与总览用同一施工区间，不计等验收的过夜时间',async()=>{
 const project=mkdtempSync(join(tmpdir(),'sa-worktime-'));dirs.push(project)
 const c=join(project,'.collab/cards/CARD-001');mkdirSync(c,{recursive:true})
 writeFileSync(join(c,'状态.json'),JSON.stringify({state:'已合并'}))
 writeFileSync(join(c,'流转.jsonl'),[{kind:'开工',time:'2026-11-15T22:23:50'},{kind:'结束',state:'待验收',time:'2026-11-15T22:27:21'},{kind:'合并',time:'2026-11-16T07:14:34'}].map(x=>JSON.stringify(x)).join('\n'))
 const item={id:'CARD-001',state:'已合并',phase:{code:'done',tone:'done',needs_you:false}}
 const g=Object.create(Gateway.prototype) as any;g.config={project};g.get=async(path:string)=>path==='/api/cards'?[item]:{card:item,elapsed:31843,stations:[]}
 const row=(await g.cards())[0];const view=JSON.parse(await g.view('CARD-001'))
 expect(row.elapsed).toBe(211);expect(view.workElapsed).toBe(row.elapsed)
})
