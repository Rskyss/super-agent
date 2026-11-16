// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { TaskDockBridge } from '../src/client/TaskDockBridge.tsx'
afterEach(cleanup)
it.each([
 {confirm:false,draft:'第二条改双击',clear:false},
 {confirm:true,draft:'第二条改双击',clear:true},
 {confirm:true,draft:'用户又补了新文字',clear:false},
])('确认=$confirm 草稿=$draft 只清实际已提交的旧草稿',async({confirm,draft,clear})=>{
 let pending:any={id:'rev-1',card:'CARD-017',summary:'第2条：单击 → 双击',text:'第二条改双击',sourceDraft:'第二条改双击',createdAt:1,criteriaBase:['标题黑色','单击保存'],criteriaChanges:[{replaceNth:2,text:'双击保存'}]}
 const ok=(value:unknown)=>({ok:true as const,value})
 const inputActions={setDraft:vi.fn(),submit:vi.fn()}
 const face:any={sessionId:'task-test',useInput:(select:any)=>select({draft,attachmentIds:[],phase:'plain'}),inputActions,
 cardOfSession:async()=>ok({id:'CARD-017'}),pendingChange:async()=>ok(pending),decideChange:vi.fn(async()=>{pending=null;return ok({ok:true,message:''})}),view:async()=>ok(JSON.stringify({card:{phase:{code:'review',actions:['revise']}}})),plan:async()=>ok('{}'),cardAction:vi.fn(),preview:vi.fn(),openPreview:vi.fn(),openProgress:vi.fn()}
 render(<TaskDockBridge {...face}/>);
 fireEvent.click(await screen.findByRole('button',{name:confirm?'确认改':'算了'}))
 await waitFor(()=>expect(face.decideChange).toHaveBeenCalledWith('CARD-017','rev-1',confirm))
 await waitFor(()=>expect(screen.queryByRole('button',{name:'确认改'})).toBeNull())
 if(clear)expect(inputActions.setDraft).toHaveBeenCalledWith('')
 else expect(inputActions.setDraft).not.toHaveBeenCalled()
 expect(face.cardAction).not.toHaveBeenCalled()
})
