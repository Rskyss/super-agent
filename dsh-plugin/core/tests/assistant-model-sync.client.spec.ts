// @vitest-environment jsdom
/// <reference types="vite/client" />
import {expect,it,vi} from 'vitest'
const modules=import.meta.glob('../src/client/assistant-model-sync.ts',{eager:true}) as Record<string,any>
it('更换助手后已有对话使用原生选模型接口跟随新模型，不另开会话',async()=>{const sync=modules['../src/client/assistant-model-sync.ts']?.syncAssistantModelToSessions;expect(sync,'缺少旧对话跟随助手模型的接线').toBeTypeOf('function');const selectModel=vi.fn(async()=>({ok:true,value:{selected:{provider:'superagent-custom',model:'chat-b'}}}));await sync({selectModel}, {provider:'superagent-custom',model:'chat-b'},['existing-chat']);expect(selectModel).toHaveBeenCalledTimes(1);expect(selectModel).toHaveBeenCalledWith({sessionId:'existing-chat',provider:'superagent-custom',model:'chat-b'})})
it('旧对话选模型失败时不能假称已同步成功',async()=>{const sync=modules['../src/client/assistant-model-sync.ts']?.syncAssistantModelToSessions;expect(sync).toBeTypeOf('function');await expect(sync({selectModel:vi.fn(async()=>({ok:false,error:{message:'fixture model unavailable'}}))},{provider:'superagent-custom',model:'chat-b'},['existing-chat'])).rejects.toThrow(/fixture model unavailable/)})
