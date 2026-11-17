// 第七批 7-4：右栏“你批方案”第二行写“你 · HH:MM 批准”（对照 04 / 05），时间读流转记录里真实的批准时刻。
import {expect,it} from 'vitest'
import {parseProgressView} from '../src/progress-model.ts'

const view=(extra:Record<string,unknown>={})=>JSON.stringify({card:{id:'CARD-001',title:'t',state:'开发中',phase:{code:'running',label:'进行中'}},stations:[],...extra})

it('批准过且有时间：第二行写“你 · HH:MM 批准”，不再另起一行“已批准”',()=>{
  const step=parseProgressView(view({approval:{time:'09:15'}}),'CARD-001').steps.find(s=>s.key==='approve')!
  expect(step.state).toBe('done')
  expect([step.actor,step.model].filter(Boolean).join(' · ')).toBe('你 · 09:15 批准')
  expect(step.summary).toBe('')
})

it('没有批准时间（旧卡）：照旧写“已批准”，不编时间',()=>{
  const step=parseProgressView(view(),'CARD-001').steps.find(s=>s.key==='approve')!
  expect(step.model).toBe('')
  expect(step.summary).toBe('已批准')
})

it('还没批准时不显示批准时间',()=>{
  const raw=JSON.stringify({card:{id:'CARD-001',title:'t',state:'待对齐',phase:{code:'plan_ready',label:'等你批准方案'}},stations:[],approval:{time:'09:15'}})
  const step=parseProgressView(raw,'CARD-001').steps.find(s=>s.key==='approve')!
  expect(step.state).toBe('wait')
  expect(step.model).toBe('')
})
