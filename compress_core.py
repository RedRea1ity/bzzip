"""压缩工具核心模块 — 支持 zip / tar / gz

仅依赖 Python 标准库 (zipfile, tarfile, gzip, shutil, pathlib)。
不调用任何外部二进制。
"""

from __future__ import annotations

import gzip
import os
import shutil
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Optional, Sequence, Union


# ---------------------------------------------------------------------------
# 结果对象
# ---------------------------------------------------------------------------

class ArchiveFormat(Enum):
    ZIP = "zip"
    TAR = "tar"
    GZ = "gz"          # 单文件 gzip；多文件时自动退化为 tar.gz
    TAR_GZ = "tar.gz"   # 显式 tar.gz


@dataclass
class ArchiveResult:
    """统一返回结果"""
    success: bool
    error: Optional[str] = None
    file_count: int = 0
    original_size: int = 0        # 原始总字节数
    compressed_size: int = 0      # 压缩后字节数
    elapsed_ms: float = 0.0       # 耗时（毫秒）
    files: List[str] = field(default_factory=list)   # 归档内文件路径列表

    @property
    def compression_ratio(self) -> float:
        """压缩率 0.0 ~ 1.0（越高压缩越多）；原始为 0 时返回 0.0"""
        if self.original_size <= 0:
            return 0.0
        return 1.0 - self.compressed_size / self.original_size

    def __repr__(self) -> str:
        st = "OK" if self.success else f"FAIL({self.error})"
        return (
            f"ArchiveResult({st}, files={self.file_count}, "
            f"orig={self.original_size}, comp={self.compressed_size}, "
            f"ratio={self.compression_ratio:.2%})"
        )


# ---------------------------------------------------------------------------
# 内部工具函数
# ---------------------------------------------------------------------------

def _resolve_paths(files: Sequence[Union[str, Path]]) -> List[Path]:
    """把输入统一成绝对 Path 列表，跳过不存在的路径"""
    result: List[Path] = []
    for f in files:
        p = Path(f).resolve()
        if p.exists():
            result.append(p)
    return result


def _total_size(paths: Sequence[Path]) -> int:
    """递归计算总字节数"""
    total = 0
    for p in paths:
        if p.is_file():
            total += p.stat().st_size
        elif p.is_dir():
            for child in p.rglob("*"):
                if child.is_file():
                    total += child.stat().st_size
    return total


def _collect_files(paths: Sequence[Path]) -> List[Path]:
    """展开目录，收集所有文件（含子目录中的文件）"""
    files: List[Path] = []
    for p in paths:
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            for child in sorted(p.rglob("*")):
                if child.is_file():
                    files.append(child)
    return files


def _detect_format(archive_path: Union[str, Path]) -> ArchiveFormat:
    """根据文件扩展名推断格式"""
    name = str(archive_path).lower()
    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        return ArchiveFormat.TAR_GZ
    if name.endswith(".tar") or name.endswith(".tar.bz2") or name.endswith(".tbz2"):
        return ArchiveFormat.TAR
    if name.endswith(".gz"):
        return ArchiveFormat.GZ
    if name.endswith(".zip"):
        return ArchiveFormat.ZIP
    # 默认 zip
    return ArchiveFormat.ZIP


def _norm_arcname(file_path: Path, base: Path) -> str:
    """计算归档内的相对路径，始终使用 / 作为分隔符"""
    try:
        rel = file_path.relative_to(base)
    except ValueError:
        rel = Path(file_path.name)
    return rel.as_posix()


# ---------------------------------------------------------------------------
# compress
# ---------------------------------------------------------------------------

def compress(
    files: Sequence[Union[str, Path]],
    output_path: Union[str, Path],
    fmt: Union[str, ArchiveFormat] = ArchiveFormat.ZIP,
) -> ArchiveResult:
    """将 *files*（文件或目录）压缩到 *output_path*。

    Parameters
    ----------
    files : 文件/目录路径列表
    output_path : 输出归档路径
    fmt : 归档格式（"zip" / "tar" / "gz" / ArchiveFormat 枚举）

    Returns
    -------
    ArchiveResult
    """
    import time
    t0 = time.perf_counter()

    if isinstance(fmt, str):
        fmt = ArchiveFormat(fmt.lower())

    paths = _resolve_paths(files)
    if not paths:
        return ArchiveResult(success=False, error="没有可压缩的文件")

    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    original_size = _total_size(paths)

    try:
        if fmt == ArchiveFormat.ZIP:
            _compress_zip(paths, out)
        elif fmt == ArchiveFormat.TAR:
            _compress_tar(paths, out, mode="w")
        elif fmt in (ArchiveFormat.GZ, ArchiveFormat.TAR_GZ):
            # 多文件 → tar.gz；单文件 → 纯 gzip
            all_files = _collect_files(paths)
            if len(all_files) == 1 and fmt == ArchiveFormat.GZ:
                _compress_gz_single(all_files[0], out)
            else:
                _compress_tar(paths, out, mode="w:gz")
        else:
            return ArchiveResult(success=False, error=f"不支持的格式: {fmt}")
    except Exception as exc:
        # 清理失败的输出文件
        if out.exists():
            try:
                out.unlink()
            except OSError:
                pass
        return ArchiveResult(success=False, error=str(exc))

    compressed_size = out.stat().st_size if out.exists() else 0
    elapsed = (time.perf_counter() - t0) * 1000

    # 列出归档内容以便返回文件列表
    info = list_contents(out)
    file_names = info.files if info.success else [str(p) for p in _collect_files(paths)]

    return ArchiveResult(
        success=True,
        file_count=len(file_names),
        original_size=original_size,
        compressed_size=compressed_size,
        elapsed_ms=elapsed,
        files=file_names,
    )


def _compress_zip(paths: Sequence[Path], out: Path) -> None:
    with zipfile.ZipFile(str(out), "w", zipfile.ZIP_DEFLATED) as zf:
        for p in paths:
            if p.is_file():
                arcname = p.name
                zf.write(str(p), arcname)
            elif p.is_dir():
                base = p.parent
                for child in sorted(p.rglob("*")):
                    if child.is_file():
                        arcname = _norm_arcname(child, base)
                        zf.write(str(child), arcname)


def _compress_tar(paths: Sequence[Path], out: Path, mode: str = "w") -> None:
    with tarfile.open(str(out), mode) as tf:
        for p in paths:
            if p.is_file():
                tf.add(str(p), arcname=p.name)
            elif p.is_dir():
                base = p.parent
                for child in sorted(p.rglob("*")):
                    if child.is_file():
                        arcname = _norm_arcname(child, base)
                        tf.add(str(child), arcname=arcname)


def _compress_gz_single(file_path: Path, out: Path) -> None:
    with open(str(file_path), "rb") as f_in:
        with gzip.open(str(out), "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)


# ---------------------------------------------------------------------------
# extract
# ---------------------------------------------------------------------------

def extract(
    archive_path: Union[str, Path],
    output_dir: Union[str, Path],
) -> ArchiveResult:
    """将 *archive_path* 解压到 *output_dir*。"""
    import time
    t0 = time.perf_counter()

    src = Path(archive_path).resolve()
    if not src.exists():
        return ArchiveResult(success=False, error=f"归档不存在: {src}")

    dest = Path(output_dir).resolve()
    dest.mkdir(parents=True, exist_ok=True)

    fmt = _detect_format(src)

    try:
        if fmt == ArchiveFormat.ZIP:
            extracted = _extract_zip(src, dest)
        elif fmt in (ArchiveFormat.TAR, ArchiveFormat.TAR_GZ):
            extracted = _extract_tar(src, dest)
        elif fmt == ArchiveFormat.GZ:
            extracted = _extract_gz(src, dest)
        else:
            return ArchiveResult(success=False, error=f"不支持的格式: {fmt}")
    except Exception as exc:
        return ArchiveResult(success=False, error=str(exc))

    elapsed = (time.perf_counter() - t0) * 1000

    # 计算原始大小
    original_size = sum(
        f.stat().st_size for f in dest.rglob("*") if f.is_file()
    )
    compressed_size = src.stat().st_size

    return ArchiveResult(
        success=True,
        file_count=len(extracted),
        original_size=original_size,
        compressed_size=compressed_size,
        elapsed_ms=elapsed,
        files=[str(f.relative_to(dest)) for f in extracted],
    )


def _extract_zip(src: Path, dest: Path) -> List[Path]:
    with zipfile.ZipFile(str(src), "r") as zf:
        zf.extractall(str(dest))
        return [dest / name for name in zf.namelist() if not name.endswith("/")]


def _extract_tar(src: Path, dest: Path) -> List[Path]:
    extracted: List[Path] = []
    with tarfile.open(str(src)) as tf:
        # 安全过滤：去除绝对路径和 ..
        members = []
        for m in tf.getmembers():
            if m.name.startswith("/") or ".." in m.name:
                continue
            members.append(m)
        tf.extractall(str(dest), members=members, filter="data")
        extracted = [dest / m.name for m in members if not m.isdir()]
    return extracted


def _extract_gz(src: Path, dest: Path) -> List[Path]:
    # 单文件 gzip → 去掉 .gz 后缀
    out_name = src.stem if src.suffix == ".gz" else src.name + ".out"
    out_path = dest / out_name
    with gzip.open(str(src), "rb") as f_in:
        with open(str(out_path), "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
    return [out_path]


# ---------------------------------------------------------------------------
# list_contents
# ---------------------------------------------------------------------------

def list_contents(archive_path: Union[str, Path]) -> ArchiveResult:
    """列出归档内容，不执行解压。"""
    src = Path(archive_path).resolve()
    if not src.exists():
        return ArchiveResult(success=False, error=f"归档不存在: {src}")

    fmt = _detect_format(src)

    try:
        if fmt == ArchiveFormat.ZIP:
            return _list_zip(src)
        elif fmt in (ArchiveFormat.TAR, ArchiveFormat.TAR_GZ):
            return _list_tar(src)
        elif fmt == ArchiveFormat.GZ:
            return _list_gz(src)
        else:
            return ArchiveResult(success=False, error=f"不支持的格式: {fmt}")
    except Exception as exc:
        return ArchiveResult(success=False, error=str(exc))


def _list_zip(src: Path) -> ArchiveResult:
    with zipfile.ZipFile(str(src), "r") as zf:
        names = [i.filename for i in zf.infolist() if not i.is_dir()]
        orig = sum(i.file_size for i in zf.infolist())
        comp = src.stat().st_size
        return ArchiveResult(
            success=True,
            file_count=len(names),
            original_size=orig,
            compressed_size=comp,
            files=names,
        )


def _list_tar(src: Path) -> ArchiveResult:
    with tarfile.open(str(src)) as tf:
        members = tf.getmembers()
        names = [m.name for m in members if not m.isdir()]
        orig = sum(m.size for m in members)
        comp = src.stat().st_size
        return ArchiveResult(
            success=True,
            file_count=len(names),
            original_size=orig,
            compressed_size=comp,
            files=names,
        )


def _list_gz(src: Path) -> ArchiveResult:
    comp = src.stat().st_size
    return ArchiveResult(
        success=True,
        file_count=1,
        original_size=0,  # gzip 不保存原始大小
        compressed_size=comp,
        files=[src.stem],
    )


# ---------------------------------------------------------------------------
# test_archive
# ---------------------------------------------------------------------------

def test_archive(archive_path: Union[str, Path]) -> ArchiveResult:
    """测试归档完整性。"""
    src = Path(archive_path).resolve()
    if not src.exists():
        return ArchiveResult(success=False, error=f"归档不存在: {src}")

    fmt = _detect_format(src)

    try:
        if fmt == ArchiveFormat.ZIP:
            return _test_zip(src)
        elif fmt in (ArchiveFormat.TAR, ArchiveFormat.TAR_GZ):
            return _test_tar(src)
        elif fmt == ArchiveFormat.GZ:
            return _test_gz(src)
        else:
            return ArchiveResult(success=False, error=f"不支持的格式: {fmt}")
    except Exception as exc:
        return ArchiveResult(success=False, error=str(exc))


def _test_zip(src: Path) -> ArchiveResult:
    with zipfile.ZipFile(str(src), "r") as zf:
        bad = zf.testzip()
        if bad is not None:
            return ArchiveResult(
                success=False,
                error=f"损坏的文件: {bad}",
                file_count=len(zf.namelist()),
            )
        info = _list_zip(src)
        return ArchiveResult(
            success=True,
            file_count=info.file_count,
            original_size=info.original_size,
            compressed_size=info.compressed_size,
            files=info.files,
        )


def _test_tar(src: Path) -> ArchiveResult:
    # tarfile 没有内置完整性校验，逐成员读取来验证
    with tarfile.open(str(src)) as tf:
        members = tf.getmembers()
        for m in members:
            if not m.isdir():
                f = tf.extractfile(m)
                if f is not None:
                    while f.read(8192):
                        pass
        info = _list_tar(src)
        return ArchiveResult(
            success=True,
            file_count=info.file_count,
            original_size=info.original_size,
            compressed_size=info.compressed_size,
            files=info.files,
        )


def _test_gz(src: Path) -> ArchiveResult:
    with gzip.open(str(src), "rb") as f:
        while f.read(8192):
            pass
    comp = src.stat().st_size
    return ArchiveResult(
        success=True,
        file_count=1,
        compressed_size=comp,
    )
