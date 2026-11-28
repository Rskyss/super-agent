"""用法：python3 docs/流程数据/跨项目模型对比.py（每批跑完重跑一次）。
跨项目汇总：每个节点用时、需求类型、模型，看哪个模型适合哪一步。只读 .collab/cards/*/流转.jsonl。"""
import json, re, sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
PROJ = {'开发助手': '/Users/rs/Desktop/project/开发助手', 'train': '/Users/rs/Desktop/project/AI策略游戏/train',
        '超级玩家': '/Users/rs/Desktop/project/超级玩家/super-agent', 'chrild': '/Users/rs/Desktop/project/chrild',
        'yt-scribe': '/Users/rs/Desktop/project/视频提取文字/yt-scribe-clone'}
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / '模型对比-跨项目.md'
def ev_of(c):
    out = []
    if not (c / '流转.jsonl').exists(): return out
    for l in (c / '流转.jsonl').read_text(encoding='utf-8', errors='ignore').splitlines():
        try: out.append(json.loads(l))
        except Exception: pass
    return out
def kind_of(proj, card):
    t = ''
    try: t = (card / '需求卡.md').read_text(encoding='utf-8', errors='ignore')
    except OSError: pass
    if proj == '开发助手' or proj == '超级玩家': return '工具自身（Python/Swift）'
    if 'migrations' in t and re.search(r'supabase/migrations/\S+\.sql', t): return '网页·含数据库迁移'
    if re.search(r'并发|竞态|覆盖|权限|锁', t[:1500]): return '网页·规则/并发/权限'
    if re.search(r'样式|文案|排版|颜色|浅底|输入框|显示', t[:600]): return '网页·样式文案'
    return '网页·功能'
def norm_model(m):
    m = (m or '（没记）').replace('xhigh', '极高').replace('high', '高').replace('medium', '中')
    return m
agg = defaultdict(lambda: dict(n=0, sec=0.0, tok=0, ok=0, first_pass=0, dev_n=0, rv_pass=0, rv_n=0))
kind_agg = defaultdict(lambda: dict(n=0, sec=0.0, first_pass=0))
cards_rows = []
for proj, root in PROJ.items():
    for c in sorted(Path(root, '.collab/cards').glob('CARD-*')):
        ev = ev_of(c)
        if not ev: continue
        kind = kind_of(proj, c)
        tasks = {e.get('seq'): e for e in ev if e.get('kind') == '派活'}
        start = next((e.get('ts') for e in ev if e.get('ts') and e.get('kind') in ('开跑', '开工')), None)
        wait = next((e.get('ts') for e in ev if e.get('ts') and e.get('kind') == '结束'), None)
        merged = next((e.get('ts') for e in ev if e.get('ts') and e.get('kind') == '合并'), None)
        returns = sum(1 for e in ev if e.get('kind') == '退回')
        manual = sum(1 for e in ev if e.get('kind') == '转人工')
        fails = sum(1 for e in ev if e.get('kind') == '判定' and e.get('result') == '没过')
        work = 0.0; models = set()
        for i, e in enumerate(ev):
            if e.get('kind') != '回复' or e.get('seconds') is None: continue
            t = tasks.get(e.get('reply_to')) or {}
            step = e.get('step') or t.get('step') or ''
            tool = e.get('tool') or t.get('tool') or ''
            model = norm_model(t.get('model'))
            sec = float(e.get('seconds') or 0); work += sec
            tk = e.get('tokens') or {}
            tok = int(tk.get('total') or 0) if isinstance(tk, dict) else 0
            a = agg[(step, tool, model)]
            a['n'] += 1; a['sec'] += sec; a['tok'] += tok; a['ok'] += e.get('ok') is True
            models.add(f'{step}:{tool}·{model}')
            nxt = [x for x in ev[i + 1:i + 12]]
            if step == '开发' and e.get('ok') is True:
                j = next((x for x in nxt if x.get('kind') == '判定'), None)
                if j: a['dev_n'] += 1; a['first_pass'] += j.get('result') == '过'
                ka = kind_agg[(kind, tool, model)]; ka['n'] += 1; ka['sec'] += sec
                if j and j.get('result') == '过': ka['first_pass'] += 1
            if step == '复查' and e.get('ok') is True:
                r = next((x for x in nxt if x.get('kind') == '复查结论'), None)
                s = str(e.get('summary') or '')
                passed = ('不通过' not in s and '通过' in s) if not r else r.get('result') != '退回'
                a['rv_n'] += 1; a['rv_pass'] += passed
        f = lambda t: '—' if not (start and t) else f'{(float(t) - float(start)) / 60:.0f}'
        cards_rows.append((proj, c.name, (ev[0].get('summary') or '')[:30].replace('|', '／'), kind, f(wait), f(merged), f'{work/60:.0f}', returns, fails, manual))
L = [f'# 模型对比（跨项目，自动生成）', '', f'> 生成：{datetime.now():%Y-%m-%d %H:%M}（纽约时间）。数据：各项目 .collab/cards/*/流转.jsonl。“用时”是干活的工具自己那一步的耗时，不含排队、等人、Claude/Gemini 接手。',
     '> “一次过”= 写代码交活后程序紧接着跑验收测试就全过；“复查放行”= 复查员给通过的比例（复查严不严也看这个，越低说明它挑得越多，不一定是坏事）。', '',
     '## 一、按“步骤 + 工具 + 模型”', '', '| 步骤 | 工具 · 模型 | 次数 | 平均用时（分） | 平均 token | 交成率 | 写代码一次过 | 复查放行 |', '|---|---|---|---|---|---|---|---|']
for (step, tool, model), a in sorted(agg.items(), key=lambda kv: (kv[0][0], -kv[1]['n'])):
    if a['n'] < 1: continue
    fp = f"{a['first_pass']}/{a['dev_n']}" if a['dev_n'] else '—'
    rv = f"{a['rv_pass']}/{a['rv_n']}" if a['rv_n'] else '—'
    L.append(f"| {step} | {tool} · {model} | {a['n']} | {a['sec']/a['n']/60:.1f} | {a['tok']/a['n']/1e4:.0f} 万 | {a['ok']}/{a['n']} | {fp} | {rv} |")
L += ['', '## 二、写代码：按需求类型 × 模型', '', '| 需求类型（按需求卡粗分） | 工具 · 模型 | 轮数 | 平均用时（分） | 一次过 |', '|---|---|---|---|---|']
for (k, tool, model), a in sorted(kind_agg.items()):
    L.append(f"| {k} | {tool} · {model} | {a['n']} | {a['sec']/a['n']/60:.1f} | {a['first_pass']}/{a['n']} |")
L += ['', '## 三、每张卡', '', '| 项目 | 卡 | 主题 | 类型 | 开跑→待验收（分） | 开跑→放进项目（分） | 干活合计（分） | 复查退回 | 测试没过 | 转人工 |', '|---|---|---|---|---|---|---|---|---|---|']
for r in cards_rows: L.append('| ' + ' | '.join(str(x) for x in r) + ' |')
OUT.write_text('\n'.join(L) + '\n', encoding='utf-8'); print(len(cards_rows), 'cards')
