import { afterEach, expect, it, vi } from 'vitest'
import { prepareRevisionOnHost } from '../src/revision-host.ts'
import { parseRevisionModelOutput } from '../src/revision.ts'
afterEach(()=>vi.useRealTimers())
function setup(output:string) {
 const host:any={projectPath:'/tmp/superagent-no-real-project',isSubmitting:()=>false,view:vi.fn(async()=>JSON.stringify({card:{state:'待验收',phase:{code:'review',actions:['revise']}},accept:{criteria:['标题黑色','单击保存']}})),cardAction:vi.fn(async()=>({ok:true,message:''})),requestChange:vi.fn()}
 const stream=vi.fn(async function*(_args:unknown){yield {type:'text-delta',index:0,text:output}})
 const ctx:any={agents:{get:vi.fn(()=>({model:{current:()=>({provider:'task-provider',model:'task-model'})}}))},llm:{stream}}
 return {host,ctx,stream,run:()=>prepareRevisionOnHost(host,ctx,{card:'CARD-017',sessionId:'task-CARD-017',text:'第二条改成双击，新增保存提示'})}
}
it('调用任务会话当前模型拆出两条；只出确认条，原话不改写',async()=>{
 const f=setup('{"changes":[{"replaceNth":2,"text":"双击保存"},{"add":"保存成功提示"}]}')
 expect(await f.run()).toMatchObject({ok:true,pending:true})
 expect(f.stream).toHaveBeenCalledWith(expect.objectContaining({provider:'task-provider',model:'task-model',signal:expect.any(AbortSignal)}))
 expect(f.host.requestChange).toHaveBeenCalledWith('CARD-017',expect.stringContaining('单击保存 → 双击保存'),'第二条改成双击，新增保存提示',expect.objectContaining({criteriaBase:['标题黑色','单击保存'],criteriaChanges:[{replaceNth:2,text:'双击保存'},{add:'保存成功提示'}]}))
 expect(f.host.cardAction).not.toHaveBeenCalled()
})
it('模型明确拆不出时保留原标准，沿原追加路径',async()=>{
 const f=setup('{"changes":[]}');expect(await f.run()).toMatchObject({ok:true,pending:false})
 expect(f.host.cardAction).toHaveBeenCalledWith('CARD-017','revise','第二条改成双击，新增保存提示')
 expect(f.host.requestChange).not.toHaveBeenCalled()
})
it.each(['不是JSON','{"changes":[{"replaceNth":99,"text":"越界"}]}','{"changes":[{"deleteNth":1}]}'])('模型坏输出不得改卡：%s',async output=>{
 const f=setup(output);expect(await f.run()).toMatchObject({ok:false,pending:false})
 expect(f.host.cardAction).not.toHaveBeenCalled();expect(f.host.requestChange).not.toHaveBeenCalled()
 expect(parseRevisionModelOutput(output,['标题黑色','单击保存']).ok).toBe(false)
})
it('即使流不响应abort，也要在60秒返回超时并不写回',async()=>{
 vi.useFakeTimers();const f=setup('');f.stream.mockImplementation(async function*(){await new Promise(()=>{});yield {} as never})
 let result:any;void f.run().then(r=>{result=r})
 await vi.advanceTimersByTimeAsync(60_100)
 expect(result).toMatchObject({ok:false,pending:false,message:expect.stringContaining('超时')})
 expect(f.host.cardAction).not.toHaveBeenCalled();expect(f.host.requestChange).not.toHaveBeenCalled()
})
it('模型拆出逐条修改时图片随确认保存，不能在分析后丢掉原图',async()=>{
 const f=setup('{"changes":[{"replaceNth":2,"text":"双击保存"}]}')
 const attachments=[{mediaType:'image/png',data:'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII='}]
 const r=await prepareRevisionOnHost(f.host,f.ctx,{card:'CARD-017',sessionId:'task-CARD-017',text:'按图改第二条',attachments,sourceAttachmentIds:['img-1']} as any)
 expect(r).toMatchObject({ok:true,pending:true})
 expect(f.host.requestChange).toHaveBeenCalledWith('CARD-017',expect.any(String),'按图改第二条',expect.objectContaining({attachments,sourceAttachmentIds:['img-1']}))
 expect(f.host.cardAction).not.toHaveBeenCalled()
})
it('模型空拆分的原追加路径也必须转交图片',async()=>{
 const f=setup('{"changes":[]}')
 const attachments=[{mediaType:'image/png',data:'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII='}]
 const r=await prepareRevisionOnHost(f.host,f.ctx,{card:'CARD-017',sessionId:'task-CARD-017',text:'见图，点了没反应',attachments} as any)
 expect(r).toMatchObject({ok:true,pending:false})
 expect(f.host.cardAction).toHaveBeenCalledWith('CARD-017','revise','见图，点了没反应',expect.objectContaining({attachments}))
})
