// @vitest-environment jsdom
// 用户 11-16 反馈：左上角版本号不要；整个窗口拖不动。
// 病根：换掉 DSH 左栏后，DSH 左栏顶上“红绿灯那一行”的可拖条没带过来；我们自己的整页（任务总览、首次配置）顶上也没有可拖区。
// 照 DSH 的规矩（ui-web base.css：带 data-window-drag 的盒子就是可拖区），在三处顶上各补一条，只盖住没有按钮的留白。
import { act, cleanup, render, screen } from '@testing-library/react'
import { createElement } from 'react'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, expect, it, vi } from 'vitest'
import { TaskSidebar } from '../src/client/TaskSidebar.tsx'
import { TasksProbe } from '../src/client/TasksProbe.tsx'
import { OnboardingPage } from '../src/client/OnboardingPage.tsx'
import { OnboardingUiStore } from '../src/client/OnboardingOverlay.tsx'

afterEach(cleanup)
const ok = <T,>(value: T) => ({ ok: true as const, value })
const css = (name: string) => readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'client', name), 'utf8')
/** 取某个类的第一段样式。 */
const block = (text: string, cls: string) => text.match(new RegExp(`\\.${cls}\\s*\\{([^}]*)\\}`))?.[1] ?? ''

function sidebar(collapsed = false) {
  const sessions = { phase: 'ready', ids: [], byId: {}, projectionsBySession: {} }
  const workspaces = { phase: 'ready', state: 'idle', error: null, items: [], archivedSessionIds: [], pinnedSessionIds: [] }
  return render(<TaskSidebar cards={vi.fn(async () => ok([]))} taskSessions={vi.fn(async () => ok({}))}
    useSessions={(s: any) => s(sessions)} useWorkspaces={(s: any) => s(workspaces)}
    openSession={vi.fn()} openTask={vi.fn(async () => {})} startChat={vi.fn(async () => {})}
    renameSession={vi.fn(async () => ok({}))} archiveSession={vi.fn(async () => ok({}))} showOverview={vi.fn()}
    wide={!collapsed} expandSidebar={vi.fn()} collapseSidebar={vi.fn()} openSettings={vi.fn()}
    useSetupLocked={() => false} version="0.2.2-rc.3" {...({} as any)} />)
}

it('左栏品牌旁不显示版本号', () => {
  sidebar()
  expect(screen.getByText('超级玩家', { exact: true })).toBeTruthy()
  expect(document.body.textContent).not.toMatch(/v?0\.2\.2/)
})

it('左栏最上面有一条可拖的顶条，排在所有内容之前，正好盖住红绿灯那一行留白', () => {
  const { container } = sidebar()
  const root = container.firstElementChild as HTMLElement
  const strip = root.firstElementChild as HTMLElement
  expect(strip.hasAttribute('data-window-drag')).toBe(true)
  expect(strip.getAttribute('aria-hidden')).toBe('true')
  expect(strip.querySelector('button')).toBeNull()
  const text = css('TaskSidebar.module.css')
  const rule = block(text, 'dragStrip')
  expect(rule).toMatch(/position:\s*absolute/)
  expect(rule).toMatch(/top:\s*0/)
  expect(rule).toMatch(/height:\s*40px/) // 与 darwin 下左栏顶部留白 40px 一致，不盖到收起按钮
  expect(block(text, 'root')).toMatch(/position:\s*relative/)
})

it('任务总览（空和有卡两种）顶上都有可拖条，且不跟着列表滚走', async () => {
  for (const rows of [[], [{ id: 'CARD-001', title: '真实卡', state: '已合并', phaseCode: 'done', phaseLabel: '已完成', needsYou: false, actions: [], updated: '2026-11-17', kind: 'work', flow: '开发软件', reviewRounds: 1, elapsed: 65, completedAt: '2026-11-17T09:00:00', firstPass: true, archived: false }]]) {
    const { container, unmount } = render(<TasksProbe cards={async () => ok(rows as any)} openTask={async () => {}} startChat={async () => {}} />)
    await act(async () => { for (let i = 0; i < 6; i++) await Promise.resolve() })
    const frame = container.firstElementChild as HTMLElement
    const strip = frame.firstElementChild as HTMLElement
    expect(strip.hasAttribute('data-window-drag')).toBe(true)
    expect(strip.getAttribute('aria-hidden')).toBe('true')
    // 滚动的是可拖条后面的那一层，可拖条自己不在滚动层里
    expect(strip.nextElementSibling).not.toBeNull()
    unmount()
  }
  const text = css('TasksProbe.module.css')
  expect(block(text, 'frame')).toMatch(/position:\s*relative/)
  expect(block(text, 'dragStrip')).toMatch(/position:\s*absolute/)
  expect(block(text, 'dragStrip')).toMatch(/height:\s*24px/) // 等于页面顶部留白，不盖住任何按钮
})

it('首次配置整页顶上有可拖条', () => {
  const store = new OnboardingUiStore(); store.applyHostState({ keychainEnabled: true, required: true, configured: false } as any)
  const { container } = render(createElement(OnboardingPage, { store, loadModels: vi.fn(async () => []), complete: vi.fn(async () => {}), onOnboardingDone: vi.fn(async () => {}) } as any))
  const page = container.firstElementChild as HTMLElement
  const strip = page.firstElementChild as HTMLElement
  expect(strip.hasAttribute('data-window-drag')).toBe(true)
  expect(strip.getAttribute('aria-hidden')).toBe('true')
  const text = css('OnboardingPage.module.css')
  expect(block(text, 'page')).toMatch(/position:\s*relative/)
  expect(block(text, 'dragStrip')).toMatch(/position:\s*absolute/)
  expect(block(text, 'dragStrip')).toMatch(/height:\s*32px/) // 等于滚动区顶部留白
})
