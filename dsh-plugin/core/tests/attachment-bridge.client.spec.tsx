// @vitest-environment jsdom
import { act,cleanup,fireEvent,render,screen,waitFor } from '@testing-library/react'
import { afterEach,expect,it,vi } from 'vitest'
import { TaskDockBridge } from '../src/client/TaskDockBridge.tsx'
afterEach(cleanup)
const picture={mediaType:'image/png',data:'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII='}
const ok=(value:unknown)=>({ok:true as const,value})
function setup(fallback=false){
 let current={draft:'照截图把第二条改成蓝色',attachmentIds:['img-1'],phase:'plain'},pending:any=null
 const inputActions={setDraft:vi.fn(),submit:vi.fn(),removeAttachment:vi.fn()}
 const face:any={sessionId:'task-photo',useInput:(select:any)=>select(current),inputActions,cardOfSession:async()=>ok({id:'CARD-019'}),view:async()=>ok(JSON.stringify({card:{id:'CARD-019',title:'截图测试',phase:{code:'review',actions:['revise']}},accept:{criteria:['原标准']}})),plan:async()=>ok('{}'),pendingChange:async()=>ok(pending),decideChange:vi.fn(async()=>{pending=null;return ok({ok:true,message:''})}),cardAction:vi.fn(),preview:vi.fn(),openPreview:vi.fn(),openProgress:vi.fn(),readRevisionAttachments:vi.fn(async()=>[picture]),releaseRevisionAttachments:vi.fn(),prepareRevision:vi.fn(async(card:string,text:string,sourceDraft:string,attachments:any[],sourceAttachmentIds:string[])=>{
  if(!fallback)pending={id:'image-change',card,summary:'第2条：原色 → 蓝色',text,sourceDraft,sourceAttachmentIds,attachmentCount:attachments?.length,createdAt:1}
  return ok({ok:true,pending:!fallback,message:''})
 })}
 const r=render(<TaskDockBridge {...face}/>);
 return {face,inputActions,unmount:()=>r.unmount(),changeDraft:()=>{current={draft:'我还在补充下一条要求',attachmentIds:['img-1','img-2'],phase:'plain'};r.rerender(<TaskDockBridge {...face}/>);}}
}
it.each([false,true])('带截图先出确认；确认=$confirm 不提前清草稿或落卡',async confirm=>{
 const f=setup();fireEvent.click(await screen.findByRole('button',{name:'继续改'}))
 await waitFor(()=>expect(f.face.prepareRevision).toHaveBeenCalledWith('CARD-019','照截图把第二条改成蓝色','照截图把第二条改成蓝色',[picture],['img-1']))
 expect(f.inputActions.setDraft).not.toHaveBeenCalled();expect(f.inputActions.removeAttachment).not.toHaveBeenCalled()
 fireEvent.click(await screen.findByRole('button',{name:confirm?'确认改':'算了'}))
 await waitFor(()=>expect(f.face.decideChange).toHaveBeenCalledWith('CARD-019','image-change',confirm))
 if(confirm){await waitFor(()=>expect(f.inputActions.setDraft).toHaveBeenCalledWith(''));expect(f.inputActions.removeAttachment).toHaveBeenCalledWith('img-1');expect(f.face.releaseRevisionAttachments).toHaveBeenCalledWith(['img-1'])}
 else{expect(f.inputActions.setDraft).not.toHaveBeenCalled();expect(f.inputActions.removeAttachment).not.toHaveBeenCalled();expect(f.face.releaseRevisionAttachments).not.toHaveBeenCalled()}
 expect(f.face.cardAction).not.toHaveBeenCalled()
})
it('带图的合法空拆分交出成功后清掉相同的原话与图',async()=>{
 const f=setup(true);fireEvent.click(await screen.findByRole('button',{name:'继续改'}))
 await waitFor(()=>expect(f.inputActions.setDraft).toHaveBeenCalledWith(''))
 expect(f.face.prepareRevision).toHaveBeenCalledWith('CARD-019','照截图把第二条改成蓝色','照截图把第二条改成蓝色',[picture],['img-1'])
 expect(f.inputActions.removeAttachment).toHaveBeenCalledWith('img-1')
})
it('读原图失败不降级成文字，原话和图片都保留',async()=>{
 const f=setup();f.face.readRevisionAttachments.mockRejectedValue(new Error('截图没读到，请重新选择'))
 fireEvent.click(await screen.findByRole('button',{name:'继续改'}))
 expect(await screen.findByText(/截图没读到/)).toBeTruthy()
 expect(f.face.prepareRevision).not.toHaveBeenCalled();expect(f.inputActions.setDraft).not.toHaveBeenCalled();expect(f.inputActions.removeAttachment).not.toHaveBeenCalled()
})
it('确认期间又加了文字或新图，成功后不清掉新草稿',async()=>{
 const f=setup();fireEvent.click(await screen.findByRole('button',{name:'继续改'}));await screen.findByRole('button',{name:'确认改'})
 f.changeDraft();fireEvent.click(screen.getByRole('button',{name:'确认改'}))
 await waitFor(()=>expect(f.face.decideChange).toHaveBeenCalled())
 expect(f.inputActions.setDraft).not.toHaveBeenCalled();expect(f.inputActions.removeAttachment).not.toHaveBeenCalled();expect(f.face.releaseRevisionAttachments).not.toHaveBeenCalled()
})
it('切走会话后迟到的空拆分成功回包不清旧组件持有的草稿和图片',async()=>{
 const f=setup(true);let finish:((r:unknown)=>void)|undefined
 f.face.prepareRevision.mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve}))
 fireEvent.click(await screen.findByRole('button',{name:'继续改'}));await waitFor(()=>expect(f.face.prepareRevision).toHaveBeenCalled())
 f.unmount()
 await act(async()=>{finish?.(ok({ok:true,pending:false,message:''}));await Promise.resolve();await Promise.resolve()})
 expect(f.inputActions.setDraft).not.toHaveBeenCalled();expect(f.inputActions.removeAttachment).not.toHaveBeenCalled();expect(f.face.releaseRevisionAttachments).not.toHaveBeenCalled()
})
