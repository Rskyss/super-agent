// @vitest-environment jsdom
import {expect,it} from 'vitest'
import {buildDecisionView} from '../src/client/decision-model.ts'
const modules=import.meta.glob('../src/client/TaskPageChrome.tsx',{eager:true}) as Record<string,Record<string,any>>
function api(name:string){const f=modules['../src/client/TaskPageChrome.tsx']?.[name];expect(f,`缺少实际使用的任务呈现 ${name}`).toBeTypeOf('function');return f}
const payload=(code:string)=>({card:{id:'CARD-022',phase:{code}}})
it('04a方案写完独立说明带真实问题数，没有问题不捏造一个',()=>{
 const f=api('getTaskPhaseAnnouncement')
 expect(f(payload('plan_ready'),{plan:'## 需要你确认的问题\n问候用中文还是英文？建议中文。'})).toBe('调度员把方案写好了，有 1 个问题要你答，我标了建议。你批了才开工。')
 expect(f(payload('plan_ready'),{plan:'## 需要你确认的问题\n没有'})).toBe('调度员把方案写好了，你批了才开工。')
})
it('写方案中和三轮复查停下有各自独立阶段说明，其他停因不谎称复查失败',()=>{
 const f=api('getTaskPhaseAnnouncement')
 expect(f(payload('planning'))).toBe('调度员正在写方案，好了我叫你。')
 const reviews=[1,2,3].map(round=>({round,found:'问题',changed:'修改'}))
 expect(f({...payload('blocked'),stopReviews:reviews})).toContain('复查 3 轮都没通过，我先停下来了')
 expect(f({card:{phase:{code:'blocked',detail:'有改动越界了且没撤回来'}}})).toContain('有改动越界了且没撤回来')
})
it('任务头部原生后台任务称谓只随真实活跃预览补预览，不改真实数量',()=>{
 const f=api('formatTaskJobsLabel')
 expect(f('1 个后台任务',[{kind:'preview',status:'running'}])).toBe('1 个后台任务 · 预览')
 expect(f('2 个后台任务',[{kind:'preview',status:'stopping'},{kind:'other',status:'running'}])).toBe('2 个后台任务 · 预览')
 expect(f('1 个后台任务',[{kind:'preview',status:'killed'}])).toBe('1 个后台任务')
 expect(f('1 个后台任务',[{kind:'other',status:'running'}])).toBe('1 个后台任务')
})

it('非复查停因即使带旧的通过复查表也不改名、不藏真实原因',()=>{
 const view={card:{phase:{code:'blocked',issues:'有改动越界了且没撤回来',detail:'有改动越界了且没撤回来',actions:['revise','resume','discard']}},stopReviews:[{round:1,found:'结论：通过',changed:'无修改记录'}]}
 const model=buildDecisionView('CARD-022',view,null)
 expect(model?.title).not.toMatch(/复查.*没通过/);expect(model?.kind).toBe('stop')
 if(model?.kind==='stop')expect(model.reason).toContain('有改动越界了且没撤回来')
})
it('14关键进展只留本次三条复查，程序内部记录不挤走独立阶段说明',()=>{
 const select=api('selectTaskKeyHandoffs')
 const reviews=[1,2,3].map(n=>({kind:'回复',from:'复查员 Codex',summary:`第${n}轮不通过`,time:`09:0${n}:00`}))
 const handoffs=[{kind:'需求',summary:'需求'},reviews[0],{kind:'回复',from:'开发员',summary:'返工'},reviews[1],{kind:'全量',summary:'内部副本运行'},reviews[2],{kind:'转人工',summary:'停下原因长记录'}]
 const data={card:{phase:{code:'blocked',actions:['accept_issues','revise']}},stopReviews:[1,2,3].map(round=>({round,found:'问题',changed:'修改'})),handoffs}
 expect(select(data)).toEqual(reviews)
 expect(select({...data,stopReviews:[{round:1,found:'本轮问题',changed:'无修改记录'}]})).toEqual([reviews[2]])
})
it('其他任务阶段默认最多三条最新关键进展，完整轨迹不由此改写',()=>{
 const select=api('selectTaskKeyHandoffs')
 const handoffs=[1,2,3,4,5,6].map(n=>({kind:'回复',from:'开发员',summary:`真实进展${n}`,time:`09:0${n}:00`}))
 expect(select({card:{phase:{code:'running'}},handoffs})).toEqual(handoffs.slice(-3))
 expect(handoffs).toHaveLength(6)
})
it.each(['有改动越界了且没撤回来','同样的报错出现了两次'])('14非复查停下标题按真实原因显示：%s',(cause)=>{
 const model=buildDecisionView('CARD-022',{card:{state:'需Claude介入',phase:{code:'blocked',issues:cause,actions:['revise','resume','discard']}},stopReviews:[]},null)
 expect(model?.title).toContain(cause);expect(model?.title).not.toContain('Claude')
 if(model?.kind==='stop'){expect(model.actions.some(a=>a.action==='accept_issues')).toBe(false);expect(model.reason).toContain(cause)}
})
