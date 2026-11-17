// @vitest-environment jsdom
// 11-16 用户实测：清空后第一次配置完模型，整个窗口点不动。
// 实测顺序：启动时 DSH 自己的浮层（按计数）先锁住页面 → 首次配置框打开、记下“原来是锁着的” → DSH 浮层解锁 →
// 配置完成、首次配置框按“原来的样子”恢复成锁住 → 页面永远点不动。
// 修法：首次配置框不再自己锁应用根元素（DSH Modal 的遮罩和焦点圈住已够），锁只归 DSH 一家管。
// 复查第 1 轮：开关顺序还有“框先开、DSH 后开/后关”等交错，下面逐个覆盖。
import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it } from 'vitest'
import { OnboardingOverlay, OnboardingUiStore } from '../src/client/OnboardingOverlay.tsx'

// jsdom 没有 inert 属性：按浏览器的样子用 inert 标记补一个
if (!('inert' in HTMLElement.prototype)) {
  Object.defineProperty(HTMLElement.prototype, 'inert', {
    configurable: true,
    get(this: HTMLElement) { return this.hasAttribute('inert') },
    set(this: HTMLElement, v: boolean) { if (v) this.setAttribute('inert', ''); else this.removeAttribute('inert') },
  })
}

// 照搬 DSH ui-settings-account/overlay-inert.ts 的按计数锁（DSH 浮层的真实做法）
const owners = new WeakMap<HTMLElement, { count: number; previous: boolean }>()
function dshLock(el: HTMLElement): () => void {
  const st = owners.get(el) ?? { count: 0, previous: el.inert }
  st.count++; owners.set(el, st); el.inert = true
  let done = false
  return () => { if (done) return; done = true; if (--st.count === 0) { el.inert = st.previous; owners.delete(el) } }
}

let root: HTMLElement
beforeEach(() => { root = document.createElement('div'); root.id = 'root'; document.body.appendChild(root) })
afterEach(() => { cleanup(); root.remove() })

const noop = async () => {}
const needsSetup = { keychainEnabled: true, required: true, configured: false }
const ready = { keychainEnabled: true, required: false, configured: true }
async function openSetup() {
  const store = new OnboardingUiStore()
  render(<OnboardingOverlay store={store} loadModels={async () => []} complete={noop} onOnboardingDone={noop} />)
  await act(async () => { store.applyHostState(needsSetup) })
  expect(store.getSnapshot().modalOpen).toBe(true)
  return store
}
async function finish(store: OnboardingUiStore) {
  await act(async () => { store.markCompleted(ready) })
  expect(store.getSnapshot().modalOpen).toBe(false)
}

it('用户实测顺序：DSH 先锁 → 框开 → DSH 解锁 → 配置完成：页面能点', async () => {
  const release = dshLock(root)
  const store = await openSetup()
  release()
  await finish(store)
  expect(root.inert).toBe(false)
})

it('框先开 → DSH 后锁 → 框先关 → DSH 后解锁：页面能点（不会被 DSH 恢复成锁住）', async () => {
  const store = await openSetup()
  const release = dshLock(root)
  await finish(store)
  expect(root.inert).toBe(true)           // DSH 浮层还开着，仍锁着是对的
  release()
  expect(root.inert).toBe(false)
})

it('DSH 先锁 → 框开 → 框先关：DSH 浮层还开着时不被框提前解锁', async () => {
  const release = dshLock(root)
  const store = await openSetup()
  await finish(store)
  expect(root.inert).toBe(true)
  release()
  expect(root.inert).toBe(false)
})

it('首次配置框是整屏模态框（遮罩挡鼠标、焦点圈在框里），不靠锁应用根元素', async () => {
  const store = await openSetup()
  expect(screen.getByRole('dialog').getAttribute('aria-modal')).toBe('true')
  expect(root.inert).toBe(false)
  await finish(store)
})

it('从设置里“更换模型”打开、取消：页面锁的状态原样', async () => {
  const store = new OnboardingUiStore()
  render(<OnboardingOverlay store={store} loadModels={async () => []} complete={noop} onOnboardingDone={noop} />)
  await act(async () => { store.applyHostState(ready) })
  await act(async () => { store.openForChange() })
  expect(root.inert).toBe(false)
  await act(async () => { store.closeIfAllowed() })
  expect(store.getSnapshot().modalOpen).toBe(false)
  expect(root.inert).toBe(false)
})
