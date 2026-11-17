// @vitest-environment jsdom
import {act,cleanup,fireEvent,render,screen} from '@testing-library/react'
import {useEffect,useState} from 'react'
import {afterEach,expect,it,vi} from 'vitest'
const modules=import.meta.glob('../src/client/TaskPageChrome.tsx',{eager:true}) as Record<string,Record<string,any>>
afterEach(cleanup)
function bridge(){const C=modules['../src/client/TaskPageChrome.tsx']?.TaskSessionViewBridge;expect(C,'缺少空任务也可选轨迹的薄视图桥').toBeTypeOf('function');return C}
const trajectory=()=> <table aria-label="真实流转"><tbody><tr><td>写方案完成</td></tr></tbody></table>
it('空任务也能显示已有真实轨迹，原Session持续挂载维护草稿持久化',async()=>{
 const C=bridge(),mount=vi.fn(),unmount=vi.fn()
 function Native(){useEffect(()=>{mount();return unmount},[]);return null}
 const native=vi.fn(()=> <Native/>),p={isTask:true,view:'chat',renderNative:native,renderTrajectory:trajectory}
 const v=render(<C {...p}/>);await act(async()=>{});expect(mount).toHaveBeenCalledTimes(1)
 v.rerender(<C {...p} view="superagent-trajectory"/>);expect(screen.getByRole('table',{name:'真实流转'})).toBeTruthy()
 expect(native).toHaveBeenLastCalledWith({view:'chat'});expect(unmount).not.toHaveBeenCalled()
 v.rerender(<C {...p}/>);expect(screen.queryByRole('table')).toBeNull();expect(mount).toHaveBeenCalledTimes(1);expect(unmount).not.toHaveBeenCalled()
})
it('切轨迹只藏原聊天，保留原节点和草稿；普通会话仍走原Session',()=>{
 const C=bridge()
 function Native(){const[draft,setDraft]=useState('');return <input aria-label="原Session草稿" value={draft} onChange={e=>setDraft(e.target.value)}/>}
 const p={isTask:true,view:'chat',renderNative:()=> <Native/>,renderTrajectory:trajectory}
 const v=render(<C {...p}/>);const input=screen.getByRole('textbox',{name:'原Session草稿'})
 fireEvent.change(input,{target:{value:'草稿必须保留'}})
 v.rerender(<C {...p} view="superagent-trajectory"/>);expect(screen.queryByRole('textbox')).toBeNull();expect(input.isConnected).toBe(true)
 v.rerender(<C {...p}/>);expect(screen.getByRole('textbox')).toBe(input);expect((input as HTMLInputElement).value).toBe('草稿必须保留')
 v.rerender(<C {...p} isTask={false} view="superagent-trajectory"/>);expect(screen.queryByRole('table')).toBeNull();expect(screen.getByRole('textbox')).toBe(input)
})
