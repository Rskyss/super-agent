// 遗留修复：卡在等接调度员时，超级玩家回答进度不能说“已经开工 / 在写方案”。
// 病根：左栏、总览用“来源.json 里 deferred_plan”判断在等接调度员，但回答进度读的是另一份状态，只写着写方案。
import {mkdtempSync,mkdirSync,rmSync,writeFileSync} from 'node:fs'
import {tmpdir} from 'node:os'
import {join} from 'node:path'
import {afterEach,expect,it} from 'vitest'
import {cardWaitsForDispatcher} from '../src/card-wait.ts'
import {describeView} from '../src/task-tools.ts'

const dirs:string[]=[]
afterEach(()=>{for(const d of dirs.splice(0))rmSync(d,{recursive:true,force:true})})
function project(deferred:boolean){
  const dir=mkdtempSync(join(tmpdir(),'sa-wait-disp-'));dirs.push(dir)
  mkdirSync(join(dir,'.collab','cards','CARD-001'),{recursive:true})
  writeFileSync(join(dir,'.collab','cards','CARD-001','来源.json'),JSON.stringify({deferred_plan:deferred}))
  return dir
}

it('只有标了暂缓、还在写方案前的卡才算在等接调度员',()=>{
  const yes=project(true),no=project(false)
  expect(cardWaitsForDispatcher(yes,'CARD-001','待对齐','plan_waiting','wait')).toBe(true)
  expect(cardWaitsForDispatcher(yes,'CARD-001','待对齐','planning','run')).toBe(true)
  expect(cardWaitsForDispatcher(no,'CARD-001','待对齐','plan_waiting','wait')).toBe(false)
  expect(cardWaitsForDispatcher(yes,'CARD-001','已放弃','plan_waiting','wait')).toBe(false)
  expect(cardWaitsForDispatcher(yes,'CARD-001','待对齐','plan_ready','wait')).toBe(false)
  expect(cardWaitsForDispatcher(yes,'../x','待对齐','plan_waiting','wait')).toBe(false)
})

it('回答进度：在等接调度员时写明还没开工，不说在写方案',()=>{
  const raw=JSON.stringify({card:{id:'CARD-001',title:'加问候',state:'待对齐',phase:{code:'plan_waiting',label:'调度员写方案中'}},stations:[]})
  const text=describeView(raw,'{}',{waitingDispatcher:true})
  expect(text).toContain('现在：等你处理 · 接调度员')
  expect(text).toMatch(/还没开工/)
  expect(text).not.toContain('调度员写方案中')
  // 不在等时保持原样
  expect(describeView(raw,'{}')).toContain('调度员写方案中')
})
