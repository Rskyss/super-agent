// 复查（第 16/17 项第 1 轮）：服务商回包若在模型编号或名字里带了 Key，不能交给界面、不能写进模型配置。
// 手填 Key 和选用旧版 Key 都走这里，所以在根上拦。
import { expect, it } from 'vitest'
import { discoverOnboardingModelsOnHost } from '../src/onboarding-host.ts'

const KEY = 'sk-fixture/SECRET+1234567890='
function ctxWith(models: unknown[]) {
  const llm = { discoverModels: async () => models }
  return { get: (name: string) => (name === 'llm' ? llm : undefined) } as never
}

it('模型编号或名字里带 Key 的整条丢掉，其余照常', async () => {
  const out = await discoverOnboardingModelsOnHost(ctxWith([
    { id: 'deepseek/deepseek-flash' },
    { id: `echo-${KEY}` },
    { id: 'm2', name: `名字里有 ${KEY}` },
  ]), { provider: 'other', baseUrl: 'https://api.example.com/v1', key: KEY })
  expect(out.map(m => m.id)).toEqual(['deepseek/deepseek-flash'])
  expect(JSON.stringify(out)).not.toContain(KEY)
})

it('全被丢掉时照实报“没有可用模型”，报错里也不带 Key', async () => {
  await expect(discoverOnboardingModelsOnHost(ctxWith([{ id: KEY }]), { provider: 'other', baseUrl: 'https://api.example.com/v1', key: KEY }))
    .rejects.toThrow(/未返回可用的聊天模型/)
})

it('网址编码后的 Key 也拦住', async () => {
  const out = await discoverOnboardingModelsOnHost(ctxWith([
    { id: 'ok' },
    { id: `x-${encodeURIComponent(KEY)}` },
    { id: 'y', name: `名字 ${encodeURIComponent(KEY)}` },
  ]), { provider: 'other', baseUrl: 'https://api.example.com/v1', key: KEY })
  expect(out.map(m => m.id)).toEqual(['ok'])
})
