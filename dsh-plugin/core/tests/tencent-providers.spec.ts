import { Context } from '@deepseek-ai/cordis'
import { credentialRef } from '@deepseek-ai/dsh-credentials'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, expect, it, vi } from 'vitest'
import { MacCredentialProvider } from '../src/credentials-provider.ts'
import { completeOnboardingOnHost, readOnboardingStateOnHost } from '../src/onboarding-host.ts'

const dirs: string[] = []
const ref = 'SUPERAGENT_TEST_R3_TENCENT'
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); for (const d of dirs.splice(0)) rmSync(d, { recursive: true, force: true }) })

async function fixture() {
  vi.stubEnv('SUPERAGENT_KEYCHAIN', '1')
  vi.stubEnv('SUPERAGENT_KEYCHAIN_TEST_REF', ref)
  const dir = mkdtempSync(join(tmpdir(), 'sa-tencent-providers-')); dirs.push(dir)
  const values = new Map<string, string>()
  const credentials = new MacCredentialProvider(new Context(), { dshHome: dir, store: {
    service: 'com.aisoup.superagent.test.r3-unit',
    get: async (k: string) => values.get(k), set: async (k: string, v: string) => { values.set(k, v) }, delete: async (k: string) => { values.delete(k) },
  } })
  await credentials.set(credentialRef(ref), 'fixture-r3-key')
  const providers: Record<string, any> = {}
  let selected = { provider: '', model: '' }
  let revision = 0
  const ctx = { get: (name: string) => ({
    credentials,
    settings: {
      describe: () => [{ ns: 'llm-pi-ai', revision, value: { providers }, user: { providers } }],
      mutate: async (_ns: string, ops: any[]) => { for (const op of ops) { if (op.op === 'set') providers[op.path[1]] = op.value; else delete providers[op.path[1]] } revision++ },
    },
    agentDefaultModel: { currentSelection: () => selected, saveSelection: async (v: typeof selected) => { selected = v } },
    llm: { discoverModels: async () => [{ id: 'fixture-chat', name: 'Fixture Chat' }] },
  } as Record<string, unknown>)[name] } as never
  return { ctx, providers, select: (baseURL: string) => {
    providers['superagent-tencent'] = { apiKeyEnv: ref, baseURL, models: [{ id: 'fixture-chat' }] }
    selected = { provider: 'superagent-tencent', model: 'fixture-chat' }
  } }
}

it('保存两个腾讯时，TokenHub和混元的模型路线分别保留，不覆盖另一家的接口', async () => {
  const t = await fixture()
  vi.stubGlobal('fetch', async () => new Response(JSON.stringify({ choices: [{ message: { content: '收到' } }] }), { status: 200 }))
  await completeOnboardingOnHost(t.ctx, { provider: 'tencent-tokenhub', baseUrl: '', key: 'fixture-tokenhub-key', model: 'fixture-chat' })
  await completeOnboardingOnHost(t.ctx, { provider: 'tencent', baseUrl: '', key: 'fixture-hunyuan-key', model: 'fixture-chat' })
  expect(t.providers['superagent-tencent-tokenhub']).toMatchObject({ displayName: '腾讯云 TokenHub', baseURL: 'https://tokenhub.tencentmaas.com/v1' })
  expect(t.providers['superagent-tencent']).toMatchObject({ displayName: '腾讯混元', baseURL: 'https://api.hunyuan.cloud.tencent.com/v1' })
})

it('以前保存的腾讯云混元路线仍能读回同一配置', async () => {
  const t = await fixture(); t.select('https://api.hunyuan.cloud.tencent.com/v1')
  expect(await readOnboardingStateOnHost(t.ctx)).toMatchObject({ configured: true, provider: 'tencent', routeId: 'superagent-tencent', baseUrl: 'https://api.hunyuan.cloud.tencent.com/v1' })
})

it('以前用旧腾讯编号保存过TokenHub时，按实际地址认成TokenHub而不是混元', async () => {
  const t = await fixture(); t.select('https://tokenhub.tencentmaas.com/v1')
  expect(await readOnboardingStateOnHost(t.ctx)).toMatchObject({ configured: true, provider: 'tencent-tokenhub', baseUrl: 'https://tokenhub.tencentmaas.com/v1' })
})
