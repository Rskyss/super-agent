import { expect, it, vi } from 'vitest'
import Gateway from '../src/index.ts'
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

function setup() {
 const g = Object.create(Gateway.prototype) as any
 g.changes = new Map(); g.submitting = new Set()
 g.view = vi.fn(async()=>JSON.stringify({card:{state:'待验收',phase:{code:'review',actions:['revise']}},accept:{criteria:['标题黑色','点击可以保存']}}))
 g.cardAction = vi.fn(async()=>({ok:true,message:''}))
 return g
}
it('两条逐条修改必须原样经确认路径交给后台，确认之前没有写回',async()=>{
 const g=setup(), options={criteriaBase:['标题黑色','点击可以保存'],criteriaChanges:[{replaceNth:2,text:'双击可以保存'},{add:'保存成功显示提示'}]}
 const c=g.requestChange('CARD-001','第2条：点击 → 双击；新增第3条：成功提示','原始输入',options)
 expect(c.criteriaChanges).toEqual(options.criteriaChanges)
 expect(g.cardAction).not.toHaveBeenCalled()
 expect((await g.decideChange('CARD-001',c.id,true)).ok).toBe(true)
 expect(g.cardAction).toHaveBeenCalledWith('CARD-001','revise','原始输入',expect.objectContaining(options))
 expect(await g.pendingChange('CARD-001')).toBeNull()
})
it('算了只撤确认条，不调任何卡片写接口',async()=>{
 const g=setup(),c=g.requestChange('CARD-001','改第2条','原始输入',{criteriaBase:['标题黑色','点击可以保存'],criteriaChanges:[{replaceNth:2,text:'双击可以保存'}]})
 expect((await g.decideChange('CARD-001',c.id,false)).ok).toBe(true)
 expect(g.cardAction).not.toHaveBeenCalled();expect(await g.pendingChange('CARD-001')).toBeNull()
})
it('结构化改动必须经cardAction正式接口送到Python，普通操作保持原参数',async()=>{
 const g=setup();g.cardAction=Gateway.prototype.cardAction;g.post=vi.fn(async()=>({ok:true}));g.cleanupCardPreviewJob=vi.fn()
 const options={criteriaBase:['标题黑色'],criteriaChanges:[{replaceNth:1,text:'蓝色'}]}
 await g.cardAction('CARD-001','revise','原始输入',options)
 expect(g.post).toHaveBeenCalledWith('/api/clerk/card',expect.objectContaining({criteria_base:options.criteriaBase,criteria_changes:options.criteriaChanges}))
})
it('重启后的视图仍从流转记录识别已逐条更新的当前标准',async()=>{
 const project=mkdtempSync(join(tmpdir(),'sa-revision-view-'))
 try {
  const dir=join(project,'.collab','cards','CARD-001');mkdirSync(dir,{recursive:true})
  writeFileSync(join(dir,'需求卡.md'),'# 卡\n## 怎么算做完\n1. 标题黑色\n2. 双击保存\n\n## 追加要求\n第二条改双击\n')
  writeFileSync(join(dir,'流转.jsonl'),JSON.stringify({kind:'追加要求',criteria_changes:[{replaceNth:2,text:'双击保存'}],text:'第二条改双击'})+'\n')
  const g:any=Object.create(Gateway.prototype);g.config={project};g.get=async()=>({card:{id:'CARD-001',state:'待验收',phase:{code:'review'}},accept:{}})
  const v=JSON.parse(await g.view('CARD-001'))
  expect(v.accept.criteriaUpdated).toBe(true)
  expect(v.accept.criteria).toEqual(['标题黑色','双击保存'])
 } finally {rmSync(project,{recursive:true,force:true})}
})
