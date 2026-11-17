// @vitest-environment jsdom
import {cleanup,fireEvent,render,screen,within} from '@testing-library/react'
import {afterEach,expect,it,vi} from 'vitest'
import {DecisionDock} from '../src/client/DecisionDock.tsx'
afterEach(cleanup)
const ok=<T,>(value:T)=>({ok:true as const,value})
const plan=JSON.stringify({version:2,plan:'# 拆解方案\n\n## 要做什么（一句话）\n给页面加问候\n\n## 拆成几步，每步做什么、产出什么\n1. 补测试\n2. 写问候\n\n## 你会看到什么结果\n输入名字显示问候\n\n## 明确不做什么\n不联网\n\n## 需要你确认的问题\n是否中文？建议中文\n',allowed:['web/index.html'],test_command:'npm test'})
function setup(code:string,actions:string[]){const raw={card:{id:'CARD-022',title:'加问候',phase:{code,label:code==='blocked'?'停下了':'等你验收',actions,detail:code==='blocked'?'复查3轮仍有问题，请决定':''}},workElapsed:90,elapsed:500,accept:{result:'已完成中文问候',how:'输入名字试一次',can_preview:true,copy_there:true,criteria:['输入名字有问候','空名字也问好','原测试通过'],changeNotes:['你确认第3条补了新增测试']},stopReviews:[{round:1,found:'会重复',changed:'加了检查'},{round:2,found:'检查太晚',changed:'移到操作前'},{round:3,found:'仍可能并发重复',changed:'无修改记录'}],stopAdvice:'建议先验收再判断是否处理剩余问题'};const props={card:'CARD-022',view:async()=>ok(JSON.stringify(raw)),plan:async()=>ok(plan),cardAction:vi.fn(async()=>ok({ok:true,message:''})),preview:vi.fn(async()=>ok({ok:true,mode:'web',url:'http://127.0.0.1:3099/'})),openPreview:vi.fn(),readDraft:()=>'',clearDraft:vi.fn(),hasAttachments:()=>false};render(<DecisionDock {...props}/>);return props}
it('05验收默认收详细标准但三个主动作始终可见，展开才看标准与追加记录',async()=>{
 setup('review',['accept','revise','discard']);await screen.findByText('做好了，请你验收')
 expect(screen.getByText(/3 条验收标准全部/)).toBeTruthy()
 expect(screen.queryByText('输入名字有问候',{exact:true})).toBeNull()
 for(const name of ['打开预览窗口','没问题，放进项目','继续改'])expect(screen.getByRole('button',{name})).toBeTruthy()
 expect(screen.queryByRole('button',{name:'先不做'})).toBeNull()
 fireEvent.click(screen.getByRole('button',{name:'展开'}))
 for(const text of ['输入名字有问候','空名字也问好','原测试通过','你确认第3条补了新增测试'])expect(screen.getByText(text,{exact:true})).toBeTruthy()
 for(const name of ['打开预览窗口','没问题，放进项目','继续改'])expect(screen.getByRole('button',{name})).toBeTruthy()
})
it('04a标题取真实方案版次，七项齐全且图上没有的先不做不露',async()=>{
 setup('plan_ready',['approve','revise','discard']);expect(await screen.findByText('方案 · 第 2 版')).toBeTruthy()
 for(const label of ['要做什么','分几步','做完你会看到','不做什么','只动这些地方','怎么算做好','要你拍板的'])expect(screen.getByText(label,{exact:true})).toBeTruthy()
 expect(screen.getByRole('button',{name:'批准，开工'})).toBeTruthy();expect(screen.getByRole('button',{name:'我要改'})).toBeTruthy()
 expect(screen.queryByRole('button',{name:'先不做'})).toBeNull()
})
it('14停下卡显示真实三轮表格和建议，四个动作按图排序',async()=>{
 setup('blocked',['accept_issues','revise','resume','discard'])
 expect(await screen.findByText('复查 3 轮都没通过，停下了')).toBeTruthy()
 const table=await screen.findByRole('table')
 for(const name of ['轮次','复查发现','写代码改了什么'])expect(within(table).getByRole('columnheader',{name})).toBeTruthy()
 expect(within(table).getAllByRole('row')).toHaveLength(4)
 expect(screen.getByText('建议先验收再判断是否处理剩余问题')).toBeTruthy()
 const names=['接受剩下的问题，去验收','按我说的改','再跑一次','先不做'];const buttons=names.map(name=>screen.getByRole('button',{name}))
 for(let i=0;i<buttons.length-1;i++)expect(buttons[i]!.compareDocumentPosition(buttons[i+1]!)&Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
})
it('05打开预览后卡内只留一次使用说明，不露草图没有的裸地址或备用链接',async()=>{
 const p=setup('review',['accept','revise','discard']);await screen.findByText('做好了，请你验收')
 fireEvent.click(screen.getByRole('button',{name:'打开预览窗口'}));await screen.findByText(/预览已经打开/)
 expect(p.openPreview).toHaveBeenCalledWith('http://127.0.0.1:3099/')
 expect(screen.queryByText(/127\.0\.0\.1:3099/)).toBeNull();expect(screen.queryByRole('link')).toBeNull()
 expect(screen.getAllByText(/输入名字试一次/)).toHaveLength(1)
})
