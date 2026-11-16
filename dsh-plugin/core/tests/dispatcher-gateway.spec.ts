import {expect,it,vi} from 'vitest'
import Gateway from '../src/index.ts'
it('已处理或不存在的接入确认卡不能再次运行检测',async()=>{const g=Object.create(Gateway.prototype) as any;g.post=vi.fn(async()=>({ok:true,status:'ready',tool:'codex'}));const r=await g.connectDispatcher('codex',{sessionId:'chat-1',proposalId:'already-handled'});expect(r.ok).toBe(false);expect(g.post).not.toHaveBeenCalled()})
