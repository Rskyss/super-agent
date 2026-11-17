// 第七批 7-3：从流转记录算“本次运行 · 改了 N 个文件 · 守住 N 条规矩”（主机端）。
import {expect,it} from 'vitest'
import {extractRunSummary} from '../src/run-summary.ts'

const ev=(kind:string,extra:Record<string,unknown>={})=>({kind,src:'程序',...extra})

it('从流转记录取：改了几个文件、通过了哪几项自动检查（取每项最新一次）',()=>{
  const run=extractRunSummary([
    ev('范围',{ok:true,summary:'改动范围正常（2 个文件）',text:'a.py\nb.py'}),
    ev('老测试',{result:'没过',summary:'跑了老测试：没过'}),
    ev('老测试',{result:'过',summary:'跑了和这次改动有关的老测试（t1）：过'}),
    ev('页面检查',{result:'过',summary:'页面检查：过'}),
  ])!
  expect(run.changedFiles).toBe(2)
  expect(run.rules.map(r=>[r.label,r.ok])).toEqual([['只改方案允许的地方',true],['相关老测试没被改坏',true],['页面检查通过',true]])
})

it('没跑过就没有本次运行；没检查过的项不算',()=>{
  expect(extractRunSummary([ev('派活',{dst:'测试员'})])).toBeUndefined()
  const run=extractRunSummary([ev('范围',{ok:true,summary:'改动范围正常（5 个文件）'})])!
  expect(run.rules).toHaveLength(1)
})

it('没守住的不计入“守住”，但点开能看到',()=>{
  const run=extractRunSummary([ev('范围',{ok:true,summary:'改动范围正常（1 个文件）'}),ev('页面检查',{result:'没过',summary:'页面检查：没过'})])!
  expect(run.rules.filter(r=>r.ok)).toHaveLength(1)
  expect(run.rules.find(r=>!r.ok)?.label).toBe('页面检查通过')
})

