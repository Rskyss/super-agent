window.__ModuleLoader__.load({
  id: '@local/sp-shell-probe',
  factory(require) {
    const React = require('react')
    const h = React.createElement
    const box = { display: 'flex', flexDirection: 'column', height: '100%', padding: '16px 12px', boxSizing: 'border-box', gap: 12, color: 'var(--dsw-text-primary, inherit)' }
    function Side(props) {
      const [tab, setTab] = React.useState('chats')
      return h('div', { style: box, 'data-sp-probe': 'sidebar', 'data-window-drag': '' },
        h('div', { style: { display: 'flex', alignItems: 'center', gap: 8, marginTop: 28 } },
          h('div', null, h('div', { style: { fontWeight: 700, fontSize: 16 } }, '超级玩家 ', h('span', { style: { fontSize: 11, opacity: .6 } }, 'v0.1')), h('div', { style: { fontSize: 11, opacity: .6 } }, 'AIsoup Lab'))),
        h('button', { 'data-sp-probe': 'new-chat', onClick: () => props.ws.startSession(), style: { padding: '10px', borderRadius: 8 } }, '+ 新会话'),
        h('div', { style: { display: 'flex', gap: 4 } },
          h('button', { onClick: () => setTab('chats'), style: { flex: 1, fontWeight: tab === 'chats' ? 700 : 400 } }, '对话'),
          h('button', { onClick: () => setTab('tasks'), style: { flex: 1, fontWeight: tab === 'tasks' ? 700 : 400 } }, '任务')),
        h('div', { style: { flex: 1, fontSize: 13, opacity: .7 } }, tab === 'chats' ? '（实测：对话列表放这里）' : '（实测：任务列表放这里）'),
        h('button', { 'data-sp-probe': 'settings', onClick: () => props.layout.selectPanel(null), style: { textAlign: 'left' } }, '⚙ 设置'))
    }
    function Right() {
      return h('div', { style: { ...box, borderLeft: '1px solid var(--dsw-border-subtle, #333)' }, 'data-sp-probe': 'rightbar' }, h('div', { style: { fontWeight: 600 } }, '进度 · 第 0 步 / 共 7 步'), h('div', { style: { fontSize: 13, opacity: .7 } }, '（实测：我们自己的右栏）'))
    }
    function Header() {
      return h('div', { 'data-sp-probe': 'header', style: { padding: '14px 24px', fontWeight: 600, borderBottom: '1px solid var(--dsw-border-subtle, #ddd)' } }, '（实测：我们自己的会话标题栏）', h('span', { style: { fontWeight: 400, opacity: .6, marginLeft: 8 } }, '超级玩家'))
    }
    return {
      inject: ['slots', 'layout', 'uiWorkspace'],
      apply(ctx) {
        ctx.slots.inject('sidebar', () => ctx.slots.register({ name: 'sidebar', inject: () => ({ ws: ctx.uiWorkspace, layout: ctx.layout }) }, Side))
        ctx.slots.inject('conversation.header', () => ctx.slots.register({ name: 'conversation.header', priority: -100 }, Header))
        ctx.slots.inject('rightbar', () => ctx.slots.register({ name: 'rightbar', priority: -100 }, Right))
      },
    }
  },
})
