import {expect,it} from 'vitest'
const modules=import.meta.glob('../src/receipt-anchor.ts',{eager:true}) as Record<string,Record<string,any>>
const windows=[{startSeq:7,startTime:Date.parse('2026-11-16T09:18:00Z'),endTime:Date.parse('2026-11-16T09:18:20Z')},{startSeq:24,startTime:Date.parse('2026-11-16T09:19:00Z'),endTime:Date.parse('2026-11-16T09:19:10Z')}]
function resolve(){const f=modules['../src/receipt-anchor.ts']?.resolveReceiptTurn;expect(f).toBeTypeOf('function');return f}
it('03b精确轮次锚在新消息后仍留在原确认轮，不挪到最新轮',()=>{
 const f=resolve();expect(f(null,7,windows)).toBe(7)
 expect(f('2026-11-16T09:18:30Z',7,[...windows,{startSeq:40,startTime:Date.parse('2026-11-16T09:20:00Z'),endTime:null}])).toBe(7)
})
it('旧卡只有整个秒精度确认窗口唯一落在两轮之间才恢复，不能误配同秒边界',()=>{
 const f=resolve();expect(f('2026-11-16T09:18:30Z',undefined,windows)).toBe(7)
 const ambiguous=[{...windows[0],endTime:Date.parse('2026-11-16T09:18:30.500Z')},windows[1]]
 expect(f('2026-11-16T09:18:30Z',undefined,ambiguous)).toBeNull()
})
it('已保存轮次尚未加载或时间未知时不造最新轮锚，也不回退覆盖精确记录',()=>{
 const f=resolve();expect(f('2026-11-16T09:18:30Z',99,windows)).toBeNull()
 expect(f(null,undefined,windows)).toBeNull()
})
