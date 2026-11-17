/**
 * 设置整页（页面对齐改版第六批，对照 06 / 07 / 08 / 15 / 16）。
 * - 点左下“设置”：中间换成这一页（左边分区列 + 右边内容），不再弹框；
 * - 五个分区都露出来（用户 11-16 晚）：业务流程只读显示现在的开发流程（编辑、新增流程以后做）；
 *   定时任务只有框架和说明（功能二期）；能力层、积木库读新版自己积木库的真实资料；
 * - 花费不做（用户 11-16 晚“花费这个功能先不开发”）。
 */
import { useEffect, useState, useSyncExternalStore, type ReactElement, type ReactNode } from 'react'
import { Button, Tag } from '@deepseek-ai/dsh-client-ui-primitives'
import type { RemoteResult } from '@deepseek-ai/dsh-api-remotes/client'
import type { AbilityRow, CardRow, RecordsInfo, StoredKey, TeamInfo, TeamOption, TeamPick, TeamRole, WorkflowInfo } from '../types.ts'
import css from './SettingsPage.module.css'

export type SettingsSectionId = 'flow' | 'abilities' | 'blocks' | 'schedule' | 'general'
export type ThemeChoice = 'light' | 'dark' | 'system'

export interface SettingsThemePort {
  readonly get: () => string
  readonly set: (id: ThemeChoice) => void
  readonly subscribe: (listener: () => void) => () => void
}

export interface SettingsPageInjected {
  readonly abilities: () => Promise<RemoteResult<AbilityRow[]>>
  readonly blocks: () => Promise<RemoteResult<unknown>>
  readonly team: () => Promise<RemoteResult<TeamInfo>>
  /** 过渡期：换某一步谁来跑（下一张任务卡起生效）。 */
  readonly saveTeamRole: (role: TeamRole, tool: string, model: string, effort: string) => Promise<RemoteResult<TeamInfo>>
  readonly workflow: () => Promise<RemoteResult<WorkflowInfo>>
  readonly cards: () => Promise<RemoteResult<CardRow[]>>
  readonly storedKeys: () => Promise<RemoteResult<StoredKey[]>>
  readonly deleteKey: (id: string) => Promise<RemoteResult<boolean>>
  readonly records: () => Promise<RemoteResult<RecordsInfo>>
  readonly openRecords: () => Promise<RemoteResult<boolean>>
  readonly theme: SettingsThemePort
  /** 开一个新对话，输入框预填这句话（空字符串 = 不预填）。 */
  readonly startChatWithPrompt: (prompt: string) => Promise<void>
  /** 积木库那一块（沿用 BlocksSettingsSection）。 */
  readonly renderBlocks: () => ReactNode
  readonly initialSection?: SettingsSectionId | undefined
}

const SECTIONS: readonly { id: SettingsSectionId; title: string; hint: string }[] = [
  { id: 'flow', title: '业务流程', hint: '能力卡串成的流程' },
  { id: 'abilities', title: '能力层', hint: '一件件能力的事' },
  { id: 'blocks', title: '积木库', hint: '零件：大脑、订阅工具、工具' },
  { id: 'schedule', title: '定时任务', hint: '持续运行的流程' },
  { id: 'general', title: '通用', hint: '外观、钥匙串、记录' },
]

type Load<T> = { status: 'loading' } | { status: 'error'; message: string } | { status: 'ready'; value: T }

function useRemote<T>(load: () => Promise<RemoteResult<T>>, deps: readonly unknown[] = []): [Load<T>, () => void] {
  const [state, setState] = useState<Load<T>>({ status: 'loading' })
  const [tick, setTick] = useState(0)
  useEffect(() => {
    let alive = true
    setState({ status: 'loading' })
    load().then((r) => {
      if (!alive) return
      setState(r.ok ? { status: 'ready', value: r.value } : { status: 'error', message: r.error.message || '读取失败' })
    }).catch((error: unknown) => {
      if (alive) setState({ status: 'error', message: error instanceof Error ? error.message : String(error) })
    })
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, ...deps])
  return [state, () => { setTick(n => n + 1) }]
}

function LoadState<T>(props: { state: Load<T>; children: (value: T) => ReactNode }): ReactElement {
  if (props.state.status === 'loading') return <p className={css.muted}>正在读取…</p>
  if (props.state.status === 'error') return <div className={css.error} role="alert">{`读取失败：${props.state.message}`}</div>
  return <>{props.children(props.state.value)}</>
}

function PageHead(props: { title: string; tag?: string; intro: string; actions?: ReactNode }): ReactElement {
  return (
    <div className={css.head}>
      <div className={css.headText}>
        <div className={css.titleRow}>
          <h2 className={css.title}>{props.title}</h2>
          {props.tag ? <small className={css.titleTag}>{props.tag}</small> : null}
        </div>
        <p className={css.intro}>{props.intro}</p>
      </div>
      {props.actions ? <div className={css.headActions}>{props.actions}</div> : null}
    </div>
  )
}

// ---------- 08 业务流程（现在的开发流程；谁来跑过渡期可选，用户 11-16 晚） ----------

const STEP_ROLE_NAMES: Readonly<Record<TeamRole, string>> = { tester: '写测试', developer: '写代码', reviewer: '复查' }

function StepPicker(props: { role: TeamRole; team: TeamInfo; save: SettingsPageInjected['saveTeamRole']; onSaved: (team: TeamInfo) => void }): ReactElement {
  const name = STEP_ROLE_NAMES[props.role]
  const current: TeamPick = props.team.picks?.[props.role] ?? { tool: '', model: '', effort: '' }
  const [pick, setPick] = useState<TeamPick>(current)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [error, setError] = useState('')
  useEffect(() => { setPick(current) }, [current.tool, current.model, current.effort])
  const tools = props.team.options?.[props.role] ?? []
  const models: readonly TeamOption[] = props.team.models?.[pick.tool] ?? []
  const modelMeta = models.find(m => m.value === pick.model)
  const efforts = (props.team.efforts ?? []).filter(e => !modelMeta?.efforts || modelMeta.efforts.includes(e.value))
  const changed = pick.tool !== current.tool || pick.model !== current.model || pick.effort !== current.effort

  const changeTool = (tool: string) => {
    const first = props.team.models?.[tool]?.[0]
    const effort = tool === 'codex' ? (first?.efforts?.includes('high') ? 'high' : first?.efforts?.[0] ?? 'high') : ''
    setPick({ tool, model: tool === 'none' ? '' : first?.value ?? '', effort })
    setNote('')
    setError('')
  }
  const save = async () => {
    setBusy(true)
    setError('')
    setNote('')
    try {
      const r = await props.save(props.role, pick.tool, pick.model, pick.tool === 'codex' ? pick.effort : '')
      if (r.ok) {
        setNote('已保存，下一张任务卡起按新的跑')
        props.onSaved(r.value)
      } else {
        setError(`没保存：${r.error.message}`)
      }
    } catch (e: unknown) {
      setError(`没保存：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={css.picker}>
      <select className={css.select} aria-label={`${name}用哪个工具`} value={pick.tool} disabled={busy}
        onChange={(e) => { changeTool(e.target.value) }}>
        {tools.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
      {pick.tool !== 'none' ? (
        <select className={css.select} aria-label={`${name}用哪个模型`} value={pick.model} disabled={busy}
          onChange={(e) => {
            const next = models.find(m => m.value === e.target.value)
            const keep = !next?.efforts || next.efforts.includes(pick.effort)
            setPick({ ...pick, model: e.target.value, effort: pick.tool === 'codex' && !keep ? next?.efforts?.[0] ?? pick.effort : pick.effort })
            setNote('')
          }}>
          {models.some(m => m.value === pick.model) ? null : <option value={pick.model}>{pick.model || '选一个模型'}</option>}
          {models.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      ) : null}
      {pick.tool === 'codex' ? (
        <select className={css.select} aria-label={`${name}的思考强度`} value={pick.effort} disabled={busy}
          onChange={(e) => { setPick({ ...pick, effort: e.target.value }); setNote('') }}>
          {efforts.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      ) : null}
      {changed ? (
        <div className={css.pickerActions}>
          <Button size="sm" variant="primary" disabled={busy || (pick.tool !== 'none' && !pick.model)} onClick={() => { void save() }}>保存</Button>
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => { setPick(current); setError('') }}>算了</Button>
        </div>
      ) : null}
      {note ? <div className={css.pickerNote}>{note}</div> : null}
      {error ? <div className={css.errorText} role="alert">{error}</div> : null}
    </div>
  )
}

function FlowSection(props: SettingsPageInjected): ReactElement {
  const [workflow] = useRemote(props.workflow)
  const [loadedTeam] = useRemote(props.team)
  const [savedTeam, setSavedTeam] = useState<TeamInfo | null>(null)
  const [cards] = useRemote(props.cards)
  const team = savedTeam ?? (loadedTeam.status === 'ready' ? loadedTeam.value : null)
  const who = (role: TeamRole) => (team?.roles[role] ? `能力卡 · ${team.roles[role]}` : '能力卡')
  const steps: readonly { name: string; who: string; gate: string; role?: TeamRole }[] = [
    { name: '写测试', who: who('tester'), gate: '关卡：先跑必须不过', role: 'tester' },
    { name: '写代码', who: who('developer'), gate: '关卡：新测试全过', role: 'developer' },
    { name: '跑全部测试', who: '能力卡 · 程序', gate: '关卡：全部通过' },
    { name: '复查', who: who('reviewer'), gate: '关卡：无严重、中等问题', role: 'reviewer' },
    { name: '你验收', who: '人', gate: '在真实窗口点一遍' },
  ]
  const rounds = workflow.status === 'ready' && workflow.value.maxReviewRounds ? workflow.value.maxReviewRounds : 3
  const runs = cards.status === 'ready' ? cards.value.length : null
  return (
    <>
      <div className={css.head}>
        <div className={css.headText}>
          <div className={css.titleRow}>
            <h2 className={css.title}>业务流程 · 开发软件</h2>
            {runs !== null ? <small className={css.titleTag}>{`跑过 ${runs} 次`}</small> : null}
          </div>
          <p className={css.intro}>把能力卡串起来，规定每一关怎么过、过不了怎么办、哪里要你拍板。</p>
        </div>
        <div className={css.headActions}>
          {/* “改这条流程”先藏：编辑、新增流程是以后的功能（用户 11-16 晚），见已藏清单 */}
          <Button size="sm" variant="primary" onClick={() => { void props.startChatWithPrompt('') }}>用它做一个新需求</Button>
        </div>
      </div>
      {loadedTeam.status === 'error' && !savedTeam ? <div className={css.error} role="alert">{`读不到分工：${loadedTeam.message}`}</div> : null}
      <ol className={css.steps} aria-label="流程步骤">
        {steps.map((step, index) => (
          <li key={step.name} className={css.step}>
            <div className={css.stepNo}>{`第 ${index + 1} 步`}</div>
            <div className={css.stepName}>{step.name}</div>
            <div className={css.stepWho}>{step.who}</div>
            <div className={css.stepGate}>{step.gate}</div>
            {step.role && team?.picks ? (
              <StepPicker role={step.role} team={team} save={props.saveTeamRole} onSaved={setSavedTeam} />
            ) : null}
          </li>
        ))}
      </ol>
      <section className={css.card} aria-label="过不了怎么办">
        <h3 className={css.cardTitle}>过不了怎么办</h3>
        <dl className={css.rules}>
          <div className={css.ruleRow}><dt>写测试时，测试一跑就通过</dt><dd>说明测试没测到东西，退回重写测试</dd></div>
          <div className={css.ruleRow}><dt>新测试或全部测试没过</dt><dd>带着报错退回写代码</dd></div>
          <div className={css.ruleRow}><dt>复查有严重或中等问题</dt><dd>{`带着问题清单退回写代码，最多 ${rounds} 轮`}</dd></div>
          <div className={css.ruleRow}><dt>{`${rounds} 轮还没过`}</dt><dd className={css.warn}>停下来交给你，不硬跑</dd></div>
        </dl>
      </section>
    </>
  )
}

// ---------- 07 能力层 ----------

function AbilitiesSection(props: SettingsPageInjected): ReactElement {
  const [state] = useRemote(props.abilities)
  return (
    <>
      <PageHead
        title="能力层"
        tag="能力卡"
        intro="每张卡是一件能单独办的事：收什么、交回什么、怎么算做对，都写清楚。试做通过才能放进来。"
        actions={<Button size="sm" variant="primary" onClick={() => { void props.startChatWithPrompt('我想要一个新能力：') }}>+ 跟超级玩家说一个新能力</Button>}
      />
      <LoadState state={state}>
        {rows => rows.length === 0 ? (
          <div className={css.empty}>还没有能力卡</div>
        ) : (
          <table className={css.table}>
            <thead>
              <tr><th>能力卡</th><th>收什么 → 交什么</th><th>谁来跑</th><th>怎么算做对</th><th aria-label="状态" /></tr>
            </thead>
            <tbody>
              {rows.map(row => row.problem ? (
                <tr key={row.id}>
                  <td className={css.strong}>{row.title}</td>
                  <td colSpan={3} className={css.errorText}>{row.problem}</td>
                  <td><Tag tone="danger">读不了</Tag></td>
                </tr>
              ) : (
                <tr key={row.id}>
                  <td className={css.strong}>{row.title}</td>
                  <td>{`${row.inputs ?? '—'} → ${row.output ?? '—'}`}</td>
                  <td>{row.who ?? '—'}</td>
                  <td>{row.check ?? '—'}</td>
                  <td>{row.draft ? <Tag>草稿</Tag> : <Tag tone="success">{`用过 ${row.runs ?? 0} 次`}</Tag>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </LoadState>
    </>
  )
}

// ---------- 16 定时任务（框架和说明，功能二期） ----------

function ScheduleSection(): ReactElement {
  return (
    <>
      {/* “+ 跟超级玩家说一个新的”先藏：定时任务功能二期做（用户 11-16 晚），见已藏清单 */}
      <PageHead title="定时任务" tag="持续运行的流程" intro="让某条流程按时间自己跑。每次跑完都会生成一张任务卡，在左栏“任务”里看结果。" />
      <div className={css.empty}>还没有定时任务</div>
      <section className={css.card} aria-label="设一条定时任务时，超级玩家会问你这几件事">
        <h3 className={css.cardTitle}>设一条定时任务时，超级玩家会问你这几件事</h3>
        <dl className={css.rules}>
          <div className={css.ruleRow}><dt>多久跑一次</dt><dd>每小时 / 每天几点 / 每周几</dd></div>
          <div className={css.ruleRow}><dt>出什么情况才打扰你</dt><dd>只在有变化时提醒，免得天天一堆消息</dd></div>
          <div className={css.ruleRow}><dt>同一件事不要重复处理</dt><dd>已经处理过的变化，下次不再提醒</dd></div>
          <div className={css.ruleRow}><dt>电脑关了怎么办</dt><dd className={css.warn}>现在要电脑开着才能跑；关机期间错过的，开机后补跑一次</dd></div>
        </dl>
      </section>
    </>
  )
}

// ---------- 15 通用 ----------

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`
  if (bytes >= 1024 * 1024) return `${Math.round(bytes / 1024 / 1024)} MB`
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`
  return `${bytes} B`
}

function homeShort(path: string): string {
  const match = /^\/Users\/[^/]+(\/.*)?$/.exec(path)
  return match ? `~${match[1] ?? ''}` : path
}

function GeneralSection(props: SettingsPageInjected): ReactElement {
  const theme = useSyncExternalStore(props.theme.subscribe, props.theme.get, props.theme.get)
  const [keys, reloadKeys] = useRemote(props.storedKeys)
  const [records] = useRemote(props.records)
  const [managing, setManaging] = useState(false)
  const [confirming, setConfirming] = useState<StoredKey | null>(null)
  const [actionError, setActionError] = useState('')
  const [busy, setBusy] = useState(false)

  const remove = async (key: StoredKey) => {
    setBusy(true)
    setActionError('')
    try {
      const r = await props.deleteKey(key.id)
      if (!r.ok) setActionError(`没删掉：${r.error.message}`)
      setConfirming(null)
      reloadKeys()
    } finally {
      setBusy(false)
    }
  }

  const choices: readonly { id: ThemeChoice; label: string }[] = [
    { id: 'light', label: '浅色' }, { id: 'dark', label: '深色' }, { id: 'system', label: '跟随系统' },
  ]
  const themeWord = theme === 'light' ? '浅色' : theme === 'dark' ? '深色' : '跟随系统'

  return (
    <>
      <PageHead title="通用" intro="外观和这台电脑上存着的东西。" />
      {/* “花费提醒”一节先不做（用户 11-16 晚），见已藏清单 */}
      <section className={css.group} aria-label="外观">
        <h3 className={css.groupTitle}>外观</h3>
        <div className={css.row}>
          <div className={css.rowMain}>
            <div className={css.rowName}>主题</div>
            <div className={css.rowMeta}>{`${themeWord}（沿用 DSH 的浅色 / 深色）`}</div>
          </div>
          <div className={css.rowActions}>
            {choices.map(choice => (
              <Button key={choice.id} size="sm" variant={theme === choice.id ? 'primary' : 'outline'}
                aria-pressed={theme === choice.id} onClick={() => { props.theme.set(choice.id) }}>
                {choice.label}
              </Button>
            ))}
          </div>
        </div>
      </section>
      <section className={css.group} aria-label="数据">
        <h3 className={css.groupTitle}>数据</h3>
        <div className={css.row}>
          <div className={css.rowMain}>
            <div className={css.rowName}>钥匙串里存着的 Key</div>
            <div className={css.rowMeta}>
              <LoadState state={keys}>
                {list => list.length === 0 ? '还没有（只在这台电脑上）' : `${list.length} 个：${list.map(k => k.label).join('、')}（只在这台电脑上）`}
              </LoadState>
            </div>
          </div>
          <div className={css.rowActions}>
            <Button size="sm" variant="outline" disabled={keys.status !== 'ready' || keys.value.length === 0}
              onClick={() => { setManaging(v => !v); setConfirming(null) }}>
              {managing ? '收起' : '管理'}
            </Button>
          </div>
        </div>
        {managing && keys.status === 'ready' ? (
          <div className={css.subList}>
            {keys.value.map(key => (
              <div key={key.id} className={css.subRow}>
                <span>{key.label}</span>
                {confirming?.id === key.id ? (
                  <span className={css.confirm}>
                    <span className={css.warn}>{key.inUse ? '删了以后要重新配置大模型，确定吗？' : '确定删掉这把 Key 吗？'}</span>
                    <Button size="sm" variant="primary" disabled={busy} onClick={() => { void remove(key) }}>确认删除</Button>
                    <Button size="sm" variant="ghost" disabled={busy} onClick={() => { setConfirming(null) }}>算了</Button>
                  </span>
                ) : (
                  <Button size="sm" variant="ghost" aria-label={`删除 ${key.label}`} onClick={() => { setConfirming(key) }}>删除</Button>
                )}
              </div>
            ))}
          </div>
        ) : null}
        {actionError ? <div className={css.error} role="alert">{actionError}</div> : null}
        <div className={css.row}>
          <div className={css.rowMain}>
            <div className={css.rowName}>任务和对话记录</div>
            <div className={css.rowMeta}>
              <LoadState state={records}>
                {info => `存放在 ${homeShort(info.path)} · 占用 ${formatBytes(info.bytes)}`}
              </LoadState>
            </div>
          </div>
          <div className={css.rowActions}>
            <Button size="sm" variant="outline" onClick={() => { void props.openRecords() }}>打开文件夹</Button>
          </div>
        </div>
      </section>
    </>
  )
}

// ---------- 外框 ----------

export function SettingsPage(props: SettingsPageInjected): ReactElement {
  const [section, setSection] = useState<SettingsSectionId>(props.initialSection ?? 'blocks')
  return (
    <div className={css.frame}>
      {/* 窗口拖动顶条：盖住顶部 24px 留白，不盖按钮。 */}
      <div className={css.dragStrip} data-window-drag="" aria-hidden="true" />
      <nav className={css.nav} aria-label="设置">
        <div className={css.navLabel}>设置</div>
        {SECTIONS.map(item => (
          <button key={item.id} type="button" className={item.id === section ? `${css.navItem} ${css.navItemActive}` : css.navItem}
            aria-current={item.id === section ? 'page' : undefined} onClick={() => { setSection(item.id) }}>
            <span className={css.navTitle} data-nav-title="">{item.title}</span>
            <span className={css.navHint}>{item.hint}</span>
          </button>
        ))}
      </nav>
      <div className={css.content}>
        <div className={css.inner}>
          {section === 'flow' ? <FlowSection {...props} /> : null}
          {section === 'abilities' ? <AbilitiesSection {...props} /> : null}
          {section === 'blocks' ? props.renderBlocks() : null}
          {section === 'schedule' ? <ScheduleSection /> : null}
          {section === 'general' ? <GeneralSection {...props} /> : null}
        </div>
      </div>
    </div>
  )
}
