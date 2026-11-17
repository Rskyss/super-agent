import {expect,it} from 'vitest'
import {formatSafeOnboardingError} from '../src/onboarding.ts'
it('实际503发现失败显示短中文原因，不把技术URL当作原因',()=>{
  const text=formatSafeOnboardingError(new Error('http://127.0.0.1:48921/v1/models answered 503'))
  expect(text).toBe('服务商暂时不可用，请稍后重新粘贴 Key')
})
