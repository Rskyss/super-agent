// @vitest-environment jsdom
import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ChangeDock } from '../src/client/ChangeDock.tsx'
afterEach(()=>{cleanup();vi.useRealTimers()})
it('空轮询跑过多次后主动刷新，立即显示新确认条，不等旧序号重新追平',async()=>{
 vi.useFakeTimers();let pending:any=null
 const props:any={card:'CARD-017',refreshTick:0,pendingChange:vi.fn(async()=>({ok:true,value:pending})),decideChange:vi.fn(),onStatusChange:vi.fn()}
 const r=render(<ChangeDock {...props}/>);
 await act(async()=>{await vi.advanceTimersByTimeAsync(12_000)})
 expect(props.pendingChange.mock.calls.length).toBeGreaterThan(5)
 pending={id:'fresh',card:'CARD-017',summary:'第2条：单击 → 双击',text:'改',createdAt:1}
 r.rerender(<ChangeDock {...props} refreshTick={1}/>);
 await act(async()=>{await Promise.resolve();await Promise.resolve()})
 expect(screen.queryByRole('button',{name:'确认改'})).not.toBeNull()
})
