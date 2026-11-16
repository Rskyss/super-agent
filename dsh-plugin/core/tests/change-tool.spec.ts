import { expect, it, vi } from 'vitest'
import { apply } from '../src/task-tools.ts'

it.each(['done','dropped'])('结束的卡（%s）不能出改要求确认条', async phaseCode => {
  const registered: any[]=[]
  const requestChange=vi.fn()
  const gateway={projectPath:'/tmp/no-project',cards:async()=>[{id:'CARD-001',phaseCode}],view:async()=>JSON.stringify({card:{phase:{code:phaseCode}}}),requestChange}
  apply({tools:{register:(tool:unknown)=>registered.push(tool)},superagentGateway:gateway} as never)
  const tool=registered.find(t=>t.name==='card_change')
  const result=await tool.execute({card:'CARD-001',summary:'第1条：旧 → 新',text:'新'}, {agent:{id:'task-CARD-001'}})
  expect(String(result)).toContain('这张卡已结束，要改请新建一张')
  expect(requestChange).not.toHaveBeenCalled()
})
