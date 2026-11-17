/**
 * 超级助手首次配置 / 更换模型的纯策略层（浏览器端与 Host 端均可安全复用，不含 Node 依赖）：
 * - 服务商预置（默认 DeepSeek、阿里百炼、腾讯云、其他 OpenAI 兼容）。
 * - 严格 URL 安全校验：生产环境强制 HTTPS，禁止 URL 内含账号密码、query 参数与片段；仅明确测试开关允许回环 http。
 * - `completeAssistantSetup`：严格保证“校 URL → 选模型 → 拉名单校验存在 → 真实小回复校验非空 → 持久化”，
 *   任意环节失败均不持久化，任何返回值与错误信息均抹除密钥原文。
 */

export type SetupProviderId = 'deepseek' | 'dashscope' | 'tencent-tokenhub' | 'tencent' | 'other'

export interface SetupProviderPreset {
  readonly id: SetupProviderId
  readonly label: string
  readonly defaultBaseUrl: string
  readonly routeId: string
  readonly requiresBaseUrlInput: boolean
}

/** 预置服务商列表（顺序固定：默认 DeepSeek、阿里百炼、腾讯云 TokenHub、腾讯混元、其他 OpenAI 兼容）。 */
export const SETUP_PROVIDER_PRESETS: readonly SetupProviderPreset[] = [
  {
    id: 'deepseek',
    label: 'DeepSeek',
    defaultBaseUrl: 'https://api.deepseek.com',
    routeId: 'deepseek',
    requiresBaseUrlInput: false,
  },
  {
    id: 'dashscope',
    label: '阿里百炼',
    defaultBaseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    routeId: 'superagent-dashscope',
    requiresBaseUrlInput: false,
  },
  {
    id: 'tencent-tokenhub',
    label: '腾讯云 TokenHub',
    defaultBaseUrl: 'https://tokenhub.tencentmaas.com/v1',
    routeId: 'superagent-tencent-tokenhub',
    requiresBaseUrlInput: false,
  },
  {
    id: 'tencent',
    label: '腾讯混元',
    defaultBaseUrl: 'https://api.hunyuan.cloud.tencent.com/v1',
    routeId: 'superagent-tencent',
    requiresBaseUrlInput: false,
  },
  {
    id: 'other',
    label: '其他 OpenAI 兼容',
    defaultBaseUrl: '',
    routeId: 'superagent-custom',
    requiresBaseUrlInput: true,
  },
]

export interface OnboardingModelOption {
  readonly id: string
  readonly name: string
  readonly contextWindow?: number | undefined
  readonly maxTokens?: number | undefined
  readonly inputModalities?: readonly ('text' | 'image')[] | undefined
}

export interface OnboardingSetupRequest {
  readonly provider: string
  readonly baseUrl: string
  readonly key: string
  readonly model?: string | undefined
  /** 第 17 项：选用旧版复制过来的 Key 时填条目名（此时 key 为空，由主机读钥匙串）。 */
  readonly legacyAccount?: string | undefined
}

export interface OnboardingSetupResult {
  readonly ok: true
  readonly provider: string
  readonly baseUrl: string
  readonly model: string
}

export interface OnboardingPorts {
  listModels(request: OnboardingSetupRequest): Promise<readonly OnboardingModelOption[]>
  reply(request: OnboardingSetupRequest): Promise<string>
  persist(request: OnboardingSetupRequest): Promise<void>
}

export interface ValidateBaseUrlOptions {
  readonly allowLoopbackHttp?: boolean | undefined
}

export interface CompleteSetupOptions extends ValidateBaseUrlOptions {}

/** 从文本中擦除可能回显的密钥片段（包括原始串、前后去空白串、URL 编码串与常见令牌前缀）。 */
export function redactOnboardingSecret(text: string, secret?: string): string {
  let cleaned = String(text ?? '')
  if (secret) {
    const candidates = new Set<string>([secret, secret.trim()])
    try {
      candidates.add(encodeURIComponent(secret))
      candidates.add(encodeURIComponent(secret.trim()))
    } catch {
      // 忽略异常编码
    }
    for (const candidate of candidates) {
      if (candidate.length >= 2) {
        cleaned = cleaned.split(candidate).join('***')
      }
    }
  }
  cleaned = cleaned.replace(/Bearer\s+[A-Za-z0-9._~+/=-]{4,}/gi, 'Bearer ***')
  cleaned = cleaned.replace(/\bsk-[A-Za-z0-9._-]{4,}/g, 'sk-***')
  return cleaned.replace(/[\r\n\t]+/g, ' ').trim()
}

/** 清理粘贴/输入带来的首尾空白及换行。 */
export function normalizeApiKeyInput(raw: string): string {
  return raw.replace(/[\r\n\u200B-\u200D\uFEFF]/g, '').trim()
}

export function findProviderPreset(provider: string): SetupProviderPreset | undefined {
  const key = provider.trim().toLowerCase()
  return SETUP_PROVIDER_PRESETS.find(item => item.id === key)
}

export function resolveEffectiveBaseUrl(provider: string, baseUrl: string): string {
  const trimmed = baseUrl.trim()
  if (trimmed.length > 0) return trimmed
  const preset = findProviderPreset(provider)
  return preset?.defaultBaseUrl ?? ''
}

function isLoopbackHostname(hostname: string): boolean {
  const normalized = hostname.replace(/^\[|\]$/g, '').toLowerCase()
  return normalized === '127.0.0.1' || normalized === 'localhost' || normalized === '::1'
}

/**
 * 校验服务商接口地址：
 * - 公开地址必须使用 HTTPS；
 * - 严禁地址中夹带用户名/密码、`?` 查询参数或 `#` 片段；
 * - 仅调用方（Host 测试环境）显式传入 `allowLoopbackHttp: true` 且主机名为本机回环地址时允许 `http://`。
 */
export function validateSetupBaseUrl(rawUrl: string, options: ValidateBaseUrlOptions = {}): string {
  const trimmed = typeof rawUrl === 'string' ? rawUrl.trim() : ''
  if (!trimmed) {
    throw new Error('请填写接口地址')
  }
  if (trimmed.includes('?')) {
    throw new Error('接口地址不能包含查询参数（例如 ?api_key=...），请仅填写基础网址')
  }
  if (trimmed.includes('#')) {
    throw new Error('接口地址不能包含 # 片段标识')
  }
  let parsed: URL
  try {
    parsed = new URL(trimmed)
  } catch {
    throw new Error('接口地址格式无效，请填写以 https:// 开头的完整地址')
  }
  if (parsed.username || parsed.password) {
    throw new Error('接口地址不能包含账号或密码信息，请删去后再试')
  }
  if (parsed.search && parsed.search.length > 0) {
    throw new Error('接口地址不能包含查询参数，请删去后再试')
  }
  const allowLoopback = options.allowLoopbackHttp === true
  if (parsed.protocol === 'https:') {
    // 合法 HTTPS
  } else if (parsed.protocol === 'http:') {
    if (!allowLoopback || !isLoopbackHostname(parsed.hostname)) {
      throw new Error('出于安全限制，接口地址必须使用 https://（仅本机受控测试模式允许本机回环 http 地址）')
    }
  } else {
    throw new Error('接口地址协议不受支持，请使用 https://')
  }
  return parsed.toString().replace(/\/+$/, '')
}

/** 按 HTTP 状态码映射大白话错误提示（绝不携带响应体原文，避免泄露回显密钥）。 */
export function translateHttpStatusMessage(status: number): string {
  if (status === 401) {
    return 'API Key 验证未通过（401）：密钥无效、填错或已过期，请核对后重贴'
  }
  if (status === 403) {
    return '访问被服务商拒绝（403）：当前密钥没有调用该接口或模型的权限'
  }
  if (status === 402) {
    return '账户额度或余额不足（402）：请前往服务商控制台检查账户余额或资源包'
  }
  if (status === 429) {
    return '请求过于频繁或已触达限流（429）：请稍等几秒再试，或检查并发配额'
  }
  if (status === 404) {
    return '找不到对应接口或模型路径（404）：请检查接口地址是否填写完整'
  }
  if (status >= 500) {
    return `服务商服务器暂时异常（HTTP ${status}），请稍后再试`
  }
  return `服务商请求被拒绝（HTTP ${status}），请检查服务商、接口地址与密钥配置`
}

/** 将网络或端口错误转换成安全的大白话提示，确保不含密钥或过长系统日志。 */
export function formatSafeOnboardingError(error: unknown, secret?: string): string {
  const raw = error instanceof Error ? error.message : String(error ?? '')
  const redacted = redactOnboardingSecret(raw, secret)
  const lower = redacted.toLowerCase()

  if (/\b401\b|unauthorized|invalid[_\s-]?api[_\s-]?key|authentication/.test(lower)) {
    return translateHttpStatusMessage(401)
  }
  if (/\b403\b|forbidden|permission/.test(lower)) {
    return translateHttpStatusMessage(403)
  }
  if (/\b402\b|payment required|insufficient[_\s-]?(quota|balance|funds)/.test(lower)) {
    return translateHttpStatusMessage(402)
  }
  if (/\b429\b|rate[_\s-]?limit|too many requests/.test(lower)) {
    return translateHttpStatusMessage(429)
  }
  if (/\b(?:502|503|504)\b|service[_\s-]?unavailable|bad[_\s-]?gateway|gateway[_\s-]?timeout/.test(lower)) {
    return '服务商暂时不可用，请稍后重新粘贴 Key'
  }
  if (/econnrefused|enotfound|etimedout|eai_again|fetch failed|network|timed out|timeout|aborted|socket hang up/.test(lower)) {
    return '无法连接到服务商接口（网络超时或地址不可达），请检查网络或接口地址是否正确'
  }

  return redacted || '配置验证失败，请稍后重试'
}

/**
 * 超级助手配置确认核心流程：
 * 1. 先检查接口地址安全性与必填项（Key 非空、已明确选模型）；
 * 2. 拉取可用模型名单并核实所选模型在名单内；
 * 3. 发送一句真实的轻量聊天请求，确认收到非空回复；
 * 4. 最后执行持久化（写入钥匙串 + 模型配置），返回值绝不含密钥。
 */
export async function completeAssistantSetup(
  ports: OnboardingPorts,
  request: OnboardingSetupRequest,
  options: CompleteSetupOptions = {},
): Promise<OnboardingSetupResult> {
  const rawKey = request.key ?? ''
  const cleanKey = normalizeApiKeyInput(rawKey)
  if (!cleanKey) {
    throw new Error('请先输入 API Key')
  }

  const provider = (request.provider ?? '').trim() || 'deepseek'
  const rawBaseUrl = resolveEffectiveBaseUrl(provider, request.baseUrl ?? '')
  const safeBaseUrl = validateSetupBaseUrl(rawBaseUrl, options)

  const selectedModel = (request.model ?? '').trim()
  if (!selectedModel) {
    throw new Error('请先选择一个聊天模型后再确认')
  }

  const cleanRequest: OnboardingSetupRequest = {
    provider,
    baseUrl: safeBaseUrl,
    key: cleanKey,
    model: selectedModel,
  }

  try {
    const models = await ports.listModels(cleanRequest)
    const found = models.some(item => item.id === selectedModel)
    if (!found) {
      throw new Error('所选模型不在当前服务商返回的可用模型名单内，请重新选择')
    }

    const replyText = await ports.reply(cleanRequest)
    if (typeof replyText !== 'string' || replyText.trim().length === 0) {
      throw new Error('模型试聊未收到有效回复，未保存任何配置')
    }

    await ports.persist(cleanRequest)

    return {
      ok: true,
      provider,
      baseUrl: safeBaseUrl,
      model: selectedModel,
    }
  } catch (error: unknown) {
    const safeMessage = redactOnboardingSecret(
      error instanceof Error ? error.message : String(error ?? ''),
      rawKey,
    )
    const doubleSafe = redactOnboardingSecret(safeMessage, cleanKey)
    throw new Error(doubleSafe || '验证或保存配置失败，未写入钥匙串')
  }
}
