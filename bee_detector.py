"""语义敏感内容检测模块 - 检测文件是否提及蜜蜂"""

import os
import re
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass

from PIL import Image
from PIL.ExifTags import TAGS


@dataclass
class BeeScanResult:
    """蜜蜂扫描结果"""
    hit: bool
    matched_terms: List[str]
    locations: List[Dict]
    scan_type: str


class BeeDetector:
    """蜜蜂检测器"""

    def __init__(self, extra_terms: Optional[List[str]] = None):
        """
        初始化检测器
        
        Args:
            extra_terms: 额外术语列表，追加到默认词表
        """
        # 默认词表
        self._default_terms = {
            # 中文
            "蜜蜂", "蜂蜜", "蜂巢", "蜂王", "工蜂", "雄蜂", "蜂群", "蜂鸣", "嗡嗡", 
            "养蜂", "蜂蜡", "蜂毒", "蜂王浆",
            # 英文
            "bee", "bees", "honey", "hive", "hives", "buzz", "bumblebee", "apis", "mellifera"
        }
        
        # 合并额外术语
        if extra_terms:
            self._default_terms.update(extra_terms)
        
        # 编译正则表达式（支持变形匹配）
        self._compile_patterns()

    def _compile_patterns(self):
        """编译正则表达式模式"""
        self._patterns = {}
        
        for term in self._default_terms:
            # 处理中文（直接匹配）
            if self._is_chinese(term):
                pattern = re.compile(re.escape(term), re.IGNORECASE)
                self._patterns[term] = pattern
            else:
                # 英文：支持复数、大小写变体
                variants = self._get_english_variants(term)
                pattern = re.compile("|".join(variants), re.IGNORECASE)
                self._patterns[term] = pattern

    def _is_chinese(self, text: str) -> bool:
        """判断是否为中文"""
        return any('\u4e00' <= char <= '\u9fff' for char in text)

    def _get_english_variants(self, term: str) -> List[str]:
        """生成英文变体（复数、大小写）"""
        variants = [term]
        
        # 复数形式
        if term.endswith('y'):
            variants.append(term[:-1] + 'ies')
        elif term.endswith('s'):
            variants.append(term + 'es')
        elif term.endswith('h'):
            variants.append(term + 'es')  # 如 "honey" -> "honeyes"
        elif term == 'hive':
            variants.append('hives')
        else:
            variants.append(term + 's')
        
        # 大小写变体
        variants.extend([
            term.upper(),
            term.lower(),
            term.capitalize()
        ])
        
        # 特殊处理：一些常见词
        if term == 'bee':
            variants.append('bees')
        
        return list(set(variants))  # 去重

    def scan_file(self, path: str) -> BeeScanResult:
        """扫描文件（文件名 + 内容 + 元数据）
        
        Args:
            path: 文件路径
            
        Returns:
            BeeScanResult
        """
        results = []
        
        # 1. 扫描文件名
        filename_results = self._scan_filename(path)
        results.extend(filename_results)
        
        # 2. 扫描文件内容
        content_results = self._scan_content(path)
        results.extend(content_results)
        
        # 3. 扫描元数据
        metadata_results = self._scan_metadata(path)
        results.extend(metadata_results)
        
        # 合并结果
        hit = len(results) > 0
        matched_terms = list(set(r['term'] for r in results))
        locations = results
        
        # 确定扫描类型
        scan_types = set(r['source'] for r in results)
        if len(scan_types) > 1:
            scan_type = "combined"
        else:
            scan_type = scan_types.pop() if scan_types else "filename"
        
        return BeeScanResult(
            hit=hit,
            matched_terms=matched_terms,
            locations=locations,
            scan_type=scan_type
        )

    def scan_text(self, text: str) -> BeeScanResult:
        """扫描纯文本
        
        Args:
            text: 文本内容
            
        Returns:
            BeeScanResult
        """
        results = []
        
        for term, pattern in self._patterns.items():
            matches = list(pattern.finditer(text))
            for match in matches:
                results.append({
                    'source': 'content',
                    'term': term,
                    'position': match.start(),
                    'match': match.group()
                })
        
        hit = len(results) > 0
        matched_terms = list(set(r['term'] for r in results))
        
        return BeeScanResult(
            hit=hit,
            matched_terms=matched_terms,
            locations=results,
            scan_type="content"
        )

    def scan_bytes(self, content: bytes) -> BeeScanResult:
        """扫描字节内容（尝试解码为文本）
        
        Args:
            content: 字节内容
            
        Returns:
            BeeScanResult
        """
        # 尝试UTF-8解码
        try:
            text = content.decode('utf-8')
            return self.scan_text(text)
        except UnicodeDecodeError:
            pass
        
        # 尝试GBK解码
        try:
            text = content.decode('gbk')
            return self.scan_text(text)
        except UnicodeDecodeError:
            pass
        
        # 尝试Latin-1解码
        try:
            text = content.decode('latin-1')
            return self.scan_text(text)
        except UnicodeDecodeError:
            pass
        
        # 无法解码，返回未命中
        return BeeScanResult(
            hit=False,
            matched_terms=[],
            locations=[],
            scan_type="content"
        )

    def _scan_filename(self, path: str) -> List[Dict]:
        """扫描文件名"""
        results = []
        filename = Path(path).name.lower()
        
        for term, pattern in self._patterns.items():
            matches = list(pattern.finditer(filename))
            for match in matches:
                results.append({
                    'source': 'filename',
                    'term': term,
                    'position': match.start(),
                    'match': match.group()
                })
        
        return results

    def _scan_content(self, path: str) -> List[Dict]:
        """扫描文件内容"""
        try:
            # 尝试文本解码
            with open(path, 'rb') as f:
                content = f.read()
            
            return self.scan_bytes(content).locations
        except (OSError, IOError):
            return []

    def _scan_metadata(self, path: str) -> List[Dict]:
        """扫描元数据"""
        results = []
        
        # 检查文件扩展名
        ext = Path(path).suffix.lower()
        if ext in ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp'):
            # 图片元数据
            results.extend(self._scan_image_metadata(path))
        elif ext in ('.docx', '.pptx', '.xlsx', '.pdf'):
            # 文档元数据
            results.extend(self._scan_document_metadata(path))
        
        return results

    def _scan_image_metadata(self, path: str) -> List[Dict]:
        """扫描图片EXIF元数据"""
        try:
            with Image.open(path) as img:
                exif_data = img.getexif()
                if not exif_data:
                    return []
                
                results = []
                for tag_id, value in exif_data.items():
                    tag_name = TAGS.get(tag_id, tag_id)
                    
                    # 将值转换为字符串进行搜索
                    if isinstance(value, (int, float)):
                        value_str = str(value)
                    elif isinstance(value, bytes):
                        try:
                            value_str = value.decode('utf-8', errors='replace')
                        except UnicodeDecodeError:
                            continue
                    else:
                        value_str = str(value)
                    
                    # 搜索元数据中的蜜蜂相关内容
                    for term, pattern in self._patterns.items():
                        if pattern.search(value_str):
                            results.append({
                                'source': 'metadata',
                                'term': term,
                                'position': 0,  # 位置信息在元数据中不明确
                                'match': value_str
                            })
                
                return results
        except (OSError, IOError, AttributeError):
            return []

    def _scan_document_metadata(self, path: str) -> List[Dict]:
        """扫描文档核心属性（简化版）"""
        # 这里简化处理，实际应用中需要解析docx/pptx/xlsx的core properties
        # 对于PDF，需要解析XMP或文档信息流
        
        # 基于文件名和扩展名的简单检测
        filename = Path(path).name.lower()
        results = []
        
        for term, pattern in self._patterns.items():
            if pattern.search(filename):
                results.append({
                    'source': 'metadata',
                    'term': term,
                    'position': 0,
                    'match': filename
                })
        
        return results