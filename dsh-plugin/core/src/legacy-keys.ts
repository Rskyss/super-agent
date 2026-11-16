/**
 * 第 17 项：旧版超级助手的模型 Key 已由 Claude 复制进新版钥匙串，条目名 `legacy:<旧名>`；
 * DSH_HOME 下的清单文件只记条目名（不含密钥）。首次使用页可以直接选用。
 * - 只认三类模型 Key（腾讯云、阿里百炼、其他 OpenAI 兼容），零件 Key（如 tavily）不列；
 * - 服务商和接口地址以主机这边的对应表为准，不信界面传来的；
 * - Key 只在主机这边读出来用于验证和保存，绝不返回界面。
 */
import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import type { LegacyKeyOption } from './types.ts'

export type { LegacyKeyOption } from './types.ts'

export const LEGACY_KEY_MANIFEST_FILENAME = 'superagent-legacy-keys.json'

/** 旧版“其他”条目只记了主机名；已知的写全路径，未知的按常见 /v1 试（验证不过会照实报错）。 */
const KNOWN_OTHER_PATHS: Readonly<Record<string, string>> = {
  'ark.cn-beijing.volces.com': '/api/v3',
}
const KNOWN_OTHER_LABELS: Readonly<Record<string, string>> = {
  'ark.cn-beijing.volces.com': '火山方舟',
}
const HOST_PATTERN = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$/

function optionOf(account: string): LegacyKeyOption | undefined {
  if (account === 'legacy:tencent') {
    // 旧版接的是腾讯云 TokenHub（不是新版“腾讯云”预置的混元地址）
    return { account, provider: 'tencent', label: '腾讯云 TokenHub', baseUrl: 'https://tokenhub.tencentmaas.com/v1' }
  }
  if (account === 'legacy:bailian') {
    return { account, provider: 'dashscope', label: '阿里百炼', baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1' }
  }
  const prefix = 'legacy:other:'
  if (account.startsWith(prefix)) {
    const host = account.slice(prefix.length)
    if (!HOST_PATTERN.test(host)) return undefined
    return {
      account,
      provider: 'other',
      label: KNOWN_OTHER_LABELS[host] ?? `其他：${host}`,
      baseUrl: `https://${host}${KNOWN_OTHER_PATHS[host] ?? '/v1'}`,
    }
  }
  return undefined
}

/** 解析清单（坏了、没有都当作没有可导入的）。 */
export function legacyOptionsFromManifest(text: string): LegacyKeyOption[] {
  let data: unknown
  try {
    data = JSON.parse(text)
  } catch {
    return []
  }
  const accounts = (data as { accounts?: unknown } | null)?.accounts
  if (!Array.isArray(accounts)) return []
  const seen = new Set<string>()
  const out: LegacyKeyOption[] = []
  for (const item of accounts) {
    if (typeof item !== 'string' || seen.has(item)) continue
    const option = optionOf(item)
    if (option) {
      seen.add(item)
      out.push(option)
    }
  }
  return out
}

/** 主机读清单：DSH_HOME 下的 superagent-legacy-keys.json。 */
export async function readLegacyOptions(dshHome: string | undefined = process.env.DSH_HOME): Promise<LegacyKeyOption[]> {
  if (!dshHome) return []
  try {
    return legacyOptionsFromManifest(await readFile(join(dshHome, LEGACY_KEY_MANIFEST_FILENAME), 'utf8'))
  } catch {
    return []
  }
}

interface LegacyCapableRequest {
  readonly provider: string
  readonly baseUrl: string
  readonly key: string
  readonly model?: string | undefined
  readonly legacyAccount?: string | undefined
}

/**
 * 请求带了 legacyAccount 时：核对在清单里，服务商和地址用清单的，Key 由主机从钥匙串读出填进去。
 * 没带就原样返回。
 */
export async function resolveLegacyRequest<T extends LegacyCapableRequest>(
  request: T,
  options: readonly LegacyKeyOption[],
  readKey: (account: string) => Promise<string | undefined>,
): Promise<Omit<T, 'legacyAccount'> | T> {
  const account = request.legacyAccount
  if (account === undefined || account === '') return request
  const option = options.find(item => item.account === account)
  if (!option) throw new Error('这把 Key 不是旧版复制过来的，不能这样选用')
  const key = (await readKey(option.account))?.trim()
  if (!key) throw new Error('旧版复制过来的 Key 没读到（可能钥匙串没允许），请重试或改成自己填')
  const { legacyAccount: _drop, ...rest } = request
  return { ...rest, provider: option.provider, baseUrl: option.baseUrl, key }
}
