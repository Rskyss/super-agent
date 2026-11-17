// @vitest-environment jsdom
/// <reference types="vite/client" />
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { ComponentType } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
const modules=import.meta.glob('../src/client/DecisionDock.tsx',{eager:true}) as Record<string,Record<string,ComponentType<any>>>
const ok=(value:unknown)=>({ok:true,value})
afterEach(cleanup)
function setup(code:string, acceptExtra:Record<string,unknown>={}) {
 const C=modules['../src/client/DecisionDock.tsx']!.DecisionDock!
 const f={card:'CARD-015',view:async()=>ok(JSON.stringify({card:{id:'CARD-015',title:'测试卡',phase:{code,label:code,actions:['revise']}},stations:[],accept:{criteria:['标题黑色','点击保存'],...acceptExtra}})),plan:async()=>ok(JSON.stringify({plan:'# 拆解方案\n## 要做什么（一句话）\n测试卡\n',allowed:[],test_command:'true'})),cardAction:vi.fn(async()=>ok({ok:true,message:''})),prepareRevision:vi.fn(async()=>ok({ok:true,pending:true,message:''})),preview:vi.fn(),openPreview:vi.fn(),readDraft:()=> '第二条改成双击保存',clearDraft:vi.fn(),hasAttachments:()=>false}
 render(<C {...f}/>);return f
}
it.each(['review','blocked'])('%s的修改先拆分并出确认，不能直接写回或清掉原话',async code=>{
 const f=setup(code)
 fireEvent.click(await screen.findByRole('button',{name:code==='review'?'继续改':'按我说的改'}))
 await act(async()=>{await Promise.resolve();await Promise.resolve()})
 expect(f.prepareRevision).toHaveBeenCalledWith('CARD-015','第二条改成双击保存')
 expect(f.cardAction).not.toHaveBeenCalled();expect(f.clearDraft).not.toHaveBeenCalled()
})
it('方案批准阶段我要改也先拆分出确认，不能直接重写方案或清草稿',async()=>{
 const f=setup('plan_ready')
 fireEvent.click(await screen.findByRole('button',{name:'我要改'}))
 await act(async()=>{await Promise.resolve();await Promise.resolve()})
 expect(f.prepareRevision).toHaveBeenCalledWith('CARD-015','第二条改成双击保存')
 expect(f.cardAction).not.toHaveBeenCalled()
 expect(f.clearDraft).not.toHaveBeenCalled()
})
it('合法的空拆分完成追加后才清空旧草稿',async()=>{
 const f=setup('review');f.prepareRevision.mockResolvedValue(ok({ok:true,pending:false,message:''}))
 fireEvent.click(await screen.findByRole('button',{name:'继续改'}))
 await act(async()=>{await Promise.resolve();await Promise.resolve()})
 expect(f.clearDraft).toHaveBeenCalledTimes(1);expect(f.cardAction).not.toHaveBeenCalled()
})
it('模型拆分失败时留下草稿并显示失败，不能假装已经追加',async()=>{
 const f=setup('review');f.prepareRevision.mockResolvedValue(ok({ok:false,pending:false,message:'暂时拆不出修改，请重试'}))
 fireEvent.click(await screen.findByRole('button',{name:'继续改'}))
 expect(await screen.findByText(/暂时拆不出修改/)).toBeTruthy()
 expect(f.clearDraft).not.toHaveBeenCalled();expect(f.cardAction).not.toHaveBeenCalled()
})
it('标准已经逐条更新时显示当前验收标准，不能仍叫原标准以追加为准',async()=>{
 setup('review',{criteriaUpdated:true,changeNotes:['第二条改成双击保存']})
 await screen.findByRole('button',{name:'继续改'})
 fireEvent.click(screen.getByRole('button',{name:'展开'}))
 expect(screen.queryByText(/原验收标准/)).toBeNull()
 expect(screen.getByText('验收标准（请你核对）')).toBeTruthy()
 expect(screen.getByText('追加要求记录')).toBeTruthy()
})
