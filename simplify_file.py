"""文件简化模块 — 模拟文件被反复压缩后内容逐渐丢失

核心接口：simplify_file(path, level) -> bytes
按文件类型分派到不同的简化策略，level 越高简化越狠。
所有操作在本地执行，不联网，不生成 UI，不生成压缩逻辑。
"""

from __future__ import annotations

import io
import math
import os
import random
import re
import shutil
import struct
import tarfile
import tempfile
import wave
import zipfile
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Union

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEFAULT_THRESHOLD = 10
_BLOCK_SIZE = 4096

# 扩展名 → 类型映射
_EXT_MAP: Dict[str, str] = {}
for _ext in ("txt", "md", "json", "xml", "csv", "py", "js", "html", "css",
             "yaml", "yml", "toml", "ini", "cfg", "log", "rtf", "svg"):
    _EXT_MAP[_ext] = "text"
for _ext in ("docx", "pptx", "xlsx"):
    _EXT_MAP[_ext] = "document"
for _ext in ("pdf",):
    _EXT_MAP[_ext] = "document"
for _ext in ("jpg", "jpeg", "png", "gif", "webp", "bmp", "tiff", "tif", "ico"):
    _EXT_MAP[_ext] = "image"
for _ext in ("mp3", "wav", "flac", "ogg", "aac", "wma", "m4a"):
    _EXT_MAP[_ext] = "audio"
for _ext in ("mp4", "avi", "mkv", "mov", "wmv", "flv", "webm"):
    _EXT_MAP[_ext] = "video"
for _ext in ("zip", "tar", "gz", "tgz", "bz2", "7z", "rar"):
    _EXT_MAP[_ext] = "archive"

# Magic bytes → 类型
_MAGIC: List[Tuple[bytes, str]] = [
    (b"\x50\x4b\x03\x04", "archive"),   # ZIP / DOCX / PPTX / XLSX
    (b"\x1f\x8b", "archive"),            # GZIP
    (b"BZh", "archive"),                 # BZIP2
    (b"\x52\x61\x72", "archive"),        # RAR
    (b"7z\xbc\xaf", "archive"),          # 7z
    (b"\x89PNG", "image"),
    (b"\xff\xd8\xff", "image"),          # JPEG
    (b"GIF8", "image"),
    (b"RIFF", "audio"),                  # WAV (could also be AVI)
    (b"ID3", "audio"),                   # MP3 with ID3
    (b"\xff\xfb", "audio"),              # MP3
    (b"fLaC", "audio"),                  # FLAC
    (b"OggS", "audio"),                  # OGG
    (b"%PDF", "document"),               # PDF
    (b"\x00\x00\x00\x18ftypmp4", "video"),  # MP4 (common)
    (b"\x00\x00\x00\x1cftypisom", "video"),
    (b"\x00\x00\x00\x20ftypqt  ", "video"),  # MOV
]

# ---------------------------------------------------------------------------
# 可选依赖 — 缺失时优雅降级
# ---------------------------------------------------------------------------

try:
    from PIL import Image as PILImage
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

try:
    import numpy as _np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

try:
    from pydub import AudioSegment as _AudioSegment
    _HAS_PYDUB = True
except ImportError:
    _HAS_PYDUB = False

try:
    import cv2 as _cv2
    _HAS_CV2 = True
except ImportError:
    _HAS_CV2 = False


# ===========================================================================
# detect_type
# ===========================================================================

def detect_type(path: Union[str, Path]) -> str:
    """检测文件类型，返回 text/document/image/audio/video/archive/binary。

    优先用 magic bytes，扩展名辅助。
    """
    p = Path(path)
    if not p.exists():
        return "binary"

    # 1) Magic bytes
    try:
        head = p.read_bytes()[:32]
        for magic, ftype in _MAGIC:
            if head.startswith(magic):
                # ZIP 可能是 docx/pptx/xlsx
                if magic == b"\x50\x4b\x03\x04":
                    if _is_office_zip(head, p):
                        return "document"
                    # 也可能是 epub 等，但先归 archive
                return ftype
    except OSError:
        pass

    # 2) 扩展名
    ext = p.suffix.lstrip(".").lower()
    if ext in _EXT_MAP:
        return _EXT_MAP[ext]

    # 3) 尝试按文本探测
    try:
        data = p.read_bytes()[:8192]
        data.decode("utf-8")
        return "text"
    except (UnicodeDecodeError, OSError):
        pass

    return "binary"


def _is_office_zip(head: bytes, p: Path) -> bool:
    """区分 office docx/pptx/xlsx 和普通 zip。"""
    try:
        with zipfile.ZipFile(str(p), "r") as zf:
            names = zf.namelist()
            if any(n.startswith("word/") for n in names):
                return True
            if any(n.startswith("ppt/") for n in names):
                return True
            if any(n.startswith("xl/") for n in names):
                return True
    except (zipfile.BadZipFile, OSError):
        pass
    return False


# ===========================================================================
# simplify_file — 主入口
# ===========================================================================

def simplify_file(
    path: Union[str, Path],
    level: int = 1,
    threshold: int = DEFAULT_THRESHOLD,
) -> bytes:
    """简化文件，level 越高简化越狠。

    Parameters
    ----------
    path : 文件路径
    level : 简化级别（0 = 原样返回，越大越狠）
    threshold : 达到此 level 返回空 bytes

    Returns
    -------
    bytes  简化后的文件内容
    """
    p = Path(path)
    if not p.exists():
        return b""

    level = max(0, level)
    if level == 0:
        return p.read_bytes()

    if level >= threshold:
        return b""

    ftype = detect_type(p)

    dispatch = {
        "text": _simplify_text_file,
        "document": _simplify_document_file,
        "image": _simplify_image_file,
        "audio": _simplify_audio_file,
        "video": _simplify_video_file,
        "archive": _simplify_archive_file,
        "binary": _simplify_binary_file,
    }
    handler = dispatch.get(ftype, _simplify_binary_file)
    return handler(p, level, threshold)


# ===========================================================================
# 字节级兜底
# ===========================================================================

def simplify_bytes(
    content: bytes,
    level: int = 1,
    threshold: int = DEFAULT_THRESHOLD,
    block_size: int = _BLOCK_SIZE,
    seed: Optional[int] = None,
) -> bytes:
    """字节级兜底简化。

    按块切分，计算每块香农熵：
      - 低熵块（熵 < 阈值）直接删除
      - 高熵块按比例随机删除字节
    level 越高删除比例越大，多轮迭代，趋近于空。
    """
    if not content:
        return content
    level = max(0, level)
    if level == 0:
        return content
    if level >= threshold:
        return b""

    rng = random.Random(seed)
    # 每轮删除比例：level / threshold
    delete_ratio = level / threshold
    # 熵阈值：低于此值视为低熵
    entropy_threshold = 1.0 + level * 0.5  # level 越高，更多块被视为低熵

    buf = bytearray(content)
    rounds = max(1, level // 2)  # 简化轮数

    for _ in range(rounds):
        if not buf:
            break
        # 切块
        blocks: List[bytearray] = []
        for i in range(0, len(buf), block_size):
            blocks.append(buf[i:i + block_size])

        new_blocks: List[bytearray] = []
        for blk in blocks:
            ent = _shannon_entropy(blk)
            if ent < entropy_threshold:
                # 低熵块 → 删除
                continue
            # 高熵块 → 按比例随机删除字节
            n_del = int(len(blk) * delete_ratio)
            if n_del <= 0:
                new_blocks.append(blk)
                continue
            # 标记要删除的位置
            del_positions = set(rng.sample(range(len(blk)), min(n_del, len(blk))))
            kept = bytearray(b for i, b in enumerate(blk) if i not in del_positions)
            if kept:
                new_blocks.append(kept)

        buf = bytearray()
        for blk in new_blocks:
            buf.extend(blk)

    return bytes(buf)


def _shannon_entropy(data: bytes) -> float:
    """计算字节序列的香农熵（0~8 bits）"""
    if not data:
        return 0.0
    freq = Counter(data)
    total = len(data)
    ent = 0.0
    for count in freq.values():
        p = count / total
        if p > 0:
            ent -= p * math.log2(p)
    return ent


# ===========================================================================
# text
# ===========================================================================

_PUNCTUATION = set(
    "!,.:;?\"'()-[]{}<>/\\@#$%^&*_+=|~`"
    "！，。：；？、""''（）【】《》〔〕｛｝—…·"
)


def _simplify_text_file(p: Path, level: int, threshold: int) -> bytes:
    """文本文件简化"""
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return p.read_bytes() if p.exists() else b""

    result = _simplify_text_content(text, level, threshold)
    return result.encode("utf-8", errors="replace")


def _simplify_text_content(text: str, level: int, threshold: int) -> str:
    """文本内容简化（纯逻辑，便于测试）"""
    if not text:
        return ""
    if level >= threshold:
        return ""

    result = text
    # level 1: 去空行
    if level >= 1:
        result = _remove_blank_lines(result)
    # level 2: 去标点
    if level >= 2:
        result = _remove_punctuation(result)
    # level 3: 去重复词
    if level >= 3:
        result = _deduplicate_words(result)
    # level 4-5: 删低频词
    if level >= 4:
        thr = max(1, 6 - level)  # level 4→2, level 5→1
        result = _remove_low_freq_words(result, thr)
    # level 6+: 删长句
    if level >= 6:
        max_len = max(10, 120 - level * 10)
        result = _remove_long_sentences(result, max_len)

    return result


def _remove_blank_lines(text: str) -> str:
    return "\n".join(line for line in text.split("\n") if line.strip())


def _remove_punctuation(text: str) -> str:
    return "".join(" " if ch in _PUNCTUATION else ch for ch in text)


def _deduplicate_words(text: str) -> str:
    lines = []
    for line in text.split("\n"):
        seen: Set[str] = set()
        words: List[str] = []
        for w in line.split():
            if w not in seen:
                seen.add(w)
                words.append(w)
        lines.append(" ".join(words))
    return "\n".join(lines)


def _remove_low_freq_words(text: str, threshold: int) -> str:
    words = text.split()
    counter = Counter(words)
    _STOP = frozenset(
        "a an the is am are was were be been being have has had do does did "
        "will would shall should may might must can could of at by for with "
        "to from in out on off up down and or but not no nor".split()
    )
    return " ".join(w for w in words if w in _STOP or counter[w] > threshold)


def _remove_long_sentences(text: str, max_len: int) -> str:
    sent_split = re.compile(r"(?<=[。！？.!?])\s*")
    paragraphs = text.split("\n")
    result = []
    for para in paragraphs:
        sentences = sent_split.split(para)
        kept = [s for s in sentences if len(s) <= max_len]
        result.append(" ".join(kept))
    return "\n".join(result)


# ===========================================================================
# document (docx/pptx/xlsx via zipfile XML extraction)
# ===========================================================================

def _simplify_document_file(p: Path, level: int, threshold: int) -> bytes:
    """文档文件简化"""
    ext = p.suffix.lower()

    if ext == ".pdf":
        return _simplify_pdf(p, level, threshold)

    # docx / pptx / xlsx → ZIP 包，提取 XML 文本节点
    if ext in (".docx", ".pptx", ".xlsx"):
        return _simplify_office_zip(p, level, threshold)

    # 兜底
    return simplify_bytes(p.read_bytes(), level, threshold)


def _simplify_office_zip(p: Path, level: int, threshold: int) -> bytes:
    """对 office open XML 格式做文本简化后重新打包"""
    try:
        buf = io.BytesIO()
        with zipfile.ZipFile(str(p), "r") as zin:
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
                for item in zin.infolist():
                    data = zin.read(item.filename)
                    # 对 XML 文件做文本简化
                    if item.filename.endswith(".xml") or item.filename.endswith(".rels"):
                        try:
                            xml_text = data.decode("utf-8")
                            simplified = _simplify_xml_text(xml_text, level, threshold)
                            data = simplified.encode("utf-8")
                        except UnicodeDecodeError:
                            pass
                    zout.writestr(item, data)
        return buf.getvalue()
    except (zipfile.BadZipFile, OSError):
        return simplify_bytes(p.read_bytes(), level, threshold)


def _simplify_xml_text(xml_text: str, level: int, threshold: int) -> str:
    """提取 XML 文本节点内容并简化"""
    # 提取 <w:t>, <a:t>, <v:t> 等文本节点
    tag_pattern = re.compile(r"<(?:w:|a:|v:)?t[^>]*>([^<]*)</(?:w:|a:|v:)?t>", re.DOTALL)

    def replacer(m: re.Match) -> str:
        original = m.group(1)
        if not original.strip():
            return m.group(0)
        simplified = _simplify_text_content(original, level, threshold)
        tag = m.group(0)
        # 替换标签内容
        prefix_end = tag.index(">") + 1
        suffix_start = tag.rindex("</")
        return tag[:prefix_end] + simplified + tag[suffix_start:]

    return tag_pattern.sub(replacer, xml_text)


def _simplify_pdf(p: Path, level: int, threshold: int) -> bytes:
    """PDF 简化：提取文本流，简化后生成最小 PDF"""
    try:
        data = p.read_bytes()
        # 提取 PDF 文本流中的字符串
        text_chunks: List[str] = []
        # 简单正则提取 ( ) 包围的文本
        for m in re.finditer(rb"\(([^)]*)\)", data):
            try:
                text_chunks.append(m.group(1).decode("latin-1"))
            except UnicodeDecodeError:
                pass

        combined = " ".join(text_chunks)
        simplified = _simplify_text_content(combined, level, threshold)

        # 生成最小有效 PDF
        return _make_minimal_pdf(simplified)
    except OSError:
        return simplify_bytes(p.read_bytes(), level, threshold)


def _make_minimal_pdf(text: str) -> bytes:
    """生成包含指定文本的最小 PDF 1.4"""
    # 对 PDF 特殊字符转义
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    lines = escaped.split("\n")
    # 每行最多 80 字符
    pdf_lines: List[str] = []
    y = 750
    for line in lines:
        if not line.strip():
            y -= 12
            continue
        # PDF 字符串中需要转义
        safe = line[:80]
        pdf_lines.append(f"BT /F1 12 Tf 50 {y} Td ({safe}) Tj ET")
        y -= 14
        if y < 50:
            break

    content_str = "\n".join(pdf_lines)
    content_bytes = content_str.encode("latin-1", errors="replace")

    # 构建 PDF
    objects: List[bytes] = []
    # Obj 1: Catalog
    objects.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    # Obj 2: Pages
    objects.append(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")
    # Obj 3: Page
    objects.append(b"3 0 obj\n<< /Type /Page /Parent 2 0 R "
                   b"/MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n")
    # Obj 4: Content stream
    stream_data = content_bytes
    obj4 = (b"4 0 obj\n<< /Length " + str(len(stream_data)).encode() +
            b" >>\nstream\n" + stream_data + b"\nendstream\nendobj\n")
    objects.append(obj4)
    # Obj 5: Font
    objects.append(b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n")

    pdf = io.BytesIO()
    pdf.write(b"%PDF-1.4\n")
    offsets: List[int] = []
    for obj in objects:
        offsets.append(pdf.tell())
        pdf.write(obj)
    # xref
    xref_start = pdf.tell()
    pdf.write(b"xref\n")
    pdf.write(f"0 {len(objects) + 1}\n".encode())
    pdf.write(b"0000000000 65535 f \n")
    for off in offsets:
        pdf.write(f"{off:010d} 00000 n \n".encode())
    pdf.write(b"trailer\n")
    pdf.write(f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode())
    pdf.write(b"startxref\n")
    pdf.write(f"{xref_start}\n".encode())
    pdf.write(b"%%EOF\n")
    return pdf.getvalue()


# ===========================================================================
# image (Pillow)
# ===========================================================================

def _simplify_image_file(p: Path, level: int, threshold: int) -> bytes:
    """图片文件简化"""
    if not _HAS_PIL:
        return simplify_bytes(p.read_bytes(), level, threshold)

    try:
        img = PILImage.open(str(p))
        original_mode = img.mode
        original_format = img.format or "PNG"

        result = _simplify_image_content(img, level, threshold)

        buf = io.BytesIO()
        fmt = original_format if original_format in ("PNG", "JPEG", "GIF", "BMP", "WEBP") else "PNG"
        save_kwargs: Dict = {}
        if fmt == "JPEG" and result.mode in ("RGBA", "P", "LA"):
            result = result.convert("RGB")
        if fmt == "JPEG":
            save_kwargs["quality"] = max(1, 95 - level * 10)
        result.save(buf, format=fmt, **save_kwargs)
        return buf.getvalue()
    except Exception:
        return simplify_bytes(p.read_bytes(), level, threshold)


def _simplify_image_content(img: "PILImage.Image", level: int, threshold: int) -> "PILImage.Image":
    """图片内容简化"""
    result = img.copy()

    # level 1-2: 降采样
    if level >= 1:
        scale = max(0.05, 1.0 - level * 0.1)
        new_w = max(1, int(result.width * scale))
        new_h = max(1, int(result.height * scale))
        result = result.resize((new_w, new_h), PILImage.LANCZOS)

    # level 3-4: 降色深
    if level >= 3:
        if result.mode not in ("L", "1", "P"):
            # 减少颜色：先转 RGB 再量化
            if result.mode != "RGB":
                result = result.convert("RGB")
            n_colors = max(2, 256 // (level - 1))
            result = result.quantize(colors=n_colors).convert("RGB")

    # level 5-6: 去高频（简单模糊）
    if level >= 5:
        from PIL import ImageFilter
        for _ in range(min(level - 4, 5)):
            result = result.filter(ImageFilter.BLUR)

    # level 7+: 纯色
    if level >= 7:
        # 取主色填充
        if result.mode != "RGB":
            result = result.convert("RGB")
        small = result.resize((1, 1))
        dominant = small.getpixel((0, 0))
        result = PILImage.new("RGB", result.size, dominant)

    return result


# ===========================================================================
# audio
# ===========================================================================

def _simplify_audio_file(p: Path, level: int, threshold: int) -> bytes:
    """音频文件简化"""
    ext = p.suffix.lower()

    # WAV 用标准库
    if ext == ".wav":
        return _simplify_wav(p, level, threshold)

    # mp3/flac/ogg 用 pydub
    if _HAS_PYDUB and ext in (".mp3", ".flac", ".ogg", ".m4a", ".aac"):
        return _simplify_audio_pydub(p, level, threshold)

    # 兜底
    return simplify_bytes(p.read_bytes(), level, threshold)


def _simplify_wav(p: Path, level: int, threshold: int) -> bytes:
    """WAV 简化（标准库 wave）"""
    try:
        with wave.open(str(p), "rb") as wf:
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            n_frames = wf.getnframes()
            frames = wf.readframes(n_frames)

        # level 1-2: 降采样
        if level >= 1:
            skip = min(level, 8)
            frames = frames[::skip * sampwidth * n_channels]
            framerate = max(1, framerate // skip)

        # level 3-4: 降比特率（截断采样位宽）
        if level >= 3 and sampwidth > 1:
            new_width = max(1, sampwidth - (level - 2))
            # 简单截断：每样本只取低字节
            step = sampwidth * n_channels
            new_frames = bytearray()
            for i in range(0, len(frames) - step + 1, step):
                sample_bytes = frames[i:i + sampwidth]
                # 取低 new_width 字节
                truncated = sample_bytes[-new_width:]
                for _ in range(n_channels - 1):
                    pass
                new_frames.extend(truncated)
                # 补齐其他声道
                remaining = frames[i + sampwidth:i + step]
                new_frames.extend(remaining)
            frames = bytes(new_frames)
            sampwidth = new_width

        # level 5+: 静音段替换
        if level >= 5:
            silence_ratio = min(1.0, (level - 4) * 0.2)
            silence_len = int(len(frames) * silence_ratio)
            frames = b"\x00" * silence_len + frames[silence_len:]

        # 写回 WAV
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wo:
            wo.setnchannels(n_channels)
            wo.setsampwidth(sampwidth)
            wo.setframerate(framerate)
            wo.writeframes(frames)
        return buf.getvalue()
    except Exception:
        return simplify_bytes(p.read_bytes(), level, threshold)


def _simplify_audio_pydub(p: Path, level: int, threshold: int) -> bytes:
    """用 pydub 简化音频"""
    try:
        seg = _AudioSegment.from_file(str(p))

        # level 1-2: 降采样
        if level >= 1:
            new_rate = max(2000, seg.frame_rate // (level + 1))
            seg = seg.set_frame_rate(new_rate)

        # level 3-4: 降比特率
        if level >= 3:
            new_width = max(1, seg.sample_width - (level - 2))
            seg = seg.set_sample_width(new_width)

        # level 5+: 静音替换
        if level >= 5:
            silence_ms = int(len(seg) * min(1.0, (level - 4) * 0.2))
            silence = _AudioSegment.silent(duration=silence_ms, frame_rate=seg.frame_rate)
            seg = silence + seg[silence_ms:]

        ext = p.suffix.lower().lstrip(".")
        fmt = ext if ext in ("mp3", "ogg", "flac", "wav") else "mp3"
        return seg.export(format=fmt).read()
    except Exception:
        return simplify_bytes(p.read_bytes(), level, threshold)


# ===========================================================================
# video (opencv)
# ===========================================================================

def _simplify_video_file(p: Path, level: int, threshold: int) -> bytes:
    """视频文件简化"""
    if not _HAS_CV2:
        return simplify_bytes(p.read_bytes(), level, threshold)

    try:
        cap = _cv2.VideoCapture(str(p))
        if not cap.isOpened():
            return simplify_bytes(p.read_bytes(), level, threshold)

        fps = cap.get(_cv2.CAP_PROP_FPS) or 30
        width = int(cap.get(_cv2.CAP_PROP_FRAME_WIDTH)) or 640
        height = int(cap.get(_cv2.CAP_PROP_FRAME_HEIGHT)) or 480
        total = int(cap.get(_cv2.CAP_PROP_FRAME_COUNT)) or 0

        # level 1-2: 降帧率
        frame_skip = max(1, level)
        new_fps = max(1, fps / frame_skip)

        # level 3-4: 降分辨率
        scale = max(0.1, 1.0 - (level - 2) * 0.15) if level >= 3 else 1.0
        new_w = max(16, int(width * scale))
        new_h = max(16, int(height * scale))

        # 读取帧
        frames: List = []
        idx = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            if idx % frame_skip == 0:
                if level >= 3:
                    frame = _cv2.resize(frame, (new_w, new_h))
                # level 5+: 逐步黑化
                if level >= 5:
                    alpha = max(0.0, 1.0 - (level - 4) * 0.2)
                    frame = (frame * alpha).astype("uint8")
                frames.append(frame)
            idx += 1
        cap.release()

        if not frames:
            return simplify_bytes(p.read_bytes(), level, threshold)

        # 写入临时文件
        with tempfile.NamedTemporaryFile(suffix=".avi", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            fourcc = _cv2.VideoWriter_fourcc(*"XVID")
            writer = _cv2.VideoWriter(str(tmp_path), fourcc, new_fps, (new_w, new_h))
            for f in frames:
                writer.write(f)
            writer.release()
            return tmp_path.read_bytes()
        finally:
            tmp_path.unlink(missing_ok=True)
    except Exception:
        return simplify_bytes(p.read_bytes(), level, threshold)


# ===========================================================================
# archive
# ===========================================================================

def _simplify_archive_file(p: Path, level: int, threshold: int) -> bytes:
    """归档文件简化：递归简化内部文件"""
    ext = p.suffix.lower()

    if ext == ".zip" or (ext == ".xlsx" or ext == ".docx" or ext == ".pptx"):
        # 对 xlsx/docx/pptx 不走归档分派（已由 document 处理）
        if ext in (".xlsx", ".docx", ".pptx"):
            return simplify_bytes(p.read_bytes(), level, threshold)
        return _simplify_zip_archive(p, level, threshold)

    if ext in (".tar", ".tar.gz", ".tgz", ".gz", ".bz2"):
        return _simplify_tar_archive(p, level, threshold)

    return simplify_bytes(p.read_bytes(), level, threshold)


def _simplify_zip_archive(p: Path, level: int, threshold: int) -> bytes:
    """ZIP 归档简化"""
    try:
        buf = io.BytesIO()
        with zipfile.ZipFile(str(p), "r") as zin:
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
                for item in zin.infolist():
                    if item.is_dir():
                        zout.writestr(item, b"")
                        continue
                    data = zin.read(item.filename)
                    # 递归简化
                    with tempfile.NamedTemporaryFile(
                        suffix=Path(item.filename).suffix, delete=False
                    ) as tmp:
                        tmp.write(data)
                        tmp_path = Path(tmp.name)
                    try:
                        simplified = simplify_file(tmp_path, level, threshold)
                    finally:
                        tmp_path.unlink(missing_ok=True)
                    zout.writestr(item, simplified)
        return buf.getvalue()
    except (zipfile.BadZipFile, OSError):
        return simplify_bytes(p.read_bytes(), level, threshold)


def _simplify_tar_archive(p: Path, level: int, threshold: int) -> bytes:
    """TAR 归档简化"""
    try:
        # 检测模式
        ext = p.suffix.lower()
        if ext in (".gz", ".tgz"):
            read_mode, write_mode, is_gz = "r:gz", "w:gz", True
        elif ext == ".bz2":
            read_mode, write_mode, is_gz = "r:bz2", "w:bz2", True
        else:
            read_mode, write_mode, is_gz = "r:", "w:", False

        with tarfile.open(str(p), read_mode) as tf:
            # 对 gz/bz2 写入模式，需要用临时文件（tarfile 不支持直接写 BytesIO 的压缩模式）
            with tempfile.NamedTemporaryFile(suffix=".tar.gz" if is_gz else ".tar", delete=False) as tmp:
                tmp_path = Path(tmp.name)

            try:
                with tarfile.open(str(tmp_path), write_mode) as tout:
                    for member in tf.getmembers():
                        if member.isdir():
                            tout.addfile(member)
                            continue
                        f = tf.extractfile(member)
                        if f is None:
                            continue
                        data = f.read()

                        with tempfile.NamedTemporaryFile(
                            suffix=Path(member.name).suffix, delete=False
                        ) as inner_tmp:
                            inner_tmp.write(data)
                            inner_path = Path(inner_tmp.name)
                        try:
                            simplified = simplify_file(inner_path, level, threshold)
                        finally:
                            inner_path.unlink(missing_ok=True)

                        # 更新成员信息
                        member.size = len(simplified)
                        tout.addfile(member, io.BytesIO(simplified))
                return tmp_path.read_bytes()
            finally:
                tmp_path.unlink(missing_ok=True)
    except (tarfile.TarError, OSError):
        return simplify_bytes(p.read_bytes(), level, threshold)


# ===========================================================================
# binary
# ===========================================================================

def _simplify_binary_file(p: Path, level: int, threshold: int) -> bytes:
    """二进制文件简化 → 字节兜底"""
    return simplify_bytes(p.read_bytes(), level, threshold)
