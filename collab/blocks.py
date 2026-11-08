import json
import os
import shutil
import tempfile
from pathlib import Path

# 定义 BlockError
class BlockError(Exception):
    pass

from collab.config import DEFAULT_MODELS

# 定义预置卡 (只读)
_BUILTIN_MAP = {
    "codex": {
        "id": "codex",
        "kind": "cli",
        "name": "Codex",
        "command": "codex",
        "model": DEFAULT_MODELS["codex"],
        "run": ["exec", "--skip-git-repo-check", "-s", "workspace-write", "-m", "{model}", "{prompt}"],
        "login_check": {"args": ["login", "status"], "ok_words": ["Logged in"]}
    },
    "cursor": {
        "id": "cursor",
        "kind": "cli",
        "name": "Cursor",
        "command": "agent",
        "model": DEFAULT_MODELS["cursor"],
        "run": ["-p", "--trust", "--model", "{model}", "--output-format", "json", "{prompt}"],
        "login_check": {"args": ["status"], "ok_words": ["Logged in"]}
    },
    "agy": {
        "id": "agy",
        "kind": "cli",
        "name": "Agy",
        "command": "agy",
        "model": DEFAULT_MODELS["agy"],
        "run": ["-p", "{prompt}", "--model", "{model}", "--output-format", "json"]
    }
}
BUILTIN = list(_BUILTIN_MAP.keys())

DEFAULT_ROOT = Path.home() / "Library/Application Support/超级助手/积木库"

def home(root=None) -> Path:
    if root:
        return Path(root)
    env_home = os.environ.get("SUPER_AGENT_BLOCKS_HOME")
    if env_home:
        return Path(env_home)
    return DEFAULT_ROOT

def _validate_card_structure(card):
    if not isinstance(card, dict):
        raise BlockError("接法卡必须是字典")
    
    # 藏了密钥检查
    secret_fields = {'key', 'api_key', 'apikey', 'secret', 'token', 'password'}
    for key in card.keys():
        if key.lower() in secret_fields:
            raise BlockError(f"接法卡含密钥字段: {key}")
    
    # 递归检查字符串内容
    def check_secrets(obj):
        if isinstance(obj, str):
            if "sk-" in obj:
                # 检查后面有没有16个字母或数字
                import re
                if re.search(r'sk-[a-zA-Z0-9]{16,}', obj):
                    raise BlockError("接法卡含密钥内容")
        elif isinstance(obj, dict):
            for k, v in obj.items():
                if k.lower() in secret_fields: raise BlockError(f"接法卡含密钥字段: {k}")
                check_secrets(v)
        elif isinstance(obj, list):
            for item in obj:
                check_secrets(item)
    
    check_secrets(card)

def validate_card(card) -> dict:
    import copy
    card = copy.deepcopy(card)
    
    _validate_card_structure(card)
    
    # 3. 编号 id
    import re
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,39}', card.get("id", "")):
        raise BlockError("编号不合法")
    
    # 4. 类型 kind
    if card.get("kind") not in ("cli", "llm_api"):
        raise BlockError("类型只能是 cli 或 llm_api")
    
    # 5. 允许字段
    allowed_fields = {
        "cli": {'id', 'kind', 'name', 'command', 'run', 'model', 'version_args', 'login_check', 'done_when', 'source', 'note'},
        "llm_api": {'id', 'kind', 'name', 'provider', 'base_url', 'model', 'key_account', 'source', 'note'}
    }
    allowed = allowed_fields[card["kind"]]
    extra = set(card.keys()) - allowed
    if extra:
        raise BlockError(f"非法字段: {extra}")
        
    # 6. 名字
    if not card.get("name", "").strip() or len(card["name"].strip()) > 40:
        raise BlockError("名字不合法")
        
    # 7. 来源
    card["source"] = card.get("source", "clerk")
    if card["source"] not in ("builtin", "clerk"):
        raise BlockError("来源只能是 builtin 或 clerk")
        
    # 8. CLI 检查
    if card["kind"] == "cli":
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', card.get("command", "")):
            raise BlockError("命令不合法")
        
        run = card.get("run")
        if not isinstance(run, list) or not any("{prompt}" in str(x) for x in run):
            raise BlockError("run 必须包含 {prompt}")
        
        # 占位检查
        for item in run:
            import re
            placeholders = re.findall(r'\{([a-z_]+)\}', str(item))
            for p in placeholders:
                if p not in ('prompt', 'model'):
                    raise BlockError(f"非法占位: {{{p}}}")
        
        if any("{model}" in str(x) for x in run):
            if not card.get("model"):
                raise BlockError("模型字段不能为空")
        
        card["version_args"] = card.get("version_args", ["--version"])
        
        if "login_check" in card:
            lc = card["login_check"]
            if not isinstance(lc.get("args"), list) or not lc["args"] or not isinstance(lc.get("ok_words"), list) or not lc["ok_words"]:
                raise BlockError("登录检查格式不对")
        
        card["done_when"] = card.get("done_when", {"file": "连通检测.txt"})
        if '/' in card["done_when"].get("file", "") or card["done_when"].get("file", "").startswith('.'):
            raise BlockError("done_when 文件名不合法")
            
    # 9. LLM API 检查
    else:
        if not card.get("base_url", "").startswith("https://"):
            raise BlockError("base_url 必须以 https:// 开头")
        if not card.get("provider"): raise BlockError("服务商不能为空")
        if not card.get("model"): raise BlockError("模型不能为空")
        if not card.get("key_account"): raise BlockError("钥匙串不能为空")
        
    return card

def save_card(card, root=None) -> dict:
    validated = validate_card(card)
    base = home(root) / "基础层"
    base.mkdir(parents=True, exist_ok=True)
    
    tmp = base / f"{validated['id']}.json.tmp"
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(validated, f, ensure_ascii=False, indent=2)
    tmp.replace(base / f"{validated['id']}.json")
    
    return validated

def load_card(card_id, root=None) -> dict:
    import re
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,39}', card_id):
        raise BlockError("编号不合法")
    path = home(root) / "基础层" / f"{card_id}.json"
    if not path.exists():
        raise BlockError("没有这张卡")
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        raise BlockError("文件损坏")

def list_cards(root=None) -> list:
    base = home(root) / "基础层"
    if not base.exists(): return []
    res = []
    for p in sorted(base.glob("*.json")):
        try:
            with open(p, 'r', encoding='utf-8') as f:
                data = json.load(f)
            res.append(validate_card(data))
        except Exception as e:
            res.append({"id": p.stem, "problem": f"卡片不合法: {str(e)}"})
    return res

def remove_card(card_id, root=None):
    import re
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,39}', card_id):
        raise BlockError("编号不合法")
    path = home(root) / "基础层" / f"{card_id}.json"
    if not path.exists():
        raise BlockError("卡片不存在")
    path.unlink()

def builtin_card(name) -> dict:
    if name not in _BUILTIN_MAP:
        raise BlockError("没有这个预置卡")
    import copy
    card = copy.deepcopy(_BUILTIN_MAP[name])
    card["source"] = "builtin"
    return validate_card(card)

def build_args(card, prompt, model=None) -> list:
    validated = validate_card(card)
    if validated["kind"] == "llm_api":
        raise BlockError("llm_api 不能拼命令")
    
    model_to_use = model or validated.get("model", "")
    
    args = [validated["command"]]
    for item in validated["run"]:
        item_str = str(item)
        # 避免交叉替换：只替换原始模板中的占位符
        # 使用一个在任何字符串中都不会出现的临时占位符，或者分步替换但只在原始模板匹配时
        res = []
        i = 0
        while i < len(item_str):
            if item_str.startswith("{prompt}", i):
                res.append(prompt)
                i += 8
            elif item_str.startswith("{model}", i):
                res.append(model_to_use)
                i += 7
            else:
                res.append(item_str[i])
                i += 1
        args.append("".join(res))
    return args

def add_record(card_id, record, root=None) -> dict:
    import re
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,39}', card_id):
        raise BlockError("编号不合法")
    if not isinstance(record, dict) or "type" not in record:
        raise BlockError("记录必须有 type")
    _validate_card_structure(record)
    
    from datetime import datetime
    record["time"] = datetime.now().isoformat(timespec='seconds')
    
    rec_dir = home(root) / "记录"
    rec_dir.mkdir(parents=True, exist_ok=True)
    with open(rec_dir / f"{card_id}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record

def records(card_id, root=None, limit=None) -> list:
    rec_path = home(root) / "记录" / f"{card_id}.jsonl"
    if not rec_path.exists(): return []
    
    res = []
    with open(rec_path, 'r', encoding='utf-8') as f:
        for line in f:
            try:
                data = json.loads(line)
                if isinstance(data, dict):
                    res.append(data)
            except: pass
    
    if limit: return res[-limit:]
    return res

def last_record(card_id, root=None, type=None):
    recs = records(card_id, root=root)
    if not recs: return None
    if type:
        recs = [r for r in recs if r.get("type") == type]
    return recs[-1] if recs else None
