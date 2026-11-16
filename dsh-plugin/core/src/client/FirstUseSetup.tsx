/**
 * 超级助手首次使用 / 更换模型配置引导界面：
 * - 仅先配超级助手，不要求先配调度员；
 * - 预置 DeepSeek（默认）、阿里百炼、腾讯云与“其他 OpenAI 兼容”；
 * - Key 输入自动清理粘贴空白、password 输入框配合不可反推的安全遮罩预览；
 * - 防抖自动拉取可用聊天模型列表（固定高度可滚动），默认不代选模型；
 * - 切换服务商时清空 Key 与模型，且丢弃过期异步验证响应；
 * - 确认按钮在选定模型前禁用并防连点；成功后直接触发 `onDone`，不另出成功卡片。
 */
import { useEffect, useId, useRef, useState, type ClipboardEvent, type ReactElement } from 'react'
import { Button } from '@deepseek-ai/dsh-client-ui-primitives'
import {
  SETUP_PROVIDER_PRESETS,
  findProviderPreset,
  formatSafeOnboardingError,
  normalizeApiKeyInput,
  resolveEffectiveBaseUrl,
  type OnboardingModelOption,
  type OnboardingSetupRequest,
  type SetupProviderId,
} from '../onboarding.ts'
import type { LegacyKeyOption } from '../types.ts'
import css from './FirstUseSetup.module.css'

export interface FirstUseSetupProps {
  readonly loadModels: (request: OnboardingSetupRequest) => Promise<readonly OnboardingModelOption[]>
  readonly complete: (request: OnboardingSetupRequest) => Promise<void>
  readonly onDone: () => void | Promise<void>
  readonly debounceMs?: number | undefined
  readonly canCancel?: boolean | undefined
  readonly onCancel?: (() => void) | undefined
  readonly initialProvider?: string | undefined
  readonly initialBaseUrl?: string | undefined
  readonly existingMaskedKey?: string | undefined
  /** 第 17 项：旧版复制过来的模型 Key（只有条目名，没有密钥）。 */
  readonly legacyOptions?: readonly LegacyKeyOption[] | undefined
}

/** 浏览器端纯遮罩预览函数（不引 Node 模块，短 Key 全遮，长 Key 仅留极短首尾）。 */
function maskPreview(value: string): string {
  if (!value) return ''
  const chars = Array.from(value)
  const hidden = chars.length - 7
  if (hidden < 8) return '•'.repeat(Math.max(4, chars.length))
  return `${chars.slice(0, 3).join('')}${'•'.repeat(hidden)}${chars.slice(-4).join('')}`
}

function initialProviderOf(raw?: string): SetupProviderId {
  const matched = raw ? findProviderPreset(raw) : undefined
  return matched ? matched.id : 'deepseek'
}

export function FirstUseSetup(props: FirstUseSetupProps): ReactElement {
  const initialProvider = initialProviderOf(props.initialProvider)
  const initialPreset = findProviderPreset(initialProvider)

  const [provider, setProvider] = useState<SetupProviderId>(initialProvider)
  const [baseUrl, setBaseUrl] = useState<string>(
    props.initialBaseUrl?.trim()
      ? props.initialBaseUrl.trim()
      : (initialPreset?.defaultBaseUrl ?? ''),
  )
  const [apiKey, setApiKey] = useState('')
  const [legacy, setLegacy] = useState<LegacyKeyOption | null>(null)
  const [models, setModels] = useState<readonly OnboardingModelOption[]>([])
  const [selectedModel, setSelectedModel] = useState('')
  const [loadingModels, setLoadingModels] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [errorText, setErrorText] = useState('')

  const baseUrlInputId = useId()
  const keyInputId = useId()
  const generationRef = useRef(0)
  const submittingRef = useRef(false)
  const loadModelsRef = useRef(props.loadModels)
  loadModelsRef.current = props.loadModels

  const preset = findProviderPreset(provider)
  const requiresBaseUrl = preset?.requiresBaseUrlInput === true
  const effectiveBaseUrl = resolveEffectiveBaseUrl(provider, baseUrl)
  const debounceDelay = props.debounceMs ?? 400

  const handleSelectProvider = (nextProvider: SetupProviderId) => {
    if (nextProvider === provider) return
    generationRef.current += 1
    const nextPreset = findProviderPreset(nextProvider)
    setProvider(nextProvider)
    setBaseUrl(nextPreset?.defaultBaseUrl ?? '')
    setLegacy(null)
    setApiKey('')
    setModels([])
    setSelectedModel('')
    setLoadingModels(false)
    setErrorText('')
  }

  const handleKeyChange = (raw: string) => {
    const clean = normalizeApiKeyInput(raw)
    generationRef.current += 1
    setApiKey(clean)
    setModels([])
    setSelectedModel('')
    setErrorText('')
  }

  const resetVerification = () => {
    generationRef.current += 1
    setModels([])
    setSelectedModel('')
    setLoadingModels(false)
    setErrorText('')
  }

  const handleSelectLegacy = (option: LegacyKeyOption) => {
    resetVerification()
    setLegacy(option)
    setProvider(option.provider)
    setBaseUrl(option.baseUrl)
    setApiKey('')
  }

  const handleDropLegacy = () => {
    resetVerification()
    const fallback = findProviderPreset(provider)
    setLegacy(null)
    setBaseUrl(fallback?.defaultBaseUrl ?? '')
  }

  const handleKeyPaste = (event: ClipboardEvent<HTMLInputElement>) => {
    const pasted = event.clipboardData?.getData('text')
    if (pasted !== undefined) {
      event.preventDefault()
      handleKeyChange(pasted)
    }
  }

  const handleBaseUrlChange = (raw: string) => {
    generationRef.current += 1
    setBaseUrl(raw.trim())
    setModels([])
    setSelectedModel('')
    setErrorText('')
  }

  useEffect(() => {
    const cleanKey = normalizeApiKeyInput(apiKey)
    if (!cleanKey && !legacy) {
      setLoadingModels(false)
      return
    }
    if (requiresBaseUrl && !effectiveBaseUrl) {
      setLoadingModels(false)
      return
    }

    const requestGeneration = ++generationRef.current
    const requestPayload: OnboardingSetupRequest = legacy
      ? { provider, baseUrl: effectiveBaseUrl, key: '', legacyAccount: legacy.account }
      : { provider, baseUrl: effectiveBaseUrl, key: cleanKey }

    const timer = setTimeout(() => {
      if (requestGeneration !== generationRef.current) return
      setLoadingModels(true)
      setErrorText('')
      void loadModelsRef.current(requestPayload)
        .then((list) => {
          if (requestGeneration !== generationRef.current) return
          const normalized = Array.isArray(list) ? list.filter(item => item && item.id) : []
          setModels(normalized)
          // 保持默认不选：即使名单里只有一个模型也不擅自代选
          setSelectedModel('')
          if (normalized.length === 0) {
            setErrorText('服务商未返回可用的聊天模型，请检查接口地址或账号权限')
          }
        })
        .catch((error: unknown) => {
          if (requestGeneration !== generationRef.current) return
          setModels([])
          setSelectedModel('')
          setErrorText(formatSafeOnboardingError(error, cleanKey))
        })
        .finally(() => {
          if (requestGeneration === generationRef.current) {
            setLoadingModels(false)
          }
        })
    }, debounceDelay)

    return () => {
      clearTimeout(timer)
    }
  }, [provider, effectiveBaseUrl, requiresBaseUrl, apiKey, legacy, debounceDelay])

  const handleConfirm = async () => {
    if (submittingRef.current) return
    const cleanKey = normalizeApiKeyInput(apiKey)
    if ((!cleanKey && !legacy) || !selectedModel || loadingModels) return

    submittingRef.current = true
    setSubmitting(true)
    setErrorText('')
    try {
      await props.complete(legacy
        ? { provider, baseUrl: effectiveBaseUrl, key: '', legacyAccount: legacy.account, model: selectedModel }
        : { provider, baseUrl: effectiveBaseUrl, key: cleanKey, model: selectedModel })
      await props.onDone()
    } catch (error: unknown) {
      setErrorText(formatSafeOnboardingError(error, cleanKey))
    } finally {
      submittingRef.current = false
      setSubmitting(false)
    }
  }

  const maskedPreview = apiKey ? maskPreview(apiKey) : (props.existingMaskedKey || '')

  return (
    <div className={css.wrap}>
      <div className={css.header}>
        <h2 className={css.title}>配置超级助手模型</h2>
        <p className={css.subtitle}>
          先选好超级助手使用的模型服务商并验证密钥，即可直接开始聊天（需要用调度员干活时再单独接）。
        </p>
      </div>

      {props.legacyOptions && props.legacyOptions.length > 0 ? (
        <div className={css.field}>
          <span className={css.label}>旧版复制过来的 Key（选一个就不用再填）</span>
          <div className={css.providerGroup} role="group" aria-label="旧版复制过来的 Key">
            {props.legacyOptions.map((item) => {
              const active = legacy?.account === item.account
              return (
                <button
                  key={item.account}
                  type="button"
                  aria-pressed={active}
                  disabled={submitting}
                  className={`${css.providerBtn ?? ''} ${active ? (css.providerBtnActive ?? '') : ''}`}
                  onClick={() => { handleSelectLegacy(item) }}
                >
                  用旧版的{item.label}
                </button>
              )
            })}
          </div>
          <div className={css.hint}>第一次用时 Mac 可能问一次能否读取钥匙串，点“始终允许”。</div>
        </div>
      ) : null}

      <div className={css.field}>
        <span className={css.label}>模型服务商</span>
        <div className={css.providerGroup} role="group" aria-label="模型服务商">
          {SETUP_PROVIDER_PRESETS.map((item) => {
            const active = item.id === provider
            return (
              <button
                key={item.id}
                type="button"
                aria-pressed={active}
                disabled={submitting}
                className={`${css.providerBtn ?? ''} ${active ? (css.providerBtnActive ?? '') : ''}`}
                onClick={() => { handleSelectProvider(item.id) }}
              >
                {item.label}
              </button>
            )
          })}
        </div>
        {!requiresBaseUrl && effectiveBaseUrl ? (
          <div className={css.hint}>默认接口：{effectiveBaseUrl}</div>
        ) : null}
      </div>

      {requiresBaseUrl && !legacy ? (
        <div className={css.field}>
          <label className={css.label} htmlFor={baseUrlInputId}>接口地址</label>
          <input
            id={baseUrlInputId}
            aria-label="接口地址"
            type="url"
            className={css.input}
            placeholder="https://api.example.com/v1"
            value={baseUrl}
            disabled={submitting}
            onChange={(event) => { handleBaseUrlChange(event.target.value) }}
          />
        </div>
      ) : null}

      {legacy ? (
        <div className={css.field}>
          <span className={css.label}>API Key</span>
          <div className={css.hint}>使用旧版复制过来的 Key：{legacy.label}（接口 {legacy.baseUrl}）</div>
          <div>
            <Button size="sm" variant="outline" disabled={submitting} onClick={handleDropLegacy}>
              旧版 Key 不用了，自己填
            </Button>
          </div>
        </div>
      ) : (
      <div className={css.field}>
        <label className={css.label} htmlFor={keyInputId}>API Key</label>
        <input
          id={keyInputId}
          aria-label="API Key"
          type="password"
          autoComplete="off"
          spellCheck={false}
          className={css.input}
          placeholder="粘贴你的 API Key（将自动清理多余空格与换行）"
          value={apiKey}
          disabled={submitting}
          onPaste={handleKeyPaste}
          onChange={(event) => { handleKeyChange(event.target.value) }}
        />
        {maskedPreview ? (
          <div className={css.preview} aria-label="安全遮罩预览">
            <span>安全预览</span>
            <span>{maskedPreview}</span>
          </div>
        ) : null}
      </div>
      )}

      {loadingModels ? (
        <div className={css.statusText}>正在向服务商验证并读取可用模型…</div>
      ) : null}

      {models.length > 0 ? (
        <div className={css.field}>
          <span className={css.label}>选择聊天模型（请亲自点选）</span>
          <div className={css.modelList} role="radiogroup" aria-label="可用模型">
            {models.map((item) => {
              const checked = selectedModel === item.id
              return (
                <label
                  key={item.id}
                  className={`${css.modelRow ?? ''} ${checked ? (css.modelRowActive ?? '') : ''}`}
                >
                  <input
                    type="radio"
                    name="superagent-onboarding-model"
                    value={item.id}
                    checked={checked}
                    disabled={submitting}
                    onChange={() => {
                      setSelectedModel(item.id)
                      setErrorText('')
                    }}
                  />
                  <span>{item.name}</span>
                </label>
              )
            })}
          </div>
        </div>
      ) : null}

      {errorText ? (
        <div className={css.errorBox} role="alert">{errorText}</div>
      ) : null}

      <p className={css.securityNote}>
        隐私说明：你的 API Key 只会保存在本机钥匙串中，不会写进聊天记录、配置文件或项目文件；验证时仅向当前所选服务商发送接口校验与试聊请求。
      </p>

      <div className={css.footer}>
        {props.canCancel && props.onCancel ? (
          <Button
            size="md"
            variant="outline"
            disabled={submitting}
            onClick={props.onCancel}
          >
            取消
          </Button>
        ) : null}
        <Button
          size="md"
          variant="primary"
          disabled={!selectedModel || loadingModels || submitting}
          onClick={() => { void handleConfirm() }}
        >
          {submitting ? '正在验证并保存…' : '确认'}
        </Button>
      </div>
    </div>
  )
}
