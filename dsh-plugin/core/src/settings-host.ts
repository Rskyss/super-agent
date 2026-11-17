/**
 * 设置整页 · 通用（页面对齐改版第六批，对照 15-设置通用）：
 * - 钥匙串里存着的 Key：只列名字，不带 Key 内容；可删（只认自家登记过的正式条目和旧版复制来的条目）；
 * - 任务和对话记录：DSH_HOME 的真实位置和占用大小，可在访达打开。
 * 花费提醒用户 11-16 晚定先不做。
 */
import type { Context } from '@deepseek-ai/cordis'
import { resolveDshHome } from '@deepseek-ai/dsh-home-paths'
import { writeFileAtomic } from '@deepseek-ai/dsh-atomic-write'
import { spawn } from 'node:child_process'
import { lstat, readdir, readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { isKeychainModeEnabled, isMacCredentialProvider, resolveAssistantKeyRef } from './credentials-provider.ts'
import { LEGACY_KEY_MANIFEST_FILENAME, readLegacyOptions } from './legacy-keys.ts'
import type { RecordsInfo, StoredKey } from './types.ts'

interface KeyOps {
  listRefs(): Promise<string[]>
  describe(ref: never): Promise<{ configured: boolean }>
  unset(ref: never): Promise<void>
  readLegacyKey(account: string): Promise<string | undefined>
  deleteLegacyKey(account: string): Promise<void>
}

function keyOps(ctx: Context): KeyOps | null {
  if (!isKeychainModeEnabled()) return null
  const credentials = ctx.get('credentials')
  if (!isMacCredentialProvider(credentials)) return null
  return credentials as unknown as KeyOps
}

function dshHome(): string {
  return (process.env.DSH_HOME ?? '').trim() || resolveDshHome()
}

/** @returns 真实存着的条目（名字、是否超级玩家正在用），不含 Key 内容。 */
export async function listStoredKeys(ctx: Context): Promise<StoredKey[]> {
  const ops = keyOps(ctx)
  if (!ops) return []
  const assistant = String(resolveAssistantKeyRef())
  const out: StoredKey[] = []
  for (const ref of (await ops.listRefs()).sort()) {
    const info = await ops.describe(ref as never)
    if (!info.configured) continue
    const inUse = ref === assistant
    out.push({ id: `ref:${ref}`, label: inUse ? '超级玩家的大模型' : ref, inUse })
  }
  out.sort((a, b) => Number(b.inUse) - Number(a.inUse))
  for (const option of await readLegacyOptions(dshHome())) {
    const value = await ops.readLegacyKey(option.account)
    if (value === undefined || value.trim() === '') continue
    out.push({ id: option.account, label: `旧版复制来的 · ${option.label}`, inUse: false })
  }
  return out
}

/** 删一条；只认 listStoredKeys 列得出来的，别的一律拒绝。 */
export async function deleteStoredKey(ctx: Context, id: string): Promise<void> {
  const ops = keyOps(ctx)
  if (!ops) throw new Error('没有开钥匙串模式，没有可删的 Key')
  const known = await listStoredKeys(ctx)
  if (typeof id !== 'string' || !known.some(item => item.id === id)) {
    throw new Error('找不到这把 Key，可能已经删掉了')
  }
  if (id.startsWith('ref:')) {
    await ops.unset(id.slice('ref:'.length) as never)
    return
  }
  await ops.deleteLegacyKey(id)
  const manifest = join(dshHome(), LEGACY_KEY_MANIFEST_FILENAME)
  try {
    const data = JSON.parse(await readFile(manifest, 'utf8')) as { accounts?: unknown }
    const accounts = Array.isArray(data.accounts) ? data.accounts.filter(item => item !== id) : []
    await writeFileAtomic(manifest, `${JSON.stringify({ ...data, accounts }, null, 2)}\n`, { mode: 0o600, dirMode: 0o700 })
  } catch {
    // 清单坏了或不在：钥匙串里已经删掉，清单读不出时下次也不会列出来
  }
}

const SIZE_LIMIT_FILES = 200_000

async function folderBytes(root: string): Promise<number> {
  let total = 0
  let seen = 0
  const stack = [root]
  while (stack.length > 0 && seen < SIZE_LIMIT_FILES) {
    const dir = stack.pop()!
    let entries
    try {
      entries = await readdir(dir, { withFileTypes: true })
    } catch {
      continue
    }
    for (const entry of entries) {
      seen += 1
      const full = join(dir, entry.name)
      if (entry.isDirectory()) {
        stack.push(full)
      } else if (entry.isFile()) {
        try {
          total += (await lstat(full)).size
        } catch {
          // 读的时候被删了，跳过
        }
      }
    }
  }
  return total
}

/** @returns 任务和对话记录的真实位置和占用大小。 */
export async function recordsInfo(): Promise<RecordsInfo> {
  const path = dshHome()
  return { path, bytes: await folderBytes(path) }
}

/** 在访达里打开记录文件夹。 */
export function openRecordsFolder(): void {
  const child = spawn('open', [dshHome()], { detached: true, stdio: 'ignore' })
  child.on('error', () => {})
  child.unref()
}

/**
 * 一键检测 · 大脑（复查第 1 轮中等 9）：拿存着的 Key 真连一次服务商，看列表里有没有正在用的模型。
 * Key 只在主机里用，不交给界面；出错原因去掉 Key。
 */
export async function checkAssistantModel(
  state: { readonly configured: boolean; readonly provider?: string; readonly baseUrl?: string; readonly model?: string },
  readKey: () => Promise<string | undefined>,
  discover: (request: { provider: string; baseUrl: string; key: string }) => Promise<readonly { id: string }[]>,
): Promise<{ ok: boolean; reason: string }> {
  if (!state.configured || !state.model) return { ok: false, reason: '还没配好超级玩家的大模型' }
  const key = await readKey()
  if (!key) return { ok: false, reason: '钥匙串里没有这把 Key 了，请重新配置大模型' }
  try {
    const list = await discover({ provider: state.provider ?? 'other', baseUrl: state.baseUrl ?? '', key })
    if (!list.some(item => item.id === state.model)) {
      return { ok: false, reason: `连得上，但服务商的模型列表里没有 ${state.model} 了` }
    }
    return { ok: true, reason: '' }
  } catch (error: unknown) {
    const msg = error instanceof Error ? error.message : String(error)
    return { ok: false, reason: msg.split(key).join('***') || '连不上' }
  }
}
