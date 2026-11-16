import {expect,it} from 'vitest'
import {buildOpenAiChatCompletionsUrl,extractOpenAiReplyText} from '../src/onboarding-host.ts'
it('只有思考没有给用户的实际回复，不能算验证回话成功',()=>{expect(extractOpenAiReplyText({choices:[{message:{content:'',reasoning_content:'内部思考'}}]})).toBe('')})
it('用户填写的兼容接口基础路径保持原样，不擅自再加v1',()=>{expect(buildOpenAiChatCompletionsUrl('https://example.test/custom-api')).toBe('https://example.test/custom-api/chat/completions')})
