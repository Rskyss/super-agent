/**
 * Host 侧首次使用 / 更换模型协调器（衔接 DSH 原生 `ctx.llm.discoverModels`、`ctx.credentials`、
 * `ctx.settings` (`llm-pi-ai`) 与 `ctx.agentDefaultModel`）：
 * - 首次状态探测只检查自家专用 ref（绝不枚举系统钥匙串条目）。
 * - 拉模型走 `ctx.llm.discoverModels('llm-pi-ai', { baseURL, api: 'openai-completions', apiKey })`，
 *   使用临时 Key 不落盘。
 * - 确认前发送一次真实的轻量 OpenAI `/chat/completions` 请求，收到非空回复后才保存；
 *   任何 HTTP 错误绝不透传可能回显密钥的响应原文。
 * - 保存时强制核验已启用 `MacCredentialProvider`（钥匙串模式），兼容文件模式绝不冒充存钥匙串；
 *   写入 `credentials` + `llm-pi-ai` + `agentDefaultModel` 任一环节失败时执行逆序回滚。
 */
import { readLegacyOptions, resolveLegacyRequest } from './legacy-keys.ts'
import type { Context } from '@deepseek-ai/cordis'
import type { CredentialProvider } from '@deepseek-ai/dsh-credentials'
import type { LlmDiscoveredModel } from '@deepseek-ai/dsh-llm'
import type { SettingsPathOp } from '@deepseek-ai/dsh-settings'
import {
  isKeychainModeEnabled,
  isMacCredentialProvider,
  resolveAssistantKeyRef,
} from './credentials-provider.ts'
import {
  completeAssistantSetup,
  findProviderPreset,
  formatSafeOnboardingError,
  normalizeApiKeyInput,
  redactOnboardingSecret,
  resolveEffectiveBaseUrl,
  translateHttpStatusMessage,
  validateSetupBaseUrl,
  type OnboardingModelOption,
  type OnboardingSetupRequest,
  type OnboardingSetupResult,
} from './onboarding.ts'
import type { OnboardingState } from './types.ts'

const LLM_PI_AI_NS = 'llm-pi-ai'
const PROBE_REPLY_TIMEOUT_MS = 15_000
const PROBE_REPLY_MAX_TOKENS = 128

/** Host 端受控本机测试开关：仅显式设为 `1` 时允许访问本机回环 http 地址。 */
function allowLoopbackHttpOnHost(): boolean {
  return process.env.SUPERAGENT_ALLOW_LOOPBACK_HTTP === '1'
}

/** 读取当前 `llm-pi-ai` 配置快照（含 revision 与 user 层 providers）。 */
function readPiAiSettingsSnapshot(ctx: Context): {
  revision: number | undefined
  providers: Record<string, Record<string, unknown>>
  userProviders: Record<string, unknown>
} {
  const settings = ctx.get('settings')
  if (!settings) {
    throw new Error('缺少系统设置服务（settings），无法读取模型配置')
  }
  const descriptors = settings.describe({ redactSecrets: true })
  const entry = descriptors.find(item => item.ns === LLM_PI_AI_NS)
  const rawProviders = (entry?.value as { providers?: unknown } | undefined)?.providers
  const rawUserProviders = (entry?.user as { providers?: unknown } | undefined)?.providers
  return {
    revision: entry?.revision,
    providers: rawProviders && typeof rawProviders === 'object' && !Array.isArray(rawProviders)
      ? { ...(rawProviders as Record<string, Record<string, unknown>>) }
      : {},
    userProviders: rawUserProviders && typeof rawUserProviders === 'object' && !Array.isArray(rawUserProviders)
      ? { ...(rawUserProviders as Record<string, unknown>) }
      : {},
  }
}

function isTokenHubBaseUrl(baseUrl: string): boolean {
  const lower = baseUrl.trim().toLowerCase()
  if (!lower) return false
  try {
    return new URL(lower).hostname === 'tokenhub.tencentmaas.com'
  } catch {
    return lower.includes('tokenhub.tencentmaas.com')
  }
}

/** 从原生设置反推当前超级玩家对应的引导预设名，供设置展示使用。 */
function inferPresetProvider(routeId: string, baseUrl: string): string {
  if (routeId === 'deepseek') return 'deepseek'
  if (routeId === 'superagent-dashscope') return 'dashscope'
  if (routeId === 'superagent-tencent-tokenhub') return 'tencent-tokenhub'
  if (routeId === 'superagent-tencent') {
    return isTokenHubBaseUrl(baseUrl) ? 'tencent-tokenhub' : 'tencent'
  }
  if (baseUrl.includes('api.deepseek.com')) return 'deepseek'
  if (baseUrl.includes('dashscope.aliyuncs.com')) return 'dashscope'
  if (isTokenHubBaseUrl(baseUrl)) return 'tencent-tokenhub'
  if (baseUrl.includes('cloud.tencent.com')) return 'tencent'
  return 'other'
}

/** 解析引导选项对应的 `llm-pi-ai` provider 路由名与展示名。 */
function resolveRouteForProvider(provider: string): { routeId: string; displayName: string } {
  const preset = findProviderPreset(provider)
  if (preset) {
    return { routeId: preset.routeId, displayName: preset.label }
  }
  return { routeId: 'superagent-custom', displayName: '自定义 OpenAI 兼容' }
}

/** 构造 OpenAI 兼容 `/chat/completions` 探测地址，与原生 OpenAI SDK 一致，保留用户基础路径不擅自加 `/v1`。 */
export function buildOpenAiChatCompletionsUrl(baseUrl: string): string {
  const clean = baseUrl.trim().replace(/\/+$/, '')
  return clean.endsWith('/chat/completions') ? clean : `${clean}/chat/completions`
}

/** 解析 OpenAI 兼容 `/chat/completions` 响应中给用户的实际文本回复（不把思考过程当成成功回复）。 */
export function extractOpenAiReplyText(payload: unknown): string {
  if (typeof payload !== 'object' || payload === null) return ''
  const choices = (payload as { choices?: unknown }).choices
  if (!Array.isArray(choices) || choices.length === 0) return ''
  const first = choices[0] as { message?: unknown; text?: unknown } | undefined
  if (!first) return ''
  if (typeof first.text === 'string' && first.text.trim()) {
    return first.text.trim()
  }
  const message = first.message as { content?: unknown } | undefined
  if (!message) return ''
  if (typeof message.content === 'string' && message.content.trim()) {
    return message.content.trim()
  }
  if (Array.isArray(message.content)) {
    const joined = message.content
      .map((part) => {
        if (typeof part === 'string') return part
        if (part && typeof part === 'object') {
          const obj = part as { type?: unknown; text?: unknown }
          if (obj.type === 'thinking' || obj.type === 'reasoning' || obj.type === 'reasoning_content') {
            return ''
          }
          if (typeof obj.text === 'string') return obj.text
        }
        return ''
      })
      .join('')
      .trim()
    if (joined) return joined
  }
  return ''
}

function hasReasoningWithoutAnswer(payload: unknown): boolean {
  if (typeof payload !== 'object' || payload === null) return false
  const choices = (payload as { choices?: unknown }).choices
  if (!Array.isArray(choices) || choices.length === 0) return false
  const first = choices[0] as { message?: { reasoning_content?: unknown; content?: unknown } } | undefined
  if (!first?.message) return false
  if (typeof first.message.reasoning_content === 'string' && first.message.reasoning_content.trim().length > 0) {
    return true
  }
  if (Array.isArray(first.message.content)) {
    return first.message.content.some(
      part => part && typeof part === 'object'
        && ((part as { type?: unknown }).type === 'thinking' || (part as { type?: unknown }).type === 'reasoning'),
    )
  }
  return false
}

/** 使用原生 `ctx.llm.discoverModels` 拉取临时密钥可见的聊天模型列表（不写入持久化）。 */
export async function discoverOnboardingModelsOnHost(
  ctx: Context,
  request: { readonly provider: string; readonly baseUrl: string; readonly key: string },
  signal?: AbortSignal,
): Promise<OnboardingModelOption[]> {
  const cleanKey = normalizeApiKeyInput(request.key ?? '')
  if (!cleanKey) {
    throw new Error('请先输入 API Key')
  }
  const effectiveBaseUrl = resolveEffectiveBaseUrl(request.provider ?? 'deepseek', request.baseUrl ?? '')
  const safeBaseUrl = validateSetupBaseUrl(effectiveBaseUrl, { allowLoopbackHttp: allowLoopbackHttpOnHost() })

  const llm = ctx.get('llm')
  if (!llm) {
    throw new Error('模型运行时服务未就绪，请稍后再试')
  }

  let discovered: readonly LlmDiscoveredModel[]
  try {
    // 不传 provider 参数：确保即使选择 DeepSeek 也会真正连线验证本次填写的 API Key 与端点
    discovered = await llm.discoverModels(
      LLM_PI_AI_NS,
      {
        baseURL: safeBaseUrl,
        api: 'openai-completions',
        apiKey: cleanKey,
      },
      signal,
    )
  } catch (error: unknown) {
    throw new Error(formatSafeOnboardingError(error, cleanKey))
  }

  // Key 的原样和网址编码两种写法都拦（第 16/17 项复查第 2 轮）
  const keyForms = [...new Set([cleanKey, encodeURIComponent(cleanKey)])]
  const options: OnboardingModelOption[] = []
  const seen = new Set<string>()
  for (const item of discovered) {
    const id = typeof item.id === 'string' ? item.id.trim() : ''
    if (!id || seen.has(id)) continue
    // 服务商若在编号或名字里回显了 Key，整条丢掉，不交给界面、不写进模型配置（第 16/17 项复查）
    const rawName = typeof item.name === 'string' ? item.name : ''
    if (keyForms.some(form => id.includes(form) || rawName.includes(form))) continue
    seen.add(id)
    options.push({
      id,
      name: (typeof item.name === 'string' && item.name.trim()) ? item.name.trim() : id,
      ...(typeof item.contextWindow === 'number' && item.contextWindow > 0 ? { contextWindow: item.contextWindow } : {}),
      ...(typeof item.maxTokens === 'number' && item.maxTokens > 0 ? { maxTokens: item.maxTokens } : {}),
      ...(Array.isArray(item.inputModalities) && item.inputModalities.length > 0
        ? { inputModalities: item.inputModalities }
        : {}),
    })
  }

  if (options.length === 0) {
    throw new Error('服务商已响应，但未返回可用的聊天模型列表')
  }
  return options
}

/** 一次性小回复验证：通过真实兼容 OpenAI HTTP 接口发最小聊天请求，绝不提前保存任何配置或回显响应错误原文。 */
async function probeOneShotReplyOnHost(
  request: OnboardingSetupRequest,
  signal?: AbortSignal,
): Promise<string> {
  const allowLoopback = allowLoopbackHttpOnHost()
  const safeBaseUrl = validateSetupBaseUrl(request.baseUrl, { allowLoopbackHttp: allowLoopback })
  const endpoint = buildOpenAiChatCompletionsUrl(safeBaseUrl)
  validateSetupBaseUrl(endpoint, { allowLoopbackHttp: allowLoopback })

  const modelId = (request.model ?? '').trim()
  if (!modelId) {
    throw new Error('请先选择要使用的模型')
  }

  const controller = new AbortController()
  const timeout = setTimeout(() => { controller.abort() }, PROBE_REPLY_TIMEOUT_MS)
  const onAbort = () => { controller.abort() }
  signal?.addEventListener('abort', onAbort, { once: true })

  try {
    let response: Response
    try {
      response = await fetch(endpoint, {
        method: 'POST',
        redirect: 'manual',
        signal: controller.signal,
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${request.key}`,
        },
        body: JSON.stringify({
          model: modelId,
          messages: [{ role: 'user', content: '请只回复“收到”两个字。' }],
          max_tokens: PROBE_REPLY_MAX_TOKENS,
          temperature: 0,
          stream: false,
        }),
      })
    } catch (error: unknown) {
      throw new Error(formatSafeOnboardingError(error, request.key))
    }

    if (response.status >= 300 && response.status < 400) {
      throw new Error('接口地址返回了重定向响应，已出于安全原因阻止发送密钥，请填写最终接口地址')
    }
    if (!response.ok) {
      // 只按状态码返回大白话原因，绝不读取并抛回可能回显密钥的响应体
      throw new Error(translateHttpStatusMessage(response.status))
    }

    let body: unknown
    try {
      body = await response.json()
    } catch {
      throw new Error('模型接口返回内容不是有效的 JSON 响应')
    }
    const text = extractOpenAiReplyText(body)
    if (!text) {
      if (hasReasoningWithoutAnswer(body)) {
        throw new Error('模型只输出了思考过程，没有返回给用户的实际回复，未保存任何配置')
      }
      throw new Error('模型接口返回成功，但内容为空，未保存配置')
    }
    return text
  } finally {
    clearTimeout(timeout)
    signal?.removeEventListener('abort', onAbort)
  }
}

/** 确保当前处于正式钥匙串模式且挂载的是正式 Mac 钥匙串提供方（兼容文件模式绝不冒充存钥匙串）。 */
function assertMacKeychainProviderActive(ctx: Context): CredentialProvider {
  if (!isKeychainModeEnabled()) {
    throw new Error('当前处于兼容文件凭据模式，未启用 Mac 钥匙串模式（已拒绝以存钥匙串名义写文件）')
  }
  const credentials = ctx.get('credentials')
  if (!isMacCredentialProvider(credentials)) {
    throw new Error('Mac 钥匙串凭据提供方未挂载或当前系统不支持，已拒绝写入配置文件')
  }
  return credentials
}

/**
 * 第 17 项：请求选用了旧版复制过来的 Key 时，核对清单、用清单里的服务商和地址，Key 由主机从钥匙串读出。
 * 没选就原样返回。
 */
export async function resolveLegacyOnboardingRequestOnHost<T extends OnboardingSetupRequest>(ctx: Context, request: T): Promise<T> {
  if (!request.legacyAccount) return request
  const credentials = assertMacKeychainProviderActive(ctx) as unknown as { readLegacyKey?: (account: string) => Promise<string | undefined> }
  if (typeof credentials.readLegacyKey !== 'function') {
    throw new Error('钥匙串凭据提供方不支持读旧版复制过来的 Key')
  }
  const readKey = credentials.readLegacyKey.bind(credentials)
  return await resolveLegacyRequest(request, await readLegacyOptions(), readKey) as T
}

/** 查询当前首次配置 / 模型就绪状态（仅探测自家专用 ref，绝不枚举系统钥匙串）。 */
export async function readOnboardingStateOnHost(ctx: Context): Promise<OnboardingState> {
  if (!isKeychainModeEnabled()) {
    return {
      keychainEnabled: false,
      required: false,
      configured: true,
    }
  }

  const credentials = ctx.get('credentials')
  if (!isMacCredentialProvider(credentials)) {
    return {
      keychainEnabled: true,
      required: true,
      configured: false,
      error: 'Mac 钥匙串凭据服务未就绪（仅支持 macOS 且需启用自有钥匙串提供方）',
    }
  }

  const keyRef = resolveAssistantKeyRef()
  let keyInfo: { configured: boolean; masked?: string }
  try {
    keyInfo = await credentials.describe(keyRef)
  } catch (error: unknown) {
    return {
      keychainEnabled: true,
      required: true,
      configured: false,
      error: redactOnboardingSecret(error instanceof Error ? error.message : String(error)),
    }
  }

  const agentDefaultModel = ctx.get('agentDefaultModel')
  if (!agentDefaultModel) {
    return {
      keychainEnabled: true,
      required: true,
      configured: false,
      error: '默认模型服务尚未就绪',
    }
  }

  const selection = agentDefaultModel.currentSelection()
  const piAi = readPiAiSettingsSnapshot(ctx)
  const providerProfile = piAi.providers[selection.provider]
  const providerKeyRef = typeof providerProfile?.apiKeyEnv === 'string' ? providerProfile.apiKeyEnv : ''
  const baseUrl = typeof providerProfile?.baseURL === 'string' ? providerProfile.baseURL : ''
  const configuredModels = Array.isArray(providerProfile?.models)
    ? providerProfile.models
    : []
  const hasModelInProvider = configuredModels.some(
    item => item && typeof item === 'object' && (item as { id?: unknown }).id === selection.model,
  )

  const configured = keyInfo.configured
    && Boolean(selection.provider)
    && Boolean(selection.model)
    && providerKeyRef === keyRef
    && hasModelInProvider

  return {
    keychainEnabled: true,
    required: !configured,
    configured,
    ...(configured
      ? {
          provider: inferPresetProvider(selection.provider, baseUrl),
          routeId: selection.provider,
          baseUrl,
          model: selection.model,
          ...(keyInfo.masked ? { maskedKey: keyInfo.masked } : {}),
        }
      : {}),
  }
}

/**
 * 完整执行一次超级助手配置：
 * - 校验钥匙串模式处于启用状态；
 * - 拉模型核验名单 → 真实发一句轻量对话核验非空回复；
 * - 持久化 Key 到 Mac 钥匙串 + `llm-pi-ai` providers（保留真实模型元数据）+ `agentDefaultModel` 默认选型；
 * - 任一步骤落盘失败时立即按逆序回滚到修改前一致状态，绝不假装成功。
 */
export async function completeOnboardingOnHost(
  ctx: Context,
  rawRequest: OnboardingSetupRequest,
  signal?: AbortSignal,
): Promise<OnboardingSetupResult> {
  const credentials = assertMacKeychainProviderActive(ctx)
  const settings = ctx.get('settings')
  const agentDefaultModel = ctx.get('agentDefaultModel')
  if (!settings || !agentDefaultModel) {
    throw new Error('模型或设置服务未就绪，无法保存超级玩家配置')
  }

  const keyRef = resolveAssistantKeyRef()
  let discoveredCache: OnboardingModelOption[] = []

  const result = await completeAssistantSetup(
    {
      listModels: async (req) => {
        discoveredCache = await discoverOnboardingModelsOnHost(ctx, req, signal)
        return discoveredCache
      },
      reply: async (req) => probeOneShotReplyOnHost(req, signal),
      persist: async (req) => {
        const { routeId, displayName } = resolveRouteForProvider(req.provider)
        const toPiAiModelEntry = (item: OnboardingModelOption): Record<string, unknown> => ({
          id: item.id,
          name: item.name || item.id,
          ...(item.contextWindow ? { contextWindow: item.contextWindow } : {}),
          ...(item.maxTokens ? { maxTokens: item.maxTokens } : {}),
          ...(item.inputModalities && item.inputModalities.length > 0
            ? { input: [...item.inputModalities] }
            : { input: ['text'] }),
        })
        const discoveredEntries = discoveredCache.map(toPiAiModelEntry)
        const selectedExists = discoveredEntries.some(entry => entry.id === req.model)
        const modelsList = selectedExists
          ? discoveredEntries
          : [
              ...discoveredEntries,
              toPiAiModelEntry({ id: req.model!, name: req.model! }),
            ]

        // 在实际写入任何位置前，先读取旧状态快照用于失败时一致性回滚
        const prevCredential = await credentials.resolve(keyRef)
        const prevPiAi = readPiAiSettingsSnapshot(ctx)
        const hadPrevUserProvider = Object.prototype.hasOwnProperty.call(prevPiAi.userProviders, routeId)
        const prevUserProviderValue = hadPrevUserProvider ? prevPiAi.userProviders[routeId] : undefined
        const prevSelection = agentDefaultModel.currentSelection()

        let credAttempted = false
        let settingsAttempted = false
        let selectionAttempted = false

        try {
          credAttempted = true
          await credentials.set(keyRef, req.key)

          const nextProfile: Record<string, unknown> = {
            displayName,
            apiKeyEnv: keyRef,
            api: 'openai-completions',
            baseURL: req.baseUrl,
            models: modelsList,
          }
          const mutateOp: SettingsPathOp = {
            op: 'set',
            path: ['providers', routeId],
            value: nextProfile,
          }
          settingsAttempted = true
          await settings.mutate(LLM_PI_AI_NS, [mutateOp], prevPiAi.revision)

          selectionAttempted = true
          await agentDefaultModel.saveSelection({
            provider: routeId,
            model: req.model!,
          })
          // 校验默认模型是否真正落盘生效（防止缺失 configEditor 时 saveSelection 静默 return 假成功）
          const verifiedSelection = agentDefaultModel.currentSelection()
          if (verifiedSelection.provider !== routeId || verifiedSelection.model !== req.model) {
            throw new Error('默认模型配置未能实际生效（配置编辑器未就绪或保存被跳过），未完成配置')
          }
        } catch (persistError: unknown) {
          const rollbackFailures: string[] = []
          // 逆序回滚：只要前面发起过写尝试就做恢复，并如实记录每步回滚是否成功
          if (selectionAttempted && prevSelection.provider && prevSelection.model) {
            try {
              await agentDefaultModel.saveSelection(prevSelection)
            } catch {
              rollbackFailures.push('默认模型')
            }
          }
          if (settingsAttempted) {
            try {
              const latestPiAi = readPiAiSettingsSnapshot(ctx)
              const rollbackOp: SettingsPathOp = hadPrevUserProvider
                ? { op: 'set', path: ['providers', routeId], value: prevUserProviderValue }
                : { op: 'unset', path: ['providers', routeId] }
              await settings.mutate(LLM_PI_AI_NS, [rollbackOp], latestPiAi.revision)
            } catch {
              rollbackFailures.push('模型设置')
            }
          }
          if (credAttempted) {
            try {
              if (prevCredential && prevCredential.value.trim().length > 0) {
                await credentials.set(keyRef, prevCredential.value)
              } else {
                await credentials.unset(keyRef)
              }
            } catch {
              rollbackFailures.push('钥匙串凭据')
            }
          }

          const safeMsg = formatSafeOnboardingError(persistError, req.key)
          if (rollbackFailures.length > 0) {
            throw new Error(`保存模型配置失败，且以下状态未能自动回滚（${rollbackFailures.join('、')}），请核查并重试：${safeMsg}`)
          }
          throw new Error(`保存模型配置失败（已自动回滚原状）：${safeMsg}`)
        }
      },
    },
    rawRequest,
    { allowLoopbackHttp: allowLoopbackHttpOnHost() },
  )

  return result
}
