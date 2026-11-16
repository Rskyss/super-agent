// 第 17 项：旧版超级助手的模型 Key 已由 Claude 复制进新版钥匙串（条目名 legacy:<旧名>），
// 清单文件只记条目名（不含密钥）。首次使用页可以直接选用，Key 只在主机这边读，绝不交给界面。
import { expect, it, vi } from 'vitest'
import { legacyOptionsFromManifest, resolveLegacyRequest } from '../src/legacy-keys.ts'

const manifest = JSON.stringify({
  version: 1,
  accounts: ['legacy:tencent', 'legacy:bailian', 'legacy:other:ark.cn-beijing.volces.com', 'legacy:tavily', 'ref:SUPERAGENT_ASSISTANT_API_KEY', 'legacy:other:evil host'],
})

it('清单里的三把模型 Key 对应到服务商和旧版用的接口地址；零件 Key、非旧版条目、坏名字都不列', () => {
  const options = legacyOptionsFromManifest(manifest)
  expect(options.map(o => o.account)).toEqual(['legacy:tencent', 'legacy:bailian', 'legacy:other:ark.cn-beijing.volces.com'])
  const byAccount = Object.fromEntries(options.map(o => [o.account, o]))
  expect(byAccount['legacy:tencent']).toMatchObject({ provider: 'tencent', baseUrl: 'https://tokenhub.tencentmaas.com/v1' })
  expect(byAccount['legacy:tencent']!.label).toContain('腾讯云')
  expect(byAccount['legacy:bailian']).toMatchObject({ provider: 'dashscope', baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1' })
  expect(byAccount['legacy:other:ark.cn-beijing.volces.com']).toMatchObject({ provider: 'other', baseUrl: 'https://ark.cn-beijing.volces.com/api/v3' })
  expect(JSON.stringify(options)).not.toMatch(/key"\s*:/i)
})

it('清单不存在或坏了就是没有可导入的，不报错', () => {
  expect(legacyOptionsFromManifest('')).toEqual([])
  expect(legacyOptionsFromManifest('{坏')).toEqual([])
  expect(legacyOptionsFromManifest(JSON.stringify({ version: 1, accounts: 'legacy:tencent' }))).toEqual([])
})

it('选用旧版 Key：服务商和地址以主机清单为准（不信界面传来的），Key 由主机读出填进去', async () => {
  const options = legacyOptionsFromManifest(manifest)
  const readKey = vi.fn(async (account: string) => (account === 'legacy:tencent' ? 'fixture-legacy-secret' : undefined))
  const resolved = await resolveLegacyRequest(
    { provider: 'other', baseUrl: 'https://attacker.example/v1', key: '', legacyAccount: 'legacy:tencent', model: 'm' },
    options,
    readKey,
  )
  expect(resolved).toEqual({ provider: 'tencent', baseUrl: 'https://tokenhub.tencentmaas.com/v1', key: 'fixture-legacy-secret', model: 'm' })
  expect(readKey).toHaveBeenCalledWith('legacy:tencent')
})

it('不在清单里的条目一律拒绝、不去读钥匙串；读不到给大白话；没选旧版 Key 时原样放行', async () => {
  const options = legacyOptionsFromManifest(manifest)
  const readKey = vi.fn(async () => 'x')
  await expect(resolveLegacyRequest({ provider: 'p', baseUrl: '', key: '', legacyAccount: 'ref:SUPERAGENT_ASSISTANT_API_KEY' }, options, readKey)).rejects.toThrow(/不是旧版复制过来的/)
  await expect(resolveLegacyRequest({ provider: 'p', baseUrl: '', key: '', legacyAccount: 'legacy:tavily' }, options, readKey)).rejects.toThrow(/不是旧版复制过来的/)
  expect(readKey).not.toHaveBeenCalled()
  await expect(resolveLegacyRequest({ provider: 'p', baseUrl: '', key: '', legacyAccount: 'legacy:bailian' }, options, async () => undefined)).rejects.toThrow(/没读到/)
  const plain = { provider: 'deepseek', baseUrl: 'https://api.deepseek.com', key: 'typed' }
  expect(await resolveLegacyRequest(plain, options, readKey)).toBe(plain)
})
