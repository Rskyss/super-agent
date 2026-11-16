/** 超级助手正式接口上传给界面的数据（浏览器端可安全引用）。 */

/** 任务卡类型：干活 / 造能力 / 接零件。 */
export type CardKind = 'work' | 'ability' | 'connect'

/** 左栏、总览用的一行卡片。 */
export interface CardRow {
  readonly id: string
  readonly title: string
  readonly state: string
  readonly phaseCode: string
  readonly phaseLabel: string
  readonly needsYou: boolean
  readonly actions: readonly string[]
  readonly updated: string
  readonly kind?: CardKind | null
  readonly flow?: string | null
  readonly reviewRounds?: number | null
  readonly elapsed?: number | null
  readonly completedAt?: string | null
  readonly firstPass?: boolean | null
  readonly archived?: boolean
}

/** 后台服务状态。 */
export interface BackendStatus {
  readonly running: boolean
  readonly port: number
  readonly project: string
}

/** 卡片操作的结果。 */
export interface CardActionResult {
  readonly ok: boolean
  readonly message: string
}

/** 一张等你确认的确认卡（聊天里超级助手出的，还没建卡）。 */
export interface Proposal {
  readonly id: string
  /** 出卡的会话。 */
  readonly session: string
  readonly title: string
  readonly do: string
  readonly notDo: string
  readonly doneWhen: string
  /** 要你拍板的问题，每条带建议答案。 */
  readonly questions: readonly ProposalQuestion[]
  readonly createdAt: number
}

/** 确认卡上的一个问号。 */
export interface ProposalQuestion {
  readonly question: string
  readonly suggestion: string
}

/** 单条结构化验收标准修改（只支持替换第 N 条或新增一条）。 */
export type CriteriaChangeItem =
  | { readonly replaceNth: number; readonly text: string }
  | { readonly add: string }

/** 支持的任务卡截图格式（仅允许 PNG 与 JPEG）。 */
export type ImageMediaType = 'image/png' | 'image/jpeg'

/** 经前端读取并随改要求提交的原始图片数据（纯 base64，不含文件名）。 */
export interface ImagePayload {
  readonly mediaType: ImageMediaType
  readonly data: string
}

/** 结构化修改卡时的附加基线、改动明细与截图附件。 */
export interface CardActionOptions {
  readonly criteriaBase?: readonly string[] | undefined
  readonly criteriaChanges?: readonly CriteriaChangeItem[] | undefined
  readonly attachments?: readonly ImagePayload[] | undefined
}

/** 提出改要求确认条时的附加信息。 */
export interface RequestChangeOptions extends CardActionOptions {
  readonly sourceDraft?: string | undefined
  readonly sourceAttachmentIds?: readonly string[] | undefined
}

/** 拆分“继续改 / 按我说的改”的结果。 */
export interface PrepareRevisionResult {
  readonly ok: boolean
  readonly pending: boolean
  readonly message: string
}

/** 任务对话里提出的“改要求”，等你点确认才转给调度员（绝不含图片 base64）。 */
export interface PendingChange {
  readonly id: string
  readonly card: string
  /** 一句话说改什么，界面上原样显示。 */
  readonly summary: string
  /** 确认后交给调度员的原话。 */
  readonly text: string
  readonly createdAt: number
  readonly criteriaBase?: readonly string[] | undefined
  readonly criteriaChanges?: readonly CriteriaChangeItem[] | undefined
  readonly sourceDraft?: string | undefined
  readonly attachmentCount?: number | undefined
  readonly sourceAttachmentIds?: readonly string[] | undefined
}

/** 确认建卡的结果。 */
export interface CreatedCard {
  readonly ok: boolean
  readonly card: string
  /** 是否已经开始写方案（同类后台任务在跑时会晚一点）。 */
  readonly planning: boolean
  readonly message: string
}

/** 一张卡的任务对话信息。 */
export interface TaskSessionInfo {
  /** 已记下的会话名；还没建时为空。 */
  readonly sessionId: string
  /** 建议用的会话名（task-<项目指纹>-<卡号>）。 */
  readonly preferred: string
  /** 项目文件夹（任务对话的工作区）。 */
  readonly project: string
  /** 任务对话的标题。 */
  readonly title: string
}

/** 绑定任务对话的结果。 */
export interface TaskSessionBind {
  readonly ok: boolean
  /** 最终这张卡用的会话名（已经绑过时是原来那个）。 */
  readonly sessionId: string
  /** 是否早就绑过。 */
  readonly existed: boolean
  readonly reason: string
}

/** 已结束任务的阶段编号，不能再提出或确认改要求。 */
export const FINISHED_PHASE_CODES: ReadonlySet<string> = new Set(['done', 'dropped'])

/** 已结束任务的中文状态，配合阶段编号共同兜底判断终态。 */
export const FINISHED_CARD_STATES: ReadonlySet<string> = new Set(['已合并', '已放弃'])

/** 后台单步进度原文结构（与视图接口返回对齐）。 */
export interface ViewStation {
  readonly key: string
  readonly role?: string
  readonly who?: string
  readonly face?: string
  readonly model?: string
  readonly state?: string
  readonly detail?: string
  readonly live?: boolean
  readonly elapsed?: number | null
  readonly quiet?: number | null
}

/** 后台卡片阶段结构。 */
export interface ViewPhase {
  readonly code?: string
  readonly label?: string
  readonly tone?: string
  readonly needs_you?: boolean
  readonly detail?: string
  readonly actions?: readonly string[]
  readonly issues?: string
  readonly later?: readonly string[]
}

/** 后台视图里的卡片概要。 */
export interface ViewCard {
  readonly id?: string
  readonly title?: string
  readonly state?: string
  readonly rework?: number
  readonly round?: number
  readonly phase?: ViewPhase
}

/** 后台视图里的待验收详情。 */
export interface ViewAcceptInfo {
  readonly result?: string
  readonly how?: string
  readonly can_preview?: boolean
  readonly copy_there?: boolean
  /** 逐条验收标准（来自需求卡“怎么算做完”）。 */
  readonly criteria?: readonly string[]
  /** 历史追加修改记录（来自需求卡“追加要求”）。 */
  readonly changeNotes?: readonly string[]
  /** 流转记录中是否已做过结构化逐条验收标准更新。 */
  readonly criteriaUpdated?: boolean
}

/** 后台视图返回的完整数据结构。 */
export interface CardViewPayload {
  readonly installed?: boolean
  readonly card?: ViewCard | null
  readonly stations?: readonly ViewStation[]
  readonly elapsed?: number
  readonly workElapsed?: number | null
  readonly stale?: boolean
  readonly live?: boolean
  readonly now?: { readonly text?: string; readonly tone?: string; readonly detail?: string }
  readonly accept?: ViewAcceptInfo
}

/** 后台方案接口返回的结构。 */
export interface CardPlanPayload {
  readonly plan: string
  readonly allowed: readonly string[]
  readonly test_command: string
  readonly new_profile?: boolean
  readonly profile_text?: string
  /** 方案版本号（按流转记录里的“方案”次数计算）。 */
  readonly version?: number
}

/** 预览操作结果。 */
export interface PreviewResult {
  readonly ok: boolean
  readonly mode?: 'web' | 'folder' | string
  readonly url?: string
  readonly path?: string
  readonly reason?: string
}

/** 当前卡片的预览运行状态。 */
export interface PreviewStatus {
  readonly running: boolean
  readonly url: string
}

/** 界面七步流转的固定标识。 */
export type ProgressStepKey = 'disp' | 'approve' | 'test' | 'dev' | 'check' | 'rev' | 'accept'

/** 界面单步展示状态。 */
export type ProgressStepState = 'done' | 'active' | 'wait' | 'fail' | 'idle'

/** 界面解析后的单步进度。 */
export interface ProgressStep {
  readonly key: ProgressStepKey
  readonly label: string
  readonly state: ProgressStepState
  readonly actor: string
  readonly model: string
  readonly elapsed: number | null
  /** 最多 3 行的大白话摘要（已替换旧称）。 */
  readonly summary: string
  /** 完整原文（已替换旧称），用于展开详情。 */
  readonly rawDetail: string
  /** 是否为当前正在进行或等待用户处理的步骤。 */
  readonly current: boolean
}

/** 界面解析后的卡片七步总览。 */
export interface ParsedCardProgress {
  readonly cardId: string
  readonly title: string
  readonly stateText: string
  readonly phaseLabel: string
  readonly phaseCode: string
  readonly elapsed: number
  readonly workElapsed: number | null
  readonly steps: readonly ProgressStep[]
}

/** 流转轨迹行中登记的安全截图元数据（不含文件内容或绝对路径）。 */
export interface TrajectoryAttachment {
  readonly name: string
  readonly mediaType: ImageMediaType
}

/** 单条任务流转轨迹行。 */
export interface TrajectoryRow {
  readonly id: string
  readonly time: string
  readonly actor: string
  readonly summary: string
  readonly seconds: number | null
  readonly usage: number | null
  readonly raw: string
  readonly attachments?: readonly TrajectoryAttachment[] | undefined
}

/** 按执行人统计的明确耗时分布项。 */
export interface TrajectoryDistributionItem {
  readonly actor: string
  readonly seconds: number
}

/** 任务卡完整轨迹载荷。 */
export interface CardTrajectory {
  readonly rows: readonly TrajectoryRow[]
  readonly distribution: readonly TrajectoryDistributionItem[]
}

/** 白名单订阅调度员工具（第 10 项：仅支持 Codex 与 agy）。 */
export type DispatcherTool = 'codex' | 'agy'

/** 调度员真实状态（基于显式隔离积木库与配置指纹缓存，不假成功）。 */
export interface DispatcherState {
  readonly status: 'ready' | 'missing' | 'unknown'
  readonly tool: string
  readonly label: string
  readonly model: string
  readonly reason: string
  readonly verified: boolean
}

/** 接调度员确认卡草案（先出示是什么 / 怎么接 / 需要你 / 影响范围，确认后再检测）。 */
export interface DispatcherProposal {
  readonly id: string
  readonly session: string
  readonly tool: DispatcherTool
  readonly label: string
  readonly card: string
  readonly title: string
  readonly what: string
  readonly how: string
  readonly needYou: string
  readonly scope: string
  readonly createdAt: number
}

/** 连接调度员的可选上下文参数（兼容 exactOptionalPropertyTypes）。 */
export interface DispatcherConnectOptions {
  readonly sessionId?: string | undefined
  readonly proposalId?: string | undefined
  readonly card?: string | undefined
}

/** 确认接调度员后的真实检测与启动结果。 */
export interface DispatcherConnectResult {
  readonly ok: boolean
  readonly status: 'ready' | 'missing'
  readonly tool: string
  readonly label: string
  readonly model: string
  readonly reason: string
  readonly startedPlan: boolean
  readonly planNote: string
  readonly card: string
}

/** 首次配置 / 模型就绪状态（绝不含明文密钥）。 */
export interface OnboardingState {
  readonly keychainEnabled: boolean
  readonly required: boolean
  readonly configured: boolean
  readonly provider?: string
  readonly routeId?: string
  readonly baseUrl?: string
  readonly model?: string
  readonly maskedKey?: string
  readonly error?: string
}

/** 模型列表拉取项（仅公开元数据）。 */
export interface OnboardingModelItem {
  readonly id: string
  readonly name: string
  readonly contextWindow?: number | undefined
  readonly maxTokens?: number | undefined
  readonly inputModalities?: readonly ('text' | 'image')[] | undefined
}

/** 首次配置 / 更换模型的请求载荷。 */
/** 第 17 项：旧版复制过来、可直接选用的模型 Key（只有条目名、服务商和地址，不含密钥）。 */
export interface LegacyKeyOption {
  readonly account: string
  readonly provider: 'deepseek' | 'dashscope' | 'tencent' | 'other'
  readonly label: string
  readonly baseUrl: string
}

export interface OnboardingSetupPayload {
  readonly provider: string
  readonly baseUrl: string
  readonly key: string
  readonly model?: string | undefined
  /** 第 17 项：选用旧版复制过来的 Key（条目名），key 为空，由主机读钥匙串。 */
  readonly legacyAccount?: string | undefined
}

/** 首次配置确认结果（绝不含密钥）。 */
export interface OnboardingSetupSummary {
  readonly ok: true
  readonly provider: string
  readonly baseUrl: string
  readonly model: string
}
