"""任务卡片截图附件的校验、原子保存与派活路径交接。"""
from __future__ import annotations

import base64
import binascii
import os
import re
from datetime import datetime
from pathlib import Path

from collab.flow import read_flow

ATTACHMENT_DIR_NAME = "附件"
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 单张图片最大 10 MiB（含）
MAX_ATTACHMENTS = 20  # 单次追加要求最多附带图片数量
_MAX_B64_CHARS = ((MAX_ATTACHMENT_BYTES + 2) // 3) * 4

_MEDIA_SPECS = {
    "image/png": (".png", b"\x89PNG\r\n\x1a\n"),
    "image/jpeg": (".jpg", b"\xff\xd8\xff"),
}
_SAFE_FILENAME = re.compile(r"^\d[\d_-]*\.(png|jpg)$")
_B64_CHARS = re.compile(r"^[A-Za-z0-9+/=]+$")


class AttachmentError(ValueError):
    """图片附件不合规或保存路径不安全时抛出的可读错误。"""


def validate_attachments(attachments) -> list:
    """校验全部输入图片（类型、纯 base64、10MiB 上限、内容签名），不写入任何文件。"""
    if attachments is None:
        return []
    if not isinstance(attachments, list):
        raise AttachmentError("图片附件格式不对")
    if len(attachments) == 0:
        return []
    if len(attachments) > MAX_ATTACHMENTS:
        raise AttachmentError(f"单次最多附带 {MAX_ATTACHMENTS} 张图片")

    validated = []
    for idx, item in enumerate(attachments, 1):
        if not isinstance(item, dict):
            raise AttachmentError(f"第 {idx} 张图片格式不对")
        media = item.get("mediaType")
        if not isinstance(media, str) or media not in _MEDIA_SPECS:
            raise AttachmentError(f"第 {idx} 张图片只支持 PNG 或 JPG 格式")
        raw_b64 = item.get("data")
        if not isinstance(raw_b64, str) or not raw_b64:
            raise AttachmentError(f"第 {idx} 张图片内容为空")
        if not raw_b64.isascii() or not _B64_CHARS.fullmatch(raw_b64):
            raise AttachmentError(f"第 {idx} 张图片 base64 编码无效")
        if len(raw_b64) > _MAX_B64_CHARS + 4:
            raise AttachmentError(f"第 {idx} 张图片不能超过 10MB")
        try:
            raw_bytes = base64.b64decode(raw_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise AttachmentError(f"第 {idx} 张图片 base64 编码无效") from exc
        if not raw_bytes:
            raise AttachmentError(f"第 {idx} 张图片内容为空")
        if len(raw_bytes) > MAX_ATTACHMENT_BYTES:
            raise AttachmentError(f"第 {idx} 张图片不能超过 10MB")
        ext, magic = _MEDIA_SPECS[media]
        if not raw_bytes.startswith(magic):
            raise AttachmentError(f"第 {idx} 张图片内容与格式不匹配")
        validated.append((ext, raw_bytes))
    return validated


def check_attachment_dir(card_dir: Path) -> Path:
    """只读检查卡片与附件目录是否安全（禁止软链接与目录逃逸），不创建目录。"""
    cdir = Path(card_dir)
    if cdir.is_symlink() or not cdir.is_dir():
        raise AttachmentError("找不到这张卡片的目录")
    att_dir = cdir / ATTACHMENT_DIR_NAME
    if os.path.lexists(att_dir):
        if att_dir.is_symlink():
            raise AttachmentError("附件目录不能是软链接")
        if not att_dir.is_dir():
            raise AttachmentError("附件路径不是有效目录")
        if att_dir.resolve().parent != cdir.resolve():
            raise AttachmentError("附件目录超出了当前任务卡片范围")
    return att_dir


def cleanup_saved_attachments(att_dir: Path, created_paths: list, created_dir: bool) -> None:
    """仅清理本次新写入的附件文件；若附件目录也是本次新建且已空则一并移除。"""
    for path in created_paths or []:
        try:
            if os.path.lexists(path) and not Path(path).is_symlink():
                Path(path).unlink(missing_ok=True)
        except OSError:
            pass
    if created_dir and att_dir is not None:
        try:
            if os.path.lexists(att_dir) and not att_dir.is_symlink() and att_dir.is_dir():
                if not any(att_dir.iterdir()):
                    att_dir.rmdir()
        except OSError:
            pass


def save_attachments(card_dir: Path, validated_items: list) -> tuple:
    """将已校验的原始图片字节独占写入附件目录，返回 (相对路径列表, 新建路径列表, 是否新建目录)。"""
    if not validated_items:
        return [], [], False

    cdir = Path(card_dir)
    att_dir = check_attachment_dir(cdir)
    dir_existed = os.path.lexists(att_dir)
    if not dir_existed:
        try:
            att_dir.mkdir(exist_ok=False)
        except FileExistsError:
            dir_existed = True
    # 再次复核防止并发替换成软链接
    if att_dir.is_symlink() or not att_dir.is_dir() or att_dir.resolve().parent != cdir.resolve():
        raise AttachmentError("附件目录不安全，已取消保存")

    created_dir = not dir_existed
    created_paths: list[Path] = []
    rel_paths: list[str] = []
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    try:
        for idx, (ext, raw_bytes) in enumerate(validated_items, 1):
            for seq in range(0, 1000):
                name = f"{stamp}_{idx}{ext}" if seq == 0 else f"{stamp}_{idx}_{seq}{ext}"
                target = att_dir / name
                try:
                    fd = os.open(target, flags, 0o644)
                except FileExistsError:
                    continue
                try:
                    view = memoryview(raw_bytes)
                    written = 0
                    while written < len(raw_bytes):
                        written += os.write(fd, view[written:])
                    os.fsync(fd)
                except BaseException:
                    os.close(fd)
                    if os.path.lexists(target) and not target.is_symlink():
                        target.unlink(missing_ok=True)
                    raise
                os.close(fd)
                created_paths.append(target)
                rel_paths.append(f"{ATTACHMENT_DIR_NAME}/{name}")
                break
            else:
                raise AttachmentError("保存图片时文件名冲突过多，请稍后重试")
    except BaseException:
        cleanup_saved_attachments(att_dir, created_paths, created_dir)
        raise

    return rel_paths, created_paths, created_dir


def recorded_attachment_paths(card_dir: Path) -> list:
    """只读取本卡流转里“追加要求”真实登记过的图片，并严格校验白名单与无软链接。"""
    cdir = Path(card_dir)
    if not cdir.is_dir() or cdir.is_symlink():
        return []
    att_dir = cdir / ATTACHMENT_DIR_NAME
    if not os.path.lexists(att_dir) or att_dir.is_symlink() or not att_dir.is_dir():
        return []
    card_real = cdir.resolve()
    att_real = att_dir.resolve()
    if att_real.parent != card_real:
        return []

    prefix = f"{ATTACHMENT_DIR_NAME}/"
    seen = set()
    resolved = []
    for ev in read_flow(cdir):
        if ev.get("kind") != "追加要求":
            continue
        items = ev.get("attachments")
        if not isinstance(items, list):
            continue
        for rel in items:
            if not isinstance(rel, str) or "\\" in rel or not rel.startswith(prefix):
                continue
            name = rel[len(prefix):]
            if not _SAFE_FILENAME.fullmatch(name):
                continue
            candidate = att_dir / name
            if not os.path.lexists(candidate) or candidate.is_symlink() or not candidate.is_file():
                continue
            cand_real = candidate.resolve()
            if cand_real.parent != att_real:
                continue
            if cand_real not in seen:
                seen.add(cand_real)
                resolved.append(cand_real)
    return resolved


def attachment_prompt(card_dir: Path) -> str:
    """生成给各执行角色与调度员看的附图指令；无真实记录附件时返回空字符串。"""
    paths = recorded_attachment_paths(Path(card_dir))
    if not paths:
        return ""
    lines = [
        "",
        "本任务卡片附带以下截图，请先实际打开这些图片看清楚，再动手修改或检查（不要把图片复制进工作区）：",
    ]
    lines.extend(f"- {p}" for p in paths)
    return "\n".join(lines)
