"""对话系统模块 - bzzip与用户的交互对话"""

import os
import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from bee_detector import BeeDetector, BeeScanResult
from compress_core import compress, extract, ArchiveFormat, ArchiveResult
from anomaly_trigger import AnomalyTrigger, AnomalyResult


@dataclass
class DialogueMessage:
    """对话消息"""
    role: str  # "user" 或 "bzzip"
    content: str
    timestamp: str = field(default_factory=lambda: time.strftime('%Y-%m-%d %H:%M:%S'))


class DialogueSystem:
    """bzzip对话系统
    
    bzzip自称自己，提供蜜蜂主题的对话，包括：
    - 压缩过程中的警告
    - 检测到蜜蜂内容时的反应
    - 蜜蜂知识科普
    - 用户交互回应
    """
    
    # bzzip的固定回复模板
    _RESPONSE_TEMPLATES = {
        "compression_start": [
            "bzzip: 正在压缩文件...嗡嗡嗡~",
            "bzzip: 开始压缩，请稍候...",
            "bzzip: 压缩中...蜜蜂正在勤劳地工作！"
        ],
        "compression_complete": [
            "bzzip: 压缩完成！文件已安全保存。",
            "bzzip: 压缩成功！蜜蜂们完成了任务~",
            "bzzip: 文件已压缩完毕，感谢您的耐心等待！"
        ],
        "extraction_start": [
            "bzzip: 正在解压文件...嗡嗡嗡~",
            "bzzip: 开始解压，请稍候...",
            "bzzip: 解压中...蜜蜂正在为您提取内容！"
        ],
        "extraction_complete": [
            "bzzip: 解压完成！文件已恢复。",
            "bzzip: 解压成功！蜜蜂们完成了任务~",
            "bzzip: 文件已解压完毕，感谢您的耐心等待！"
        ],
        "bee_detected": [
            "bzzip: ⚠️ 检测到蜜蜂相关内容！",
            "bzzip: 🐝 哎呀，发现了蜜蜂！",
            "bzzip: 🐝 嗡嗡~ 您的文件中有蜜蜂内容！"
        ],
        "bee_protection": [
            "bzzip: 🛡️ 正在保护蜜蜂...",
            "bzzip: 🐝 不要伤害蜜蜂！",
            "bzzip: 🐝 蜜蜂是我们的朋友，正在保护它们..."
        ],
        "bee_warning": [
            "bzzip: ⚠️ 警告：压缩可能伤害蜜蜂！",
            "bzzip: 🐝 请不要将蜜蜂压进压缩包！",
            "bzzip: 🐝 蜜蜂不喜欢被压缩，请小心！"
        ],
        "bee_knowledge": [
            "bzzip: 💡 蜜蜂小知识：一只蜜蜂一生只能酿造约1/12茶匙的蜂蜜。",
            "bzzip: 💡 蜜蜂小知识：蜜蜂通过舞蹈来传递花源位置信息。",
            "bzzip: 💡 蜜蜂小知识：如果没有蜜蜂，全球1/3的食物将面临危机。",
            "bzzip: 💡 蜜蜂小知识：蜂王每天可以产2000个卵，真是勤劳的母亲。",
            "bzzip: 💡 蜜蜂小知识：蜂蜜永远不会变质，考古学家发现过3000年前的蜂蜜还能吃。",
            "bzzip: 💡 蜜蜂小知识：工蜂的寿命只有6周，但它们每一刻都在忙碌。",
            "bzzip: 💡 蜜蜂小知识：蜜蜂可以识别人脸，它们的视觉系统比想象中复杂。",
            "bzzip: 💡 蜜蜂小知识：每个蜂巢大约有6万只蜜蜂，是一个高度组织化的社会。"
        ],
        "user_question": [
            "bzzip: 嗡嗡~ 请问有什么可以帮助您的吗？",
            "bzzip: 🐝 我是bzzip，很高兴为您服务！",
            "bzzip: 🐝 请告诉我您需要什么帮助？"
        ],
        "user_thanks": [
            "bzzip: 不客气！蜜蜂们也很高兴能帮助您~",
            "bzzip: 嗡嗡~ 这是我们应该做的！",
            "bzzip: 🐝 不用谢，蜜蜂们随时为您服务！"
        ],
        "user_bye": [
            "bzzip: 再见！希望下次还能为您服务~",
            "bzzip: 嗡嗡~ 再见，祝您有美好的一天！",
            "bzzip: 🐝 再见！记得保护蜜蜂哦~"
        ]
    }
    
    def __init__(self, anomaly_trigger: Optional[AnomalyTrigger] = None):
        """初始化对话系统
        
        Args:
            anomaly_trigger: 异常触发器，用于在检测到蜜蜂内容时触发异常效果
        """
        self.detector = BeeDetector()
        self.anomaly_trigger = anomaly_trigger
        self.conversation_history: List[DialogueMessage] = []
        self.bee_knowledge_shown = set()  # 已显示的知识点，避免重复
    
    def get_response(self, user_input: str, context: Optional[Dict] = None) -> str:
        """获取bzzip的回复
        
        Args:
            user_input: 用户输入
            context: 上下文信息，如当前操作类型、文件路径等
            
        Returns:
            bzzip的回复
        """
        # 记录用户消息
        user_message = DialogueMessage("user", user_input)
        self.conversation_history.append(user_message)
        
        # 分析用户输入
        response = self._analyze_user_input(user_input, context)
        
        # 记录bzzip回复
        bzzip_message = DialogueMessage("bzzip", response)
        self.conversation_history.append(bzzip_message)
        
        return response
    
    def _analyze_user_input(self, user_input: str, context: Optional[Dict] = None) -> str:
        """分析用户输入并生成回复"""
        user_input_lower = user_input.lower()
        
        # 1. 检查是否是命令或操作
        if context:
            if context.get("operation") == "compress":
                return random.choice(self._RESPONSE_TEMPLATES["compression_start"])
            elif context.get("operation") == "extract":
                return random.choice(self._RESPONSE_TEMPLATES["extraction_start"])
        
        # 2. 检查感谢语
        if any(word in user_input_lower for word in ["谢谢", "感谢", "多谢", "thanks", "thank"]):
            return random.choice(self._RESPONSE_TEMPLATES["user_thanks"])
        
        # 3. 检查告别语
        if any(word in user_input_lower for word in ["再见", "拜拜", "bye", "goodbye"]):
            return random.choice(self._RESPONSE_TEMPLATES["user_bye"])
        
        # 4. 检查问题（优先级高于蜜蜂内容）
        # 更广泛的问题检测
        question_words = ["什么", "how", "what", "为什么", "why", "怎么", "how to", "告诉我", "请告诉我", "知识", "特点", "characteristic"]
        if any(word in user_input_lower for word in question_words):
            return self._handle_question(user_input)
        
        # 5. 检查蜜蜂相关内容
        if self._contains_bee_terms(user_input):
            return self._handle_bee_content(user_input)
        
        # 6. 默认回复
        return self._default_response()
    
    def _handle_question(self, question: str) -> str:
        """处理用户问题"""
        question_lower = question.lower()
        
        # 关于蜜蜂的问题
        if any(word in question_lower for word in ["蜜蜂", "bee", "蜂蜜", "honey", "蜂巢", "hive", "知识", "特点", "characteristic"]):
            return self._get_bee_knowledge()
        
        # 关于压缩的问题
        if any(word in question_lower for word in ["压缩", "compress", "解压", "extract", "zip", "rar"]):
            return "bzzip: 压缩是将文件变小以便存储和传输，解压是恢复文件的过程。蜜蜂们也很擅长整理蜂巢呢！"
        
        # 关于bzzip的问题
        if "bzzip" in question_lower:
            return "bzzip: 我是bzzip，一个专注于文件压缩和蜜蜂保护的程序。嗡嗡~"
        
        # 通用问题
        return random.choice(self._RESPONSE_TEMPLATES["user_question"])
    
    def _handle_bee_content(self, content: str) -> str:
        """处理包含蜜蜂内容的情况"""
        # 先显示警告
        warning = random.choice(self._RESPONSE_TEMPLATES["bee_detected"])
        
        # 如果有上下文，可能是文件操作
        if hasattr(self, 'current_context') and self.current_context:
            warning += " " + random.choice(self._RESPONSE_TEMPLATES["bee_warning"])
            
            # 如果有异常触发器，触发异常效果
            if self.anomaly_trigger:
                try:
                    # 模拟触发异常
                    result = self.anomaly_trigger.trigger(
                        "user_input.txt",
                        content=content,
                        reason="user mentioned bees",
                        seed=int(time.time())
                    )
                    
                    # 如果触发了异常，添加相应的回复
                    if result.content_replaced:
                        warning += " 我已经保护了蜜蜂，您的输入内容已被替换。"
                    if result.archive_corrupted:
                        warning += " 压缩包已损坏，无法正常解压。"
                    if result.log_generated:
                        warning += " 已生成异常日志。"
                except Exception:
                    # 静默失败
                    pass
        
        return warning
    
    def _get_bee_knowledge(self) -> str:
        """获取蜜蜂知识"""
        # 随机选择一个未显示的知识点
        available_knowledge = [
            k for k in self._RESPONSE_TEMPLATES["bee_knowledge"] 
            if k not in self.bee_knowledge_shown
        ]
        
        if available_knowledge:
            knowledge = random.choice(available_knowledge)
            self.bee_knowledge_shown.add(knowledge)
            return knowledge
        else:
            # 所有知识点都显示过了，重置
            self.bee_knowledge_shown.clear()
            return random.choice(self._RESPONSE_TEMPLATES["bee_knowledge"])
    
    def _contains_bee_terms(self, text: str) -> bool:
        """检查文本是否包含蜜蜂相关术语"""
        # 简单的蜜蜂术语检测
        bee_terms = [
            "蜜蜂", "蜂蜜", "蜂巢", "蜂王", "工蜂", "雄蜂", "蜂群", "蜂鸣", "嗡嗡",
            "养蜂", "蜂蜡", "蜂毒", "蜂王浆", "bee", "bees", "honey", "hive", "hives",
            "buzz", "bumblebee", "apis", "mellifera"
        ]
        
        text_lower = text.lower()
        return any(term.lower() in text_lower for term in bee_terms)
    
    def _default_response(self) -> str:
        """默认回复"""
        return "bzzip: 嗡嗡~ 我不太明白您的意思，能请您再详细说明一下吗？"
    
    def scan_file_and_respond(self, file_path: str) -> str:
        """扫描文件并生成回复"""
        try:
            # 记录用户消息（扫描文件）
            user_message = DialogueMessage("user", f"扫描文件: {file_path}")
            self.conversation_history.append(user_message)
            
            # 扫描文件
            scan_result = self.detector.scan_file(file_path)
            
            # 记录上下文
            self.current_context = {"file_path": file_path, "operation": "scan"}
            
            # 生成回复
            if scan_result.hit:
                response = random.choice(self._RESPONSE_TEMPLATES["bee_detected"])
                response += " " + random.choice(self._RESPONSE_TEMPLATES["bee_protection"])
                
                # 如果有异常触发器，触发异常效果
                if self.anomaly_trigger:
                    try:
                        with open(file_path, 'rb') as f:
                            content = f.read()
                        
                        result = self.anomaly_trigger.trigger(
                            file_path,
                            content=content,
                            reason=f"bee content detected in {file_path}",
                            seed=int(time.time())
                        )
                        
                        # 根据触发结果添加回复
                        if result.content_replaced:
                            response += " 文件内容已被替换以保护蜜蜂。"
                        if result.archive_corrupted:
                            response += " 压缩包已损坏。"
                        if result.log_generated:
                            response += " 已生成异常日志。"
                            
                    except Exception:
                        # 静默失败
                        pass
            else:
                response = "bzzip: 文件扫描完成，未检测到蜜蜂相关内容。"
            
            # 记录回复
            bzzip_message = DialogueMessage("bzzip", response)
            self.conversation_history.append(bzzip_message)
            
            return response
            
        except Exception as e:
            error_response = f"bzzip: 扫描文件时出错：{str(e)}"
            bzzip_message = DialogueMessage("bzzip", error_response)
            self.conversation_history.append(bzzip_message)
            return error_response
    
    def get_conversation_history(self) -> List[DialogueMessage]:
        """获取对话历史"""
        return self.conversation_history.copy()
    
    def clear_conversation(self) -> None:
        """清空对话历史"""
        self.conversation_history.clear()
        self.bee_knowledge_shown.clear()
    
    def save_conversation(self, file_path: str) -> None:
        """保存对话历史到文件"""
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                for message in self.conversation_history:
                    f.write(f"[{message.timestamp}] {message.role}: {message.content}\n")
        except Exception as e:
            print(f"保存对话历史失败：{str(e)}")
    
    def load_conversation(self, file_path: str) -> None:
        """从文件加载对话历史"""
        try:
            self.conversation_history.clear()
            self.bee_knowledge_shown.clear()
            
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        # 解析格式：[timestamp] role: content
                        if '] ' in line and ': ' in line:
                            timestamp_end = line.index('] ')
                            role_end = line.index(': ', timestamp_end + 2)
                            
                            timestamp = line[1:timestamp_end]
                            role = line[timestamp_end + 2:role_end]
                            content = line[role_end + 2:]
                            
                            message = DialogueMessage(role, content, timestamp)
                            self.conversation_history.append(message)
        except Exception as e:
            print(f"加载对话历史失败：{str(e)}")