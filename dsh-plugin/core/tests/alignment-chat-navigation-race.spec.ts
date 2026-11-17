import { expect, it, vi } from 'vitest'
const modules=import.meta.glob('../src/client/chat-navigation.ts',{eager:true}) as Record<string,Record<string,any>>
function navigate(){
  const fn=modules['../src/client/chat-navigation.ts']?.createChatForCurrentView
  expect(fn,'异步创建默认聊天后不能覆盖用户后来的导航').toBeTypeOf('function')
  return fn as <T>(signal:AbortSignal,create:()=>Promise<T>,open:(id:T)=>void)=>Promise<void>
}
it('创建会话期间切到别的页面，不被迟到结果抢回',async()=>{
  let finish!:(id:string)=>void
  const navigation=new AbortController()
  const open=vi.fn(),create=()=>new Promise<string>(r=>{finish=r})
  const run=navigate()(navigation.signal,create,open)
  navigation.abort();finish('new-chat');await run
  expect(open).not.toHaveBeenCalled()
})
it('没有改变导航时打开新会话，创建失败仍向调用者报错',async()=>{
  const open=vi.fn()
  await navigate()(new AbortController().signal,async()=>'new-chat',open)
  expect(open).toHaveBeenCalledWith('new-chat')
  await expect(navigate()(new AbortController().signal,async()=>{throw new Error('创建失败')},open)).rejects.toThrow('创建失败')
  expect(open).toHaveBeenCalledTimes(1)
})
