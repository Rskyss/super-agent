// 复查第 1 轮中等 14、15：“看改动”要逐字显示真实改动（不把代码里的英文换成产品叫法），后台存得下的大小这边也要读得出。
import {mkdtempSync,mkdirSync,writeFileSync,rmSync} from 'node:fs'
import {tmpdir} from 'node:os'
import {join} from 'node:path'
import {afterEach,expect,it} from 'vitest'
import {readCardTrajectory} from '../src/trajectory.ts'
const dirs:string[]=[];afterEach(()=>dirs.splice(0).forEach(p=>rmSync(p,{recursive:true,force:true})))
function setup(diffText:string){
  const project=mkdtempSync(join(tmpdir(),'sa-traj-diff-'));dirs.push(project)
  const c=join(project,'.collab/cards/CARD-001');mkdirSync(join(c,'改动'),{recursive:true})
  writeFileSync(join(c,'改动/1.diff'),diffText)
  writeFileSync(join(c,'流转.jsonl'),[
    {seq:1,kind:'派活',src:'调度员',dst:'开发员',step:'开发',time:'2026-11-16T09:00:00'},
    {seq:2,kind:'回复',src:'开发员',ok:true,reply_to:1,diff:'改动/1.diff',summary:'改好了',time:'2026-11-16T09:01:00'},
  ].map(e=>JSON.stringify(e)).join('\n')+'\n')
  return project
}
it('看改动逐字保留代码原文',()=>{
  const project=setup('+Claude Code uses API JSON\n')
  const row=readCardTrajectory(project,'CARD-001').rows.find(r=>r.diff)
  expect(row?.diff).toBe('+Claude Code uses API JSON\n')
})
it('后台存的上限以内（接近 500KB）都能读出来',()=>{
  const big='+'+'abcde\n'.repeat(80000)
  expect(big.length).toBeGreaterThan(480000)
  const project=setup(big)
  expect(readCardTrajectory(project,'CARD-001').rows.find(r=>r.diff)?.diff?.length).toBe(big.length)
})
