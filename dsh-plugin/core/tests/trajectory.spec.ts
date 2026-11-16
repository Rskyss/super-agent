/// <reference types="vite/client" />
import { mkdtempSync,mkdirSync,writeFileSync,symlinkSync,rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach,expect,it } from 'vitest'
const modules=import.meta.glob('../src/trajectory.ts',{eager:true}) as Record<string,any>
const dirs:string[]=[]
afterEach(()=>dirs.splice(0).forEach(p=>rmSync(p,{recursive:true,force:true})))
function setup(){const project=mkdtempSync(join(tmpdir(),'sa-trajectory-'));dirs.push(project);const card=join(project,'.collab/cards/CARD-001');mkdirSync(join(card,'流转'),{recursive:true});const read=modules['../src/trajectory.ts']?.readCardTrajectory;expect(read,'缺少真实轨迹读取器').toBeTypeOf('function');return{project,card,read}}
it('流转每一步保留，明确用时才能计时，未知用量不编造',()=>{const{project,card,read}=setup();writeFileSync(join(card,'流转/002-回复.md'),'结论：通过\n详细原文');writeFileSync(join(card,'流转.jsonl'),[{seq:1,time:'2026-11-15T09:00:00',kind:'派活',src:'调度员',dst:'写代码',summary:'开始改待办',text:''},{seq:2,time:'2026-11-15T09:01:00',kind:'回复',src:'写代码',summary:'做好了',seconds:60,text:'流转/002-回复.md'}].map(x=>JSON.stringify(x)).join('\n'));const r=read(project,'CARD-001');expect(r.rows).toHaveLength(2);expect(r.rows[0].seconds).toBeNull();expect(r.rows[1]).toMatchObject({actor:'写代码',seconds:60,raw:'结论：通过\n详细原文',usage:null});expect(r.distribution.reduce((n:number,x:any)=>n+x.seconds,0)).toBe(60)})
it('坏行不挡好记录，原文路径不能逃出卡片目录或穿过软链接',()=>{const{project,card,read}=setup();const outside=join(project,'outside');writeFileSync(outside,'outside-secret');symlinkSync(outside,join(card,'流转/link.md'));writeFileSync(join(card,'流转.jsonl'),'bad json\n'+[{seq:1,text:'../../outside',summary:'第一步'},{seq:2,text:'流转/link.md',summary:'第二步'}].map(x=>JSON.stringify(x)).join('\n'));const r=read(project,'CARD-001');expect(r.rows).toHaveLength(2);expect(JSON.stringify(r)).not.toContain('outside-secret');expect(()=>read(project,'../CARD-001')).toThrow()})
