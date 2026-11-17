/**
 * 06 / 06a：设置里的正式“积木库”页。
 * - 正式限制模式按 06a 分三组展示：大脑（按量付费接口）/ 干活的（订阅的命令行工具）/ 工具（命令行工具、MCP、接口）；
 * - 非 keychain 兼容分支保留原“角色”卡片、模型导航回退与安全说明，不强走钥匙串引导；
 * - 调度员“接一个 / 更换”不另造配置表单：关闭设置并新建 superagent-chat 会话，通过标准输入控制器填入话术交给对话。
 */
import { useEffect, useRef, useState, type ReactElement } from 'react'
import { Button, Tag } from '@deepseek-ai/dsh-client-ui-primitives'
import type { RemoteResult } from '@deepseek-ai/dsh-api-remotes/client'
import type { ModelCatalog } from '@deepseek-ai/dsh-api-session-controller/types'
import type { SettingsSectionOwnerProps } from '@deepseek-ai/dsh-client-ui-settings/client'
import type { BlockRow, DispatcherState } from '../types.ts'
import css from './BlocksSettingsSection.module.css'

export interface BlocksSettingsInjected {
  readonly dispatcherState: () => Promise<RemoteResult<DispatcherState>>
  readonly modelCatalog: () => Promise<RemoteResult<ModelCatalog>>
  readonly startDispatcherChat: (mode: 'connect' | 'change', currentLabel: string) => Promise<void>
  readonly openAssistantSetup?: () => boolean
  readonly useRestrictedSettings?: () => boolean
  /** 06：新版自己积木库里的真实零件（干活的、工具）。 */
  readonly blocks?: () => Promise<RemoteResult<BlockRow[]>>
  /** 06 一键检测：对调度员做一次真实检测（不改分工）。 */
  readonly recheckDispatcher?: () => Promise<RemoteResult<DispatcherState>>
  /** 06 一键检测：检测一块零件。 */
  readonly checkBlock?: (id: string) => Promise<RemoteResult<{ ok: boolean; reason: string }>>
  /** 06 一键检测：拿存着的 Key 真连一次超级玩家正在用的大脑（复查第 1 轮中等 9）。 */
  readonly checkAssistantModel?: () => Promise<RemoteResult<{ ok: boolean; reason: string }>>
}

type CheckMark = { readonly state: 'checking' } | { readonly state: 'done'; readonly ok: boolean; readonly reason: string }

export type BlocksSettingsSectionProps = SettingsSectionOwnerProps & BlocksSettingsInjected

interface ResolvedModelInfo {
  readonly configured: boolean
  readonly providerLabel: string
  readonly modelLabel: string
  readonly legacySummary: string
}

type ModelLoadState =
  | { readonly status: 'loading' }
  | { readonly status: 'error' }
  | ({ readonly status: 'ready' } & ResolvedModelInfo)

function useDefaultRestrictedSettings(): boolean {
  return true
}

function resolveDefaultModelInfo(catalog: ModelCatalog): ResolvedModelInfo {
  const def = catalog.default
  if (!def || (!def.provider && !def.model)) {
    return {
      configured: false,
      providerLabel: '',
      modelLabel: '',
      legacySummary: '暂未配置默认模型',
    }
  }
  const group = catalog.groups.find(g => g.id === def.provider)
  const modelMeta = group?.models.find(m => m.id === def.model)
  const providerLabel = group?.name || def.provider
  const modelLabel = modelMeta?.name || def.model
  const effort = def.reasoningEffort ? ` · 思考档位：${def.reasoningEffort}` : ''
  return {
    configured: true,
    providerLabel,
    modelLabel,
    legacySummary: `${providerLabel} / ${modelLabel}${effort}`,
  }
}

export function BlocksSettingsSection(props: BlocksSettingsSectionProps): ReactElement {
  const useRestricted = props.useRestrictedSettings ?? useDefaultRestrictedSettings
  const restricted = useRestricted()
  const rootRef = useRef<HTMLDivElement | null>(null)
  const [dispatcher, setDispatcher] = useState<DispatcherState | null>(null)
  const [modelState, setModelState] = useState<ModelLoadState>({ status: 'loading' })
  const [launching, setLaunching] = useState(false)
  const [actionError, setActionError] = useState('')
  const [blockRows, setBlockRows] = useState<readonly BlockRow[]>([])
  const [blocksError, setBlocksError] = useState('')
  const [checking, setChecking] = useState(false)
  const [marks, setMarks] = useState<Readonly<Record<string, CheckMark>>>({})

  useEffect(() => {
    let alive = true
    void props.dispatcherState().then((res) => {
      if (!alive) return
      if (res.ok) setDispatcher(res.value)
      else setDispatcher({ status: 'unknown', tool: '', label: '', model: '', reason: res.error.message, verified: false })
    }).catch(() => {})

    void props.modelCatalog().then((res) => {
      if (!alive) return
      if (res.ok) setModelState({ status: 'ready', ...resolveDefaultModelInfo(res.value) })
      else setModelState({ status: 'error' })
    }).catch(() => {
      if (alive) setModelState({ status: 'error' })
    })

    if (props.blocks) {
      void props.blocks().then((res) => {
        if (!alive) return
        if (res.ok) setBlockRows(res.value)
        else setBlocksError(res.error.message || '读不到积木库')
      }).catch((error: unknown) => {
        if (alive) setBlocksError(error instanceof Error ? error.message : String(error))
      })
    }
    return () => { alive = false }
  }, [])

  const isDispatcherReady = dispatcher?.status === 'ready'
  const dispatcherLabel = dispatcher?.label || (dispatcher?.tool === 'codex' ? 'Codex' : dispatcher?.tool || '')
  const isModelConfigured = modelState.status === 'ready' && modelState.configured
  const formalModelTitle = modelState.status === 'loading'
    ? '正在读取当前模型…'
    : modelState.status === 'error'
      ? '暂时读不到当前模型信息'
      : modelState.configured
        ? [modelState.providerLabel, modelState.modelLabel].filter(Boolean).join(' · ')
        : '暂未配置默认模型'
  const legacyAssistantModel = modelState.status === 'loading'
    ? '正在读取当前模型…'
    : modelState.status === 'error'
      ? '暂时读不到当前模型信息'
      : `当前默认模型：${modelState.legacySummary}`

  const handleAssistantChangeClick = () => {
    setActionError('')
    if (props.openAssistantSetup && props.openAssistantSetup()) {
      props.close()
      return
    }
    if (restricted) {
      setActionError('暂时打不开模型设置，请稍后重试')
      return
    }
    const modal = rootRef.current?.closest('[data-shortcut-modal="settings"]')
    const navButtons = modal?.querySelectorAll('nav button')
    if (navButtons) {
      for (const btn of Array.from(navButtons)) {
        const text = (btn.textContent ?? '').trim()
        if (text === '模型' || text === 'Models' || text.startsWith('模型')) {
          ;(btn as HTMLButtonElement).click()
          return
        }
      }
    }
    setActionError('没有找到模型设置入口，请点左侧栏的「模型」手动切换')
  }

  const launchDispatcherChat = async (mode: 'connect' | 'change', currentLabel: string) => {
    if (launching) return
    setLaunching(true)
    setActionError('')
    try {
      props.close()
      await props.startDispatcherChat(mode, currentLabel)
    } catch (error: unknown) {
      setLaunching(false)
      setActionError(error instanceof Error ? error.message : String(error))
    }
  }

  const checkAll = async () => {
    if (checking) return
    setChecking(true)
    const start: Record<string, CheckMark> = {}
    const checkBrain = Boolean(props.checkAssistantModel && isModelConfigured)
    if (checkBrain) start['__brain'] = { state: 'checking' }
    if (props.recheckDispatcher && dispatcher?.tool) start['__dispatcher'] = { state: 'checking' }
    if (props.checkBlock) for (const row of blockRows) start[row.id] = { state: 'checking' }
    setMarks(start)
    const failMark = (error: unknown): CheckMark => ({ state: 'done', ok: false, reason: error instanceof Error ? error.message : String(error) })
    try {
      // 大脑和调度员同时测，零件一个一个测
      await Promise.all([
        checkBrain ? props.checkAssistantModel!().then((r) => {
          setMarks(m => ({ ...m, __brain: r.ok ? { state: 'done', ok: r.value.ok, reason: r.value.reason } : { state: 'done', ok: false, reason: r.error.message } }))
        }).catch((error: unknown) => { setMarks(m => ({ ...m, __brain: failMark(error) })) }) : Promise.resolve(),
        props.recheckDispatcher && dispatcher?.tool ? props.recheckDispatcher().then((r) => {
          if (r.ok) setDispatcher(r.value)
          setMarks(m => ({ ...m, __dispatcher: { state: 'done', ok: r.ok && r.value.status === 'ready', reason: r.ok ? r.value.reason : r.error.message } }))
        }).catch((error: unknown) => { setMarks(m => ({ ...m, __dispatcher: failMark(error) })) }) : Promise.resolve(),
      ])
      if (props.checkBlock) {
        for (const row of blockRows) {
          try {
            const r = await props.checkBlock(row.id)
            const mark: CheckMark = r.ok ? { state: 'done', ok: r.value.ok, reason: r.value.reason } : { state: 'done', ok: false, reason: r.error.message }
            setMarks(m => ({ ...m, [row.id]: mark }))
          } catch (error: unknown) {
            setMarks(m => ({ ...m, [row.id]: { state: 'done', ok: false, reason: error instanceof Error ? error.message : String(error) } }))
          }
        }
      }
    } finally {
      setChecking(false)
    }
  }

  const statusTag = (id: string, fallback: BlockRow['status'] | 'ready' | 'missing', fallbackReason: string): ReactElement => {
    const mark = marks[id]
    if (mark?.state === 'checking') return <Tag tone="info">检测中</Tag>
    const ok = mark ? mark.ok : fallback === 'ok' || fallback === 'ready'
    const reason = mark ? mark.reason : fallbackReason
    if (ok) return <Tag tone="success">已连接</Tag>
    if (!mark && fallback === 'never') return <Tag>没检测过</Tag>
    return <span className={css.rowError}>{reason || '没通过'}</span>
  }

  const blockLine = (row: BlockRow, meta: string): ReactElement => (
    <div key={row.id} className={css.row}>
      <div className={css.rowMain}>
        <div className={css.rowName}>{row.name}</div>
        <div className={css.rowMeta}>{meta}</div>
      </div>
      <div className={css.rowActions}>{statusTag(row.id, row.status, row.reason)}</div>
    </div>
  )
  const usedText = (n: number) => (n > 0 ? `被 ${n} 张能力卡用到` : '没被用到')
  const brainRows = blockRows.filter(row => row.group === 'brain')
  const workerRows = blockRows.filter(row => row.group === 'worker')
  const toolRows = blockRows.filter(row => row.group === 'tool')

  if (restricted) {
    return (
      <div ref={rootRef} className={css.section}>
        <div className={css.headerRow}>
          <div className={css.header}>
            <div className={css.titleRow}>
              <h2 className={css.title}>积木库</h2>
              <small className={css.titleTag}>基础层 · 零件</small>
            </div>
            <p className={css.intro}>
              超级玩家能用的所有零件。要接新的，直接对超级玩家说。
            </p>
          </div>
          <div className={css.headerActions}>
            <Button size="sm" variant="outline" disabled={checking} onClick={() => { void checkAll() }}>
              {checking ? '检测中…' : '一键检测全部'}
            </Button>
            <Button
              size="sm"
              variant="primary"
              disabled={launching}
              onClick={() => { void launchDispatcherChat('connect', '') }}
            >
              + 让超级玩家帮我接一个
            </Button>
          </div>
        </div>
        {blocksError ? <div className={css.rowError} role="alert">{`读不到积木库：${blocksError}`}</div> : null}

        <section className={css.group} aria-label="大脑（按量付费接口）">
          <h3 className={css.groupTitle}>大脑（按量付费接口）</h3>
          <div className={css.card}>
            <div className={css.row}>
              <div className={css.rowMain}>
                <div className={css.rowName}>{formalModelTitle}</div>
                <div className={css.rowMeta}>超级玩家在用</div>
              </div>
              <div className={css.rowActions}>
                {isModelConfigured ? statusTag('__brain', 'ok', '') : null}
                <Button size="sm" variant="outline" onClick={handleAssistantChangeClick}>
                  更换
                </Button>
              </div>
            </div>
            {brainRows.map(row => blockLine(row, `按量付费 · ${usedText(row.usedBy)}`))}
          </div>
        </section>

        <section className={css.group} aria-label="干活的（订阅的命令行工具）">
          <h3 className={css.groupTitle}>干活的（订阅的命令行工具）</h3>
          <div className={css.card}>
            {isDispatcherReady ? (
              <div className={css.row}>
                <div className={css.rowMain}>
                  <div className={css.rowName}>
                    {dispatcherLabel}
                    {dispatcher?.model ? ` · ${dispatcher.model}` : ''}
                  </div>
                  <div className={css.rowMeta}>项目调度员</div>
                </div>
                <div className={css.rowActions}>
                  {statusTag('__dispatcher', 'ready', '')}
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={launching}
                    onClick={() => { void launchDispatcherChat('change', dispatcherLabel) }}
                  >
                    更换
                  </Button>
                </div>
              </div>
            ) : dispatcher?.tool ? (
              <div className={css.row}>
                <div className={css.rowMain}>
                  {marks['__dispatcher'] ? (
                    <div className={css.rowName}>{dispatcherLabel}</div>
                  ) : (
                    <div className={css.rowError}>
                      {dispatcherLabel} 未测通：{dispatcher.reason || '请先安装/登录后重新接入'}
                    </div>
                  )}
                </div>
                {marks['__dispatcher'] ? <div className={css.rowActions}>{statusTag('__dispatcher', 'missing', dispatcher.reason)}</div> : null}
                <div className={css.rowActions}>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={launching}
                    onClick={() => { void launchDispatcherChat('change', dispatcherLabel) }}
                  >
                    更换
                  </Button>
                </div>
              </div>
            ) : workerRows.length === 0 ? (
              <div className={css.emptyRow}>还没有</div>
            ) : null}
            {workerRows.map(row => blockLine(row, `订阅 · ${usedText(row.usedBy)}`))}
          </div>
        </section>

        <section className={css.group} aria-label="工具（命令行工具、MCP、接口）">
          <h3 className={css.groupTitle}>工具（命令行工具、MCP、接口）</h3>
          <div className={css.card}>
            {toolRows.length === 0 ? <div className={css.emptyRow}>还没有</div> : null}
            {toolRows.map(row => blockLine(row, usedText(row.usedBy)))}
          </div>
        </section>

        {actionError ? <div className={css.rowError} role="alert">{actionError}</div> : null}
      </div>
    )
  }

  // 非 keychain 兼容分支保留原展示与模型导航回退
  return (
    <div ref={rootRef} className={css.section}>
      <div className={css.header}>
        <h2 className={css.title}>积木库</h2>
        <p className={css.intro}>
          管理超级玩家与项目调度员。需要接一个或更换调度员时，会新开一段对话，在对话里出示确认卡后检测启用。
        </p>
      </div>

      <section className={css.group} aria-label="角色">
        <h3 className={css.groupTitle}>角色</h3>
        <div className={css.card}>
          <div className={css.row}>
            <div className={css.rowMain}>
              <div className={css.rowTitleLine}>
                <span className={css.rowName}>超级玩家</span>
                <Tag>使用当前默认模型</Tag>
              </div>
              <div className={css.rowDesc}>前台搭档：陪你聊需求、出确认卡、盯每步进度。</div>
              <div className={css.rowMeta}>{legacyAssistantModel}</div>
            </div>
            <div className={css.rowActions}>
              <Button size="sm" variant="outline" onClick={handleAssistantChangeClick}>
                更换
              </Button>
            </div>
          </div>

          <div className={css.row}>
            <div className={css.rowMain}>
              <div className={css.rowTitleLine}>
                <span className={css.rowName}>调度员</span>
                {isDispatcherReady ? <Tag>已接好</Tag> : <Tag>还没接</Tag>}
              </div>
              <div className={css.rowDesc}>后台总控：负责写方案、拆步骤，并安排后续测试与复查。</div>
              {isDispatcherReady ? (
                <div className={css.rowMeta}>
                  当前使用：{dispatcherLabel}
                  {dispatcher?.model ? `（${dispatcher.model}）` : ''}
                </div>
              ) : (
                <div className={dispatcher?.tool ? css.rowError : css.rowMeta}>
                  {dispatcher?.tool
                    ? `${dispatcherLabel || dispatcher.tool} 未测通：${dispatcher.reason || '请先安装/登录后重新接入'}`
                    : '还没有接调度员（支持订阅工具 Codex 或 agy）'}
                </div>
              )}
            </div>
            <div className={css.rowActions}>
              <Button
                size="sm"
                variant={isDispatcherReady ? 'outline' : 'primary'}
                disabled={launching}
                onClick={() => { void launchDispatcherChat(isDispatcherReady ? 'change' : 'connect', dispatcherLabel) }}
              >
                {launching ? '正在打开对话…' : isDispatcherReady ? '更换' : '接一个'}
              </Button>
            </div>
          </div>
        </div>
        {actionError ? <div className={css.rowError} role="alert">{actionError}</div> : null}
      </section>

      <div className={css.hintCard}>
        安全说明：调度员设置只保存在新版的积木库与当前项目设置里，只支持本机已安装的 Codex 或 agy，不会读取或改写你原有的积木库和钥匙串密钥。
      </div>
    </div>
  )
}
