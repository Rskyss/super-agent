import { afterEach, expect, it, vi } from 'vitest'
import { mkdtempSync,mkdirSync,writeFileSync,symlinkSync,rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import Gateway from '../src/index.ts'
const png='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII='
const picture={mediaType:'image/png',data:png}
const dirs:string[]=[]
afterEach(()=>dirs.splice(0).forEach(p=>rmSync(p,{recursive:true,force:true})))
function gateway(){const g:any=Object.create(Gateway.prototype);g.changes=new Map();g.submitting=new Set();g.view=async()=>JSON.stringify({card:{phase:{code:'review'}}});g.cardAction=vi.fn(async()=>({ok:true,message:''}));return g}
function files(){const project=mkdtempSync(join(tmpdir(),'sa-attachment-'));dirs.push(project);const card=join(project,'.collab/cards/CARD-001');mkdirSync(join(card,'附件'),{recursive:true});const name='202611161030000001.png';writeFileSync(join(card,'附件',name),Buffer.from(png,'base64'));return{project,card,name}}
it('图在确认前只存内存，公开确认条只回数量，确认后完整原图交给revise',async()=>{
 const g=gateway();const p=g.requestChange('CARD-001','第2条改色','原话',{attachments:[picture],sourceDraft:'原话',sourceAttachmentIds:['img-1']})
 expect(p.attachmentCount).toBe(1);expect(JSON.stringify(p)).not.toContain(png)
 expect(p.sourceAttachmentIds).toEqual(['img-1']);expect(g.cardAction).not.toHaveBeenCalled()
 expect((await g.decideChange('CARD-001',p.id,true)).ok).toBe(true)
 expect(g.cardAction).toHaveBeenCalledWith('CARD-001','revise','原话',expect.objectContaining({attachments:[picture]}))
})
it('算了只撤内存图与确认条，不调用卡片写接口',async()=>{
 const g=gateway();const p=g.requestChange('CARD-001','改','原话',{attachments:[picture]})
 await g.decideChange('CARD-001',p.id,false)
 expect(g.cardAction).not.toHaveBeenCalled();expect(await g.pendingChange('CARD-001')).toBeNull()
})
it('框架不同作用域代理共享同一待确认条时，原图不能因this身份不同而丢失',async()=>{
 const g=gateway(),prepareScope=new Proxy(g,{}),confirmScope=new Proxy(g,{})
 const p=prepareScope.requestChange('CARD-001','按图改','原话',{attachments:[picture]})
 expect((await confirmScope.pendingChange('CARD-001')).attachmentCount).toBe(1)
 await confirmScope.decideChange('CARD-001',p.id,true)
 expect(g.cardAction).toHaveBeenCalledWith('CARD-001','revise','原话',expect.objectContaining({attachments:[picture]}))
})
it('确认条说有图片但私有原图不可用时拒绝提交，不能降级只发文字',async()=>{
 const g=gateway();g.changes.set('CARD-001',{id:'missing-image',card:'CARD-001',summary:'看图改',text:'原话',attachmentCount:1,createdAt:1})
 const r=await g.decideChange('CARD-001','missing-image',true)
 expect(r.ok).toBe(false);expect(r.message).toMatch(/截图|图片/)
 expect(g.cardAction).not.toHaveBeenCalled();expect(await g.pendingChange('CARD-001')).not.toBeNull()
})
it('带图的正式修改请求不能只转交文字',async()=>{
 const g=gateway();g.cardAction=Gateway.prototype.cardAction;g.post=vi.fn(async()=>({ok:true}));g.cleanupCardPreviewJob=vi.fn()
 await g.cardAction('CARD-001','revise','看图修改',{attachments:[picture]})
 expect(g.post).toHaveBeenCalledWith('/api/clerk/card',expect.objectContaining({attachments:[picture]}))
})
it('轨迹追加要求保留可打开图片的安全名字，不把图片内容塞进轨迹',async()=>{
 const f=files();writeFileSync(join(f.card,'流转.jsonl'),JSON.stringify({kind:'追加要求',summary:'看图修改',attachments:['附件/'+f.name]})+'\n')
 const g:any=Object.create(Gateway.prototype);g.config={project:f.project}
 const t=await g.trajectory('CARD-001')
 expect(t.rows[0].attachments).toEqual([expect.objectContaining({name:f.name})])
 expect(JSON.stringify(t)).not.toContain(png)
})
it('查看卡内原图返回同一份字节，不能读软链或跨目录',async()=>{
 const f=files();const g:any=Object.create(Gateway.prototype);g.config={project:f.project}
 expect(g.cardAttachment,'缺少鉴权Remote里的卡片原图读取').toBeTypeOf('function')
 expect(await g.cardAttachment('CARD-001',f.name)).toEqual({mediaType:'image/png',data:png})
 const outside=join(f.project,'outside.png');writeFileSync(outside,Buffer.from(png,'base64'))
 symlinkSync(outside,join(f.card,'附件','202611161030000002.png'))
 for(const name of ['../outside.png','202611161030000002.png','https://example.com/a.png'])await expect(g.cardAttachment('CARD-001',name)).rejects.toThrow()
})
it('验收卡保留追加原话，但不把程序生成的图片Markdown路径当成用户文字展示',async()=>{
 const f=files();writeFileSync(join(f.card,'需求卡.md'),'# 截图任务\n## 怎么算做完\n1. 按图修改\n\n## 追加要求\n看这张图修改按钮\n\n![截图](附件/'+f.name+')\n')
 writeFileSync(join(f.card,'流转.jsonl'),JSON.stringify({kind:'追加要求',text:'看这张图修改按钮',attachments:['附件/'+f.name]})+'\n')
 const g:any=Object.create(Gateway.prototype);g.config={project:f.project};g.get=async()=>({card:{id:'CARD-001',state:'待验收',phase:{code:'review'}},accept:{}})
 const view=JSON.parse(await g.view('CARD-001'))
 expect(view.accept.changeNotes).toContain('看这张图修改按钮')
 expect(view.accept.changeNotes.join('\n')).not.toContain('![截图]')
 expect(view.accept.changeNotes.join('\n')).not.toContain('附件/'+f.name)
})
