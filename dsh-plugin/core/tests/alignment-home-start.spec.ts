import { expect, it, vi } from 'vitest'
const modules=import.meta.glob('../src/client/chat-navigation.ts',{eager:true}) as Record<string,Record<string,any>>
function starter(run:()=>Promise<void>) {
  const create=modules['../src/client/chat-navigation.ts']?.coalesceChatStart
  expect(create,'首页补建与首配/新会话同时触发时不能重复建会话').toBeTypeOf('function')
  return create(run) as ()=>Promise<void>
}
it('只合并进行中的开新会话请求，完成后下次仍创建全新会话',async()=>{
  let finish!:()=>void
  const run=vi.fn(()=>new Promise<void>(resolve=>{finish=resolve}))
  const start=starter(run)
  const a=start(),b=start()
  await Promise.resolve()
  expect(run).toHaveBeenCalledTimes(1)
  finish();await Promise.all([a,b])
  const c=start();await Promise.resolve()
  expect(run).toHaveBeenCalledTimes(2)
  finish();await c
})
it('创建失败后释放进行中状态，下一次可以重试',async()=>{
  const run=vi.fn().mockRejectedValueOnce(new Error('短暂失败')).mockResolvedValue(undefined)
  const start=starter(run)
  await expect(start()).rejects.toThrow('短暂失败')
  await start()
  expect(run).toHaveBeenCalledTimes(2)
})
