// @vitest-environment jsdom
/// <reference types="vite/client" />
// Claude 最终验收发现：中间栏窄时流转记录表“做了什么”被挤成一字一行、表头叠字。
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import type { ComponentType } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const modules = import.meta.glob('../src/client/TaskTrajectory.tsx', { eager: true }) as Record<string, Record<string, ComponentType<any>>>
const TaskTrajectory = modules['../src/client/TaskTrajectory.tsx']!.TaskTrajectory!
afterEach(() => cleanup())
const cssText = readFileSync(join(__dirname, '../src/client/TaskTrajectory.module.css'), 'utf8')

const payload = {
  distribution: [{ actor: '调度员', seconds: 53 }],
  rows: [
    { id: 'r1', time: '21:29:13', actor: '你', summary: '待办清单加 version 命令', seconds: null, usage: null, raw: '' },
    { id: 'r2', time: '21:30:06', actor: '调度员', summary: '写好方案', seconds: 53, usage: 1800, raw: '方案原文全文' },
  ],
}

const renderView = () => render(
  <TaskTrajectory sessionId="s1" cardOfSession={vi.fn(async () => 'CARD-011')} trajectory={vi.fn(async () => payload)} />,
)

it('轨迹说明是已记录阶段累计，避免和施工墙钟用时混为一谈',async()=>{
 renderView();expect(await screen.findByText(/已记录阶段累计/)).toBeTruthy()
 expect(screen.queryByText(/已明确记录用时合计/)).toBeNull()
})

it('表格只有五列（时间、谁、做了什么、用时、用量），“看原文”放在做了什么里，不再单占一列', async () => {
  renderView()
  const table = await screen.findByRole('table')
  // 窄栏把表格改成 block 显示时，部分浏览器会丢表格语义；显式写明角色保住读屏
  expect(table.getAttribute('role')).toBe('table')
  const allHaveRole = (selector: string, role: string) =>
    [...table.querySelectorAll(selector)].forEach(el => expect(el.getAttribute('role'), selector).toBe(role))
  allHaveRole('thead, tbody', 'rowgroup')
  allHaveRole('tr', 'row')
  allHaveRole('th', 'columnheader')
  allHaveRole('td', 'cell')
  const headers = within(table).getAllByRole('columnheader').map(h => h.textContent)
  expect(headers).toEqual(['时间', '谁', '做了什么', '用时', '用量'])
  const rows = within(table).getAllByRole('row').slice(1)
  const cells = within(rows[1]!).getAllByRole('cell')
  expect(cells).toHaveLength(5)
  expect(within(cells[2]!).getByText('写好方案')).toBeTruthy()
  fireEvent.click(within(cells[2]!).getByRole('button', { name: '看原文' }))
  expect(await screen.findByText('方案原文全文')).toBeTruthy()
  allHaveRole('tr', 'row') // 展开的原文行也要有角色
  allHaveRole('td', 'cell')
  // 没有原文的行不出现按钮
  expect(within(within(rows[0]!).getAllByRole('cell')[2]!).queryByRole('button')).toBeNull()
})

it('中间栏窄时改成上下排（第一行几点·谁·用时·用量，第二行做了什么），不会挤成一字一行', () => {
  expect(cssText).toMatch(/container-type:\s*inline-size/)
  const narrow = cssText.match(/@container[^{]*max-width:\s*(\d+)px[^{]*\{([\s\S]*?)\n\}/)
  expect(narrow, '缺少窄栏布局').not.toBeNull()
  expect(Number(narrow![1])).toBeGreaterThanOrEqual(560)
  expect(narrow![2]).toMatch(/display:\s*grid/)
  expect(narrow![2]).toMatch(/grid-template-areas/)
  // 每一格都要放进对应区域，做了什么占满第二行
  for (const [col, area] of [['Time', 'time'], ['Actor', 'actor'], ['Seconds', 'seconds'], ['Usage', 'usage'], ['Summary', 'summary']]) {
    expect(narrow![2], `${col} 没放进 ${area} 区域`).toMatch(new RegExp(`td\\.col${col}\\s*\\{\\s*grid-area:\\s*${area};`))
  }
  expect(narrow![2]).toMatch(/"summary summary summary summary"/)
  // 展开的原文行在窄栏占满整宽
  expect(narrow![2]).toMatch(/tr\.rawRow\s*\{[^}]*display:\s*block/)
  // 表头只做视觉隐藏，读屏还能读到列名（不许 display:none）
  const thead = narrow![2]!.match(/\.table thead\s*\{([^}]*)\}/)
  expect(thead).not.toBeNull()
  expect(thead![1]).not.toMatch(/display:\s*none/)
  expect(thead![1]).toMatch(/clip:/)
  // 宽栏时固定列宽合计不能挤掉“做了什么”：除做了什么外的固定列宽 + 内边距 ≤ 360px
  const widths = [...cssText.matchAll(/\.col(?:Time|Actor|Seconds|Usage)\s*\{[^}]*?width:\s*(\d+)px/g)].map(m => Number(m[1]))
  expect(widths).toHaveLength(4)
  expect(widths.reduce((a, b) => a + b, 0)).toBeLessThanOrEqual(300)
})

it('底部不重复给输入框留位（DSH 滚动区已留），避免最后一行下面多出一大段空白', () => {
  const root = cssText.match(/\.root\s*\{([^}]*)\}/)
  expect(root).not.toBeNull()
  expect(root![1]).not.toMatch(/--dsh-composer-height/)
})
