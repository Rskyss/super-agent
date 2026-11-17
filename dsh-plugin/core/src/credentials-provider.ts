/**
 * DSH CredentialProvider 正式插件入口与 macOS 钥匙串实现：
 * - 根据 `SUPERAGENT_KEYCHAIN=1` 开关在启动时二选一挂载 `MacCredentialProvider` 或原 `LocalCredentialProvider`，
 *   绝不重复注册 `credentials` 服务，兼容旧测试隔离环境不改动现有凭据配置。
 * - `MacCredentialProvider` 下：密钥与 record 仅存 macOS 钥匙串，磁盘只写非敏感引用/地址元数据，
 *   所有写操作通过进程内队列与 `withFileLock` 串行与跨进程保护；任何钥匙串异常或非 macOS 环境
 *   立即明确报错，绝不回退写明文文件，也不读取/迁移用户原有真实 Key。
 */
import type { Context } from '@deepseek-ai/cordis'
import { withFileLock, writeFileAtomic } from '@deepseek-ai/dsh-atomic-write'
import {
  CredentialProvider,
  credentialRef,
  parseCredentialKey,
  type CredentialInfo,
  type CredentialKey,
  type CredentialRecord,
  type CredentialRecordEntry,
  type CredentialRecordInfo,
  type CredentialRef,
  type ResolvedCredential,
} from '@deepseek-ai/dsh-credentials'
import LocalCredentialProvider, {
  resolveSpec,
  type Config as LocalCredentialsConfig,
} from '@deepseek-ai/dsh-credentials-local'
import { resolveDshHome } from '@deepseek-ai/dsh-home-paths'
import z from '@deepseek-ai/schemastery'
import { mkdir, readFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import {
  DEFAULT_KEYCHAIN_SERVICE,
  MacKeychainStore,
  maskSecret,
  redactSecretsInText,
  validateKeychainServiceName,
} from './keychain-store.ts'

/** 跨编译产物边界识别正式 `MacCredentialProvider` 的全局稳定品牌标记。 */
export const MAC_CREDENTIAL_PROVIDER_BRAND = Symbol.for('@superagent/core/MacCredentialProvider')

const REQUIRED_CREDENTIAL_METHODS = [
  'resolve',
  'describe',
  'set',
  'unset',
  'readRecord',
  'describeRecord',
  'listRecords',
  'modifyRecord',
  'deleteRecord',
] as const

/** 判断凭据提供方是否为具备完整能力的正式钥匙串实现（跨模块包边界稳定有效）。 */
export function isMacCredentialProvider(candidate: unknown): candidate is CredentialProvider {
  if (typeof candidate !== 'object' || candidate === null) return false
  const tagged = candidate as Record<symbol, unknown>
  if (tagged[MAC_CREDENTIAL_PROVIDER_BRAND] !== true) return false
  const record = candidate as Record<string, unknown>
  return REQUIRED_CREDENTIAL_METHODS.every(method => typeof record[method] === 'function')
}

async function restoreKeychainAccount(
  store: KeychainStoreLike,
  account: string,
  previousValue: string | undefined,
): Promise<boolean> {
  try {
    if (previousValue !== undefined && previousValue.trim().length > 0) {
      await store.set(account, previousValue)
    } else {
      await store.delete(account)
    }
    return true
  } catch {
    return false
  }
}

/** 超级助手专属默认凭据引用名（只探测自家专用 ref，不枚举系统钥匙串）。 */
export const DEFAULT_ASSISTANT_KEY_REF = 'SUPERAGENT_ASSISTANT_API_KEY'

/** 钥匙串地址元数据文件名（仅存非敏感地址索引，绝不存密钥、token 或 grant）。 */
export const KEYCHAIN_META_FILENAME = '.credentials.keychain-meta.json'

interface KeychainMetadataFile {
  version: 1
  service: string
  refs: string[]
  records: Record<string, { kind: CredentialRecord['kind'] }>
}

/** 钥匙串存储接口（允许单元测试注入替代实现，生产走 MacKeychainStore）。 */
export interface KeychainStoreLike {
  readonly service: string
  get(account: string): Promise<string | undefined>
  set(account: string, value: string): Promise<void>
  delete(account: string): Promise<void>
}

export interface MacCredentialProviderOptions extends LocalCredentialsConfig {
  readonly service?: string | undefined
  readonly store?: KeychainStoreLike | undefined
}

/** 检查是否开启 Mac 钥匙串模式（未开启时保持原文件凭据提供方，保护既有隔离测试）。 */
export function isKeychainModeEnabled(env: NodeJS.ProcessEnv = process.env): boolean {
  return env.SUPERAGENT_KEYCHAIN === '1'
}

/** 解析当前钥匙串服务名（支持测试环境专用 UUID 服务名覆盖，非法服务名立即拒绝）。 */
export function resolveKeychainServiceFromEnv(env: NodeJS.ProcessEnv = process.env): string {
  const override = env.SUPERAGENT_KEYCHAIN_SERVICE?.trim()
  if (override && override.length > 0) {
    return validateKeychainServiceName(override)
  }
  return DEFAULT_KEYCHAIN_SERVICE
}

/** 解析超级助手专用凭据 ref（支持测试覆盖，严格校验为合法环境变量标识符）。 */
export function resolveAssistantKeyRef(env: NodeJS.ProcessEnv = process.env): CredentialRef {
  const override = env.SUPERAGENT_KEYCHAIN_TEST_REF?.trim() || env.SUPERAGENT_ASSISTANT_KEY_REF?.trim()
  const raw = override && override.length > 0 ? override : DEFAULT_ASSISTANT_KEY_REF
  return credentialRef(raw)
}

function refAccount(ref: CredentialRef): string {
  return `ref:${ref}`
}

function recordAccount(key: CredentialKey): string {
  return `record:${key}`
}

function resolveMetadataPath(config: LocalCredentialsConfig): string {
  if (config.path) {
    return join(dirname(resolveSpec(config).filename), KEYCHAIN_META_FILENAME)
  }
  return join(resolveDshHome(config.dshHome), KEYCHAIN_META_FILENAME)
}

function emptyMetadata(service: string): KeychainMetadataFile {
  return { version: 1, service, refs: [], records: {} }
}

function validateRecordShape(raw: unknown): CredentialRecord {
  if (typeof raw !== 'object' || raw === null) {
    throw new Error('钥匙串中的凭据记录格式无效')
  }
  const obj = raw as Record<string, unknown>
  if (obj.kind === 'api-key') {
    const out: { kind: 'api-key'; key?: string; env?: Record<string, string> } = { kind: 'api-key' }
    if (obj.key !== undefined) {
      if (typeof obj.key !== 'string') throw new Error('钥匙串中的 api-key 记录字段无效')
      out.key = obj.key
    }
    if (obj.env !== undefined) {
      if (typeof obj.env !== 'object' || obj.env === null || Array.isArray(obj.env)) {
        throw new Error('钥匙串中的 api-key env 字段无效')
      }
      const envMap: Record<string, string> = {}
      for (const [k, v] of Object.entries(obj.env)) {
        if (typeof v !== 'string') throw new Error('钥匙串中的 api-key env 值必须为字符串')
        envMap[k] = v
      }
      out.env = envMap
    }
    return out
  }
  if (obj.kind === 'grant') {
    return { kind: 'grant', payload: obj.payload }
  }
  throw new Error('钥匙串中的凭据记录类型不受支持')
}

async function readMetadataFile(metaPath: string, service: string): Promise<KeychainMetadataFile> {
  let text: string
  try {
    text = await readFile(metaPath, 'utf8')
  } catch (error: unknown) {
    if ((error as NodeJS.ErrnoException)?.code === 'ENOENT') {
      return emptyMetadata(service)
    }
    throw error
  }
  if (!text.trim()) return emptyMetadata(service)
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    throw new Error('凭据地址元数据文件损坏，无法解析')
  }
  if (typeof parsed !== 'object' || parsed === null) {
    throw new Error('凭据地址元数据文件结构无效')
  }
  const obj = parsed as Partial<KeychainMetadataFile>
  const refs = Array.isArray(obj.refs)
    ? Array.from(new Set(obj.refs.filter((item): item is string => typeof item === 'string')))
    : []
  const records: Record<string, { kind: CredentialRecord['kind'] }> = {}
  if (obj.records && typeof obj.records === 'object' && !Array.isArray(obj.records)) {
    for (const [k, v] of Object.entries(obj.records)) {
      if (v && typeof v === 'object' && (v.kind === 'api-key' || v.kind === 'grant')) {
        // 仅允许保留 kind 地址元数据，绝不把多余字段留在磁盘
        records[parseCredentialKey(k)] = { kind: v.kind }
      }
    }
  }
  return { version: 1, service, refs, records }
}

async function writeMetadataFile(metaPath: string, meta: KeychainMetadataFile): Promise<void> {
  await mkdir(dirname(metaPath), { recursive: true, mode: 0o700 })
  await writeFileAtomic(metaPath, `${JSON.stringify(meta, null, 2)}\n`, { mode: 0o600, dirMode: 0o700 })
}

/** 基于 Mac 钥匙串的 DSH CredentialProvider 正式实现。 */
export class MacCredentialProvider extends CredentialProvider {
  readonly [MAC_CREDENTIAL_PROVIDER_BRAND] = true

  static Config: z<LocalCredentialsConfig> = z.object({
    path: z.string(),
    dshHome: z.string(),
    watch: z.boolean().default(true),
    debounceMs: z.number().min(0).default(100),
  })

  private readonly store: KeychainStoreLike
  private readonly metaPath: string
  private readonly service: string
  private writeQueue: Promise<void> = Promise.resolve()

  constructor(ctx: Context, config: MacCredentialProviderOptions = {}) {
    super(ctx)
    if (!config.store && process.platform !== 'darwin') {
      throw new Error('当前操作系统不是 macOS，无法启用 Mac 钥匙串凭据存储（已拒绝回退明文文件）')
    }
    this.service = config.service
      ? validateKeychainServiceName(config.service)
      : resolveKeychainServiceFromEnv()
    this.store = config.store ?? new MacKeychainStore({ service: this.service })
    this.metaPath = resolveMetadataPath(config)
  }

  /** 将所有写入动作先在进程内排队，再用跨进程 `withFileLock` 串行化。 */
  private runExclusive<T>(task: () => Promise<T>): Promise<T> {
    const next = this.writeQueue.then(async () => {
      await mkdir(dirname(this.metaPath), { recursive: true, mode: 0o700 })
      return withFileLock(this.metaPath, task)
    })
    this.writeQueue = next.then(() => undefined, () => undefined)
    return next
  }

  /**
   * 第 17 项：读旧版复制过来的模型 Key（条目名 `legacy:<旧名>`，由 Claude 一次性复制）。
   * 只认 legacy: 开头，正式凭据仍只能经 resolve 读；值只在主机内使用，不交给界面。
   */
  async readLegacyKey(account: string): Promise<string | undefined> {
    if (typeof account !== 'string' || !/^legacy:.+/.test(account)) {
      throw new Error('只能读旧版复制过来的钥匙串条目')
    }
    return this.store.get(account)
  }

  /** 设置 · 通用：已登记的正式条目名（只读本地索引，不扫描系统钥匙串，不含 Key 内容）。 */
  async listRefs(): Promise<string[]> {
    const meta = await readMetadataFile(this.metaPath, this.service)
    return [...meta.refs]
  }

  /** 设置 · 通用：删掉旧版复制过来的条目（只认 legacy: 开头）。 */
  async deleteLegacyKey(account: string): Promise<void> {
    if (typeof account !== 'string' || !/^legacy:.+/.test(account)) {
      throw new Error('只能删旧版复制过来的钥匙串条目')
    }
    await this.runExclusive(async () => { await this.store.delete(account) })
  }

  /** 每次按需实时读取 Mac 钥匙串；绝不缓存、绝不读旧 `.credentials.yaml` 或宿主环境密钥。 */
  async resolve(ref: CredentialRef): Promise<ResolvedCredential | undefined> {
    const validRef = credentialRef(ref)
    const value = await this.store.get(refAccount(validRef))
    if (value === undefined || value.trim().length === 0) return undefined
    return { value, source: 'keychain' }
  }

  /** 仅返回是否已配置与不可反推的遮罩预览，绝不返回明文密钥。 */
  async describe(ref: CredentialRef): Promise<CredentialInfo> {
    const validRef = credentialRef(ref)
    const value = await this.store.get(refAccount(validRef))
    if (value === undefined || value.trim().length === 0) {
      return { configured: false, writable: true }
    }
    return {
      configured: true,
      source: 'keychain',
      writable: true,
      masked: maskSecret(value),
    }
  }

  /** 先校读地址元数据，再写入钥匙串与元数据；若落盘失败立即恢复先前钥匙串状态并明确区分回滚结果。 */
  async set(ref: CredentialRef, value: string): Promise<void> {
    const validRef = credentialRef(ref)
    if (typeof value !== 'string' || value.length === 0 || value.trim().length === 0) {
      throw new Error('密钥不能为空；如需清除请使用 unset')
    }
    await this.runExclusive(async () => {
      const account = refAccount(validRef)
      // 先校验读取地址元数据，损坏时绝不覆盖钥匙串里的已有值
      const meta = await readMetadataFile(this.metaPath, this.service)
      const previousValue = await this.store.get(account)
      try {
        await this.store.set(account, value)
        if (!meta.refs.includes(validRef)) {
          meta.refs.push(validRef)
          meta.refs.sort()
          await writeMetadataFile(this.metaPath, meta)
        }
      } catch (error: unknown) {
        const restored = await restoreKeychainAccount(this.store, account, previousValue)
        const reason = redactSecretsInText(
          error instanceof Error ? error.message : String(error),
          [value, previousValue ?? ''],
        )
        throw new Error(
          restored
            ? `保存凭据失败（已恢复钥匙串原状）：${reason || '写入失败'}`
            : `保存凭据失败，且钥匙串未能恢复原状：${reason || '写入与回滚均失败'}`,
        )
      }
    })
    this.notifyUpdated(validRef)
  }

  /** 从钥匙串与索引中移除指定 ref（先校验元数据，失败时尽力恢复）。 */
  async unset(ref: CredentialRef): Promise<void> {
    const validRef = credentialRef(ref)
    const removed = await this.runExclusive(async () => {
      const account = refAccount(validRef)
      const meta = await readMetadataFile(this.metaPath, this.service)
      const previousValue = await this.store.get(account)
      const hadRef = meta.refs.includes(validRef)
      try {
        await this.store.delete(account)
        if (hadRef) {
          meta.refs = meta.refs.filter(item => item !== validRef)
          await writeMetadataFile(this.metaPath, meta)
        }
        return hadRef
      } catch (error: unknown) {
        const restored = await restoreKeychainAccount(this.store, account, previousValue)
        const reason = redactSecretsInText(
          error instanceof Error ? error.message : String(error),
          [previousValue ?? ''],
        )
        throw new Error(
          restored
            ? `移除凭据失败（已恢复钥匙串原状）：${reason || '移除失败'}`
            : `移除凭据失败，且钥匙串未能恢复原状：${reason || '移除与回滚均失败'}`,
        )
      }
    })
    if (removed) {
      this.notifyUpdated(validRef)
    }
  }

  private async readRecordInternal(key: CredentialKey): Promise<CredentialRecord | undefined> {
    const raw = await this.store.get(recordAccount(key))
    if (raw === undefined) return undefined
    let parsed: unknown
    try {
      parsed = JSON.parse(raw)
    } catch {
      throw new Error('Mac 钥匙串中的记录 JSON 格式损坏')
    }
    return validateRecordShape(parsed)
  }

  /** 读取以 JSON 存在钥匙串里的完整 record（仅限 Host 内部消费，不落盘）。 */
  async readRecord(key: CredentialKey): Promise<CredentialRecord | undefined> {
    const validKey = parseCredentialKey(key)
    return this.readRecordInternal(validKey)
  }

  /** 描述 record 存在性与类型，绝不透出 grant 或 key 值。 */
  async describeRecord(key: CredentialKey): Promise<CredentialRecordInfo> {
    const validKey = parseCredentialKey(key)
    const stored = await this.readRecordInternal(validKey)
    if (stored === undefined) {
      return { configured: false, writable: true }
    }
    return {
      configured: true,
      kind: stored.kind,
      writable: true,
    }
  }

  /** 从本地非敏感地址元数据枚举已知 record（绝不扫描/枚举系统钥匙串条目）。 */
  async listRecords(): Promise<readonly CredentialRecordEntry[]> {
    const meta = await readMetadataFile(this.metaPath, this.service)
    return Object.entries(meta.records)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([k, entry]) => ({
        key: parseCredentialKey(k),
        kind: entry.kind,
      }))
  }

  /** 在 `withFileLock` 保护下按“先校验元数据 -> 读取 -> 计算 -> 写钥匙串 -> 写元数据”原子修改 record，失败时恢复先前记录。 */
  async modifyRecord(
    key: CredentialKey,
    mutate: (current: CredentialRecord | undefined) => Promise<CredentialRecord | undefined>,
  ): Promise<CredentialRecord | undefined> {
    const validKey = parseCredentialKey(key)
    let changed = false
    const result = await this.runExclusive(async () => {
      const account = recordAccount(validKey)
      const meta = await readMetadataFile(this.metaPath, this.service)
      const previousRaw = await this.store.get(account)
      let current: CredentialRecord | undefined
      if (previousRaw !== undefined) {
        let parsed: unknown
        try {
          parsed = JSON.parse(previousRaw)
        } catch {
          throw new Error('Mac 钥匙串中的记录 JSON 格式损坏')
        }
        current = validateRecordShape(parsed)
      }
      const replacement = await mutate(current)
      if (replacement === undefined) {
        return current
      }
      const validated = validateRecordShape(replacement)
      const nextRaw = JSON.stringify(validated)
      try {
        await this.store.set(account, nextRaw)
        meta.records[validKey] = { kind: validated.kind }
        await writeMetadataFile(this.metaPath, meta)
        changed = true
        return validated
      } catch (error: unknown) {
        const restored = await restoreKeychainAccount(this.store, account, previousRaw)
        const reason = redactSecretsInText(
          error instanceof Error ? error.message : String(error),
          [nextRaw, previousRaw ?? ''],
        )
        throw new Error(
          restored
            ? `修改凭据记录失败（已恢复钥匙串原状）：${reason || '写入失败'}`
            : `修改凭据记录失败，且钥匙串未能恢复原状：${reason || '写入与回滚均失败'}`,
        )
      }
    })
    if (changed) {
      this.notifyRecordUpdated(validKey)
    }
    return result
  }

  /** 删除指定 record（先校验元数据，失败时尽力恢复先前记录）。 */
  async deleteRecord(key: CredentialKey): Promise<void> {
    const validKey = parseCredentialKey(key)
    const changed = await this.runExclusive(async () => {
      const account = recordAccount(validKey)
      const meta = await readMetadataFile(this.metaPath, this.service)
      const existedInMeta = validKey in meta.records
      const previousRaw = await this.store.get(account)
      const existedInChain = previousRaw !== undefined
      try {
        await this.store.delete(account)
        if (existedInMeta) {
          delete meta.records[validKey]
          await writeMetadataFile(this.metaPath, meta)
        }
        return existedInMeta || existedInChain
      } catch (error: unknown) {
        const restored = await restoreKeychainAccount(this.store, account, previousRaw)
        const reason = redactSecretsInText(
          error instanceof Error ? error.message : String(error),
          [previousRaw ?? ''],
        )
        throw new Error(
          restored
            ? `删除凭据记录失败（已恢复钥匙串原状）：${reason || '删除失败'}`
            : `删除凭据记录失败，且钥匙串未能恢复原状：${reason || '删除与回滚均失败'}`,
        )
      }
    })
    if (changed) {
      this.notifyRecordUpdated(validKey)
    }
  }
}

export const name = 'credentials'
export const Config: z<LocalCredentialsConfig> = MacCredentialProvider.Config

/**
 * 凭据插件二选一工厂：
 * - 仅当 `SUPERAGENT_KEYCHAIN=1` 时启用 `MacCredentialProvider`；
 * - 否则挂载原生 `LocalCredentialProvider`，保护既有隔离测试环境，避免重复注册 `credentials` 服务。
 */
export function apply(ctx: Context, config: LocalCredentialsConfig = {}): void {
  if (isKeychainModeEnabled()) {
    // 在挂载前再次核验平台与 service，避免启动异常或误落入文件凭据
    if (process.platform !== 'darwin') {
      throw new Error('已启用 SUPERAGENT_KEYCHAIN=1，但当前非 macOS 平台，拒绝启动并回退明文凭据')
    }
    resolveKeychainServiceFromEnv()
    ctx.plugin(MacCredentialProvider, config)
    return
  }
  ctx.plugin(LocalCredentialProvider, config)
}

export default {
  name,
  Config,
  apply,
}
