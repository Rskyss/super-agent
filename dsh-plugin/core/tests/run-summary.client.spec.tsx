// @vitest-environment jsdom
// 第七批 7-3：右栏底“本次运行 · 改了 N 个文件 · 守住 N 条规矩”（对照 04 / 05）。
// 用户 11-16 定：“守住 N 条规矩” = 这次跑完自动检查通过了几项；点开看是哪几项；没检查过不显示这半句。
import {cleanup,fireEvent,render,screen} from '@testing-library/react'
import {afterEach,expect,it} from 'vitest'
import {parseProgressView} from '../src/progress-model.ts'
import {ProgressPanel} from '../src/client/ProgressPanel.tsx'
afterEach(cleanup)

const view=(run?:unknown)=>JSON.stringify({isRunningNow:false,card:{id:'CARD-001',title:'t',state:'待验收',phase:{code:'review',label:'等你验收',needs_you:true}},stations:[],...(run?{run}:{})})

it('右栏底显示“本次运行 · 改了 N 个文件 · 守住 N 条规矩”，点开列出是哪几项',async()=>{
  expect(parseProgressView(view({changedFiles:3,rules:[{label:'只改方案允许的地方',ok:true,detail:'改动范围正常（3 个文件）'}]}),'CARD-001').run?.changedFiles).toBe(3)
  render(<ProgressPanel card="CARD-001" view={async()=>({ok:true,value:view({changedFiles:3,rules:[
    {label:'只改方案允许的地方',ok:true,detail:'改动范围正常（3 个文件）'},
    {label:'相关老测试没被改坏',ok:true,detail:'跑了老测试：过'},
  ]})})}/>)
  const btn=await screen.findByRole('button',{name:/本次运行 · 改了 3 个文件 · 守住 2 条规矩/})
  expect(screen.queryByText('相关老测试没被改坏')).toBeNull()
  fireEvent.click(btn)
  expect(screen.getByText('相关老测试没被改坏')).toBeTruthy()
})

it('没有本次运行数据时不显示这一行',async()=>{
  render(<ProgressPanel card="CARD-001" view={async()=>({ok:true,value:view()})}/>)
  await screen.findByText(/进度 · 第/)
  expect(screen.queryByText(/本次运行/)).toBeNull()
})
