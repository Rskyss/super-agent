import {mkdtempSync,mkdirSync,writeFileSync,rmSync} from 'node:fs'
import {tmpdir} from 'node:os'
import {join} from 'node:path'
import {afterEach,expect,it} from 'vitest'
import {readCardTrajectory} from '../src/trajectory.ts'
const dirs:string[]=[];afterEach(()=>dirs.splice(0).forEach(p=>rmSync(p,{recursive:true,force:true})))
it('05t只从真实事件明确记录的live/diff安全读内容，缺失或越界均不造链接',()=>{
 const project=mkdtempSync(join(tmpdir(),'sa-trajectory-artifacts-'));dirs.push(project)
 const c=join(project,'.collab/cards/CARD-022');mkdirSync(join(c,'流转'),{recursive:true})
 writeFileSync(join(c,'流转/001.md'),'真实报告');writeFileSync(join(c,'流转/001.log'),'真实运行输出');writeFileSync(join(c,'流转/001.diff'),'真实改动记录')
 writeFileSync(join(project,'forbidden.md'),'MUST_NOT_READ_OUTSIDE_CARD')
 const rows=[{seq:1,kind:'回复',src:'开发员',text:'流转/001.md',live:'流转/001.log',diff:'流转/001.diff'}, {seq:2,kind:'回复',src:'开发员',live:'../../../forbidden.md',diff:'../../../forbidden.md'}]
 writeFileSync(join(c,'流转.jsonl'),rows.map(r=>JSON.stringify(r)).join('\n'))
 const result=readCardTrajectory(project,'CARD-022') as any
 expect(result.rows[0].output).toBe('真实运行输出');expect(result.rows[0].diff).toBe('真实改动记录')
 expect(result.rows[1].output??'').toBe('');expect(result.rows[1].diff??'').toBe('');expect(JSON.stringify(result)).not.toContain('MUST_NOT_READ_OUTSIDE_CARD')
})
