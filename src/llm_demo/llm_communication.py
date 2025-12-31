#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
简化的Anthropic LLM通信模块

基于 MRC/backend/app/services/simple_llm.py 的直接提取
保持与原MRC系统完全一致的实现

适配MultiRoleChat系统的简化LLM服务
"""

try:
    import anthropic  # type: ignore
except Exception:  # noqa: BLE001
    class _AnthropicShim:  # noqa: D401
        """Shim for optional anthropic dependency."""

        class APIError(Exception):
            pass

        Anthropic = None

    anthropic = _AnthropicShim()  # type: ignore
import subprocess
from typing import Optional, List, Dict, Any
import logging
import time
from dataclasses import dataclass

# 尝试导入MRC的request_tracker（如果可用）
try:
    from .utils.request_tracker import RequestTracker, log_llm_info, log_llm_error, log_llm_warning
except ImportError:
    # 如果无法导入，创建基本的替代实现
    class RequestTracker:
        @staticmethod
        def get_context():
            class Context:
                def __init__(self):
                    self.request_id = "UNKNOWN"
            return Context()

    def log_llm_info(*args, **kwargs):
        pass

    def log_llm_error(*args, **kwargs):
        pass

    def log_llm_warning(*args, **kwargs):
        pass

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    """LLM响应数据结构（与MRC原实现完全一致）"""
    content: str
    model: str
    usage: Dict[str, int]
    response_time: float
    provider: str = "anthropic"


class SimpleLLMService:
    """
    简化的LLM服务类

    直接从 MRC/backend/app/services/simple_llm.py 提取
    保持与原系统完全一致的实现
    """

    def __init__(self, api_key: Optional[str] = None):
        """
        初始化简化LLM服务（与MRC原实现一致）

        Args:
            api_key (Optional[str]): API密钥，None表示自动寻找
        """
        self.api_key = api_key
        self.default_model = "claude-3-5-sonnet-20241022"
        self.default_max_tokens = 4096 * 3

        runtime_provider = "llm_demo"
        try:
            from core.runtime_config import get_runtime_config  # type: ignore

            llm_cfg = get_runtime_config().llm
            runtime_provider = llm_cfg.provider
        except Exception:
            pass

        # 创建客户端（与MRC原实现完全一致 - 使用None让SDK自动寻找认证）
        self.client = None
        if runtime_provider != "claude_code":
            if getattr(anthropic, "Anthropic", None) is None:
                raise RuntimeError("Missing dependency: anthropic (pip install anthropic) is required when llm.provider=llm_demo.")
            self.client = anthropic.Anthropic(api_key=api_key)
        logger.info(f"简化LLM服务已初始化，模型: {self.default_model}")

    def _claude_code_call(self, prompt: str, *, timeout_s: int = 300) -> str:
        # Optional support via JSON runtime config (not environment variables).
        try:
            import config  # type: ignore
            from core.runtime_config import get_runtime_config  # type: ignore

            llm_cfg = get_runtime_config().llm
            if llm_cfg.provider != "claude_code":
                raise RuntimeError("claude_code provider not enabled")
            bin_path = llm_cfg.claude_code_bin
            timeout_s = int(llm_cfg.timeout_s)
        except Exception:
            raise RuntimeError("claude_code provider not enabled (configure runtime_config.json)")

        completed = subprocess.run(
            [bin_path],
            input=prompt,
            text=True,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
        if completed.returncode != 0:
            stderr = (completed.stderr or "").strip()
            raise RuntimeError(f"claude_code returned {completed.returncode}: {stderr or 'no stderr'}")
        return (completed.stdout or "").strip()

    def llm_call(self, prompt: str) -> str:
        """
        最小的LLM调用函数（与MRC原实现完全一致）

        Args:
            prompt (str): 输入提示词

        Returns:
            str: LLM响应内容
        """
        try:
            # If configured, prefer claude_code.
            try:
                return self._claude_code_call(prompt)
            except Exception:
                pass

            if self.client is None:
                raise RuntimeError("LLM client not initialized (check runtime_config.json)")
            response = self.client.messages.create(
                model=self.default_model,
                max_tokens=self.default_max_tokens,
                messages=[{"role": "user", "content": prompt}]
            )
            return response.content[0].text

        except Exception as e:
            logger.error(f"LLM调用失败: {str(e)}")
            return f"调用失败: {str(e)}"

    def simple_llm(
        self,
        prompt: str,
        model: str = None,
        max_tokens: int = None
    ) -> str:
        """
        简单的LLM调用函数，基于MRC的原实现风格

        Args:
            prompt (str): 输入提示词
            model (str): 模型名称
            max_tokens (int): 最大输出token数

        Returns:
            str: LLM响应内容
        """
        try:
            model = model or self.default_model
            max_tokens = max_tokens or self.default_max_tokens

            try:
                return self._claude_code_call(prompt)
            except Exception:
                pass

            messages = [{"role": "user", "content": prompt}]

            if self.client is None:
                raise RuntimeError("LLM client not initialized (check runtime_config.json)")
            response = self.client.messages.create(
                model=model,
                max_tokens=max_tokens,
                messages=messages
            )

            if response.content:
                return response.content[0].text
            else:
                return "未收到有效响应"

        except anthropic.APIError as e:
            logger.error(f"API错误: {str(e)}")
            return f"API错误: {str(e)}"
        except Exception as e:
            logger.error(f"调用失败: {str(e)}")
            return f"调用失败: {str(e)}"

    def llm_call_with_history(
        self,
        prompt: str,
        history: Optional[List[Dict[str, str]]] = None,
        model: str = None,
        max_tokens: int = None
    ) -> str:
        """
        带历史记录的LLM调用函数（与MRC原实现一致）

        Args:
            prompt (str): 当前用户输入
            history (Optional[List[Dict[str, str]]]): 对话历史
            model (str): 模型名称
            max_tokens (int): 最大输出token数

        Returns:
            str: LLM响应内容
        """
        try:
            model = model or self.default_model
            max_tokens = max_tokens or self.default_max_tokens

            messages = []

            # 添加历史消息
            if history:
                messages.extend(history)

            # 添加当前消息
            messages.append({"role": "user", "content": prompt})

            if self.client is None:
                raise RuntimeError("LLM client not initialized (check runtime_config.json)")
            response = self.client.messages.create(
                model=model,
                max_tokens=max_tokens,
                messages=messages
            )

            if response.content:
                return response.content[0].text
            else:
                return "未收到有效响应"

        except anthropic.APIError as e:
            logger.error(f"API错误: {str(e)}")
            return f"API错误: {str(e)}"
        except Exception as e:
            logger.error(f"调用失败: {str(e)}")
            return f"调用失败: {str(e)}"

    def generate_response(
        self,
        messages: List[Dict[str, Any]],
        model: str = None,
        max_tokens: int = None,
        request_id: str = None,
        **kwargs
    ) -> LLMResponse:
        """
        生成LLM响应（与MRC原实现完全一致）

        Args:
            messages (List[Dict[str, Any]]): 消息列表
            model (str): 模型名称
            max_tokens (int): 最大输出token数
            request_id (str): 请求ID用于日志追踪
            **kwargs: 其他参数

        Returns:
            LLMResponse: LLM响应对象
        """
        # 如果没有传入request_id，尝试从当前上下文获取
        if request_id is None:
            context = RequestTracker.get_context()
            request_id = context.request_id if context else "UNKNOWN"

        start_time = time.time()

        log_llm_info(
            "CORE",
            "开始生成Anthropic LLM响应",
            request_id,
            model=model or self.default_model,
            max_tokens=max_tokens or self.default_max_tokens,
            message_count=len(messages) if messages else 0,
            additional_params={k: v for k, v in kwargs.items() if k not in ['model', 'max_tokens', 'messages']}
        )

        try:
            model = model or self.default_model
            max_tokens = max_tokens or self.default_max_tokens

            # 转换消息格式（与MRC原实现一致）
            anthropic_messages = []

            log_llm_info(
                "CORE",
                "开始转换消息格式为Anthropic API格式",
                request_id,
                input_format="字典列表",
                input_count=len(messages) if messages else 0,
                target_format="Anthropic API消息格式"
            )

            for i, msg in enumerate(messages):
                # 处理LLMMessage对象或字典（与MRC原实现一致）
                if hasattr(msg, 'role'):
                    role = msg.role
                    content = msg.content
                elif isinstance(msg, dict):
                    role = msg.get('role', 'user')
                    content = msg.get('content', str(msg))
                else:
                    # 对于未知类型的对象，尝试安全的属性访问
                    role = getattr(msg, 'role', 'user')
                    content = getattr(msg, 'content', str(msg))

                anthropic_message = {
                    "role": role,
                    "content": content
                }
                anthropic_messages.append(anthropic_message)

                log_llm_info(
                    "CORE",
                    f"消息格式转换[{i}] - {role}",
                    request_id,
                    content_length=len(content),
                    content_preview=content[:100] + "..." if len(content) > 100 else content,
                    conversion_success=True
                )

            log_llm_info(
                "CORE",
                "消息格式转换完成",
                request_id,
                output_format="Anthropic API消息列表",
                output_count=len(anthropic_messages),
                conversion_success=True
            )

            # 构建完整的提示词用于日志记录（与MRC原实现一致）
            full_prompt = ""
            for msg in anthropic_messages:
                full_prompt += f"{msg['role']}: {msg['content']}\n"

            log_llm_info(
                "CORE",
                "准备调用Anthropic Claude API",
                request_id,
                content=full_prompt.strip(),
                total_prompt_length=len(full_prompt),
                api_start_time=start_time,
                api_model=model,
                api_max_tokens=max_tokens
            )

            # 记录API调用开始（与MRC原实现一致）
            api_call_start = time.time()
            log_llm_info(
                "CORE",
                "调用Anthropic Claude API",
                request_id,
                api_endpoint="anthropic.messages.create",
                model=model,
                max_tokens=max_tokens,
                message_count=len(anthropic_messages),
                api_call_start_time=api_call_start
            )

            # 过滤掉max_tokens参数，避免重复传递（与MRC原实现一致）
            filtered_kwargs = {k: v for k, v in kwargs.items() if k != 'max_tokens'}

            # 使用流式响应以支持长tokens请求（与MRC原实现一致）
            response = self.client.messages.create(
                model=model,
                max_tokens=40960,  # 使用传入的参数而不是硬编码
                messages=anthropic_messages,
                stream=True,  # 启用流式响应以避免10分钟超时限制
                **filtered_kwargs
            )

            # 处理流式响应（与MRC原实现一致）
            full_response = ""
            first_content_time = None

            for event in response:
                if event.type == "content_block_delta":
                    if event.delta.type == "text_delta":
                        full_response += event.delta.text
                        # 记录第一个内容块的时间（用于性能分析）
                        if first_content_time is None:
                            first_content_time = time.time()

                elif event.type == "message_stop":
                    # 流式响应结束
                    break

            api_call_end = time.time()
            api_call_duration = api_call_end - api_call_start
            response_time = time.time() - start_time
            response_content = full_response

            log_llm_info(
                "CORE",
                "Anthropic API调用成功（流式响应）",
                request_id,
                api_response_id="STREAMING_RESPONSE",
                api_call_duration=f"{api_call_duration:.3f}s",
                api_model=model,
                api_stop_reason="STREAMING_COMPLETE",
                response_content_preview=response_content[:100] + "..." if len(response_content) > 100 else response_content,
                streaming_enabled=True,
                response_length=len(response_content)
            )

            # 估算token使用量（与MRC原实现一致）
            prompt_text = " ".join([
                msg.get('content', '') if isinstance(msg, dict) else
                getattr(msg, 'content', '') if hasattr(msg, 'content') else ''
                for msg in messages
            ])
            usage = {
                'prompt_tokens': len(prompt_text) // 4,
                'completion_tokens': len(response_content) // 4,
                'total_tokens': (len(prompt_text) + len(response_content)) // 4
            }

            # 检查响应质量（与MRC原实现一致）
            is_api_success = bool(response_content and not response_content.startswith('调用失败'))
            if is_api_success:
                log_llm_info(
                    "CORE",
                    "Anthropic LLM响应生成成功",
                    request_id,
                    response_length=len(response_content),
                    response_time=f"{response_time:.3f}s",
                    api_call_time=f"{api_call_duration:.3f}s",
                    model=model,  # 使用传入的model参数
                    provider="anthropic",
                    usage=usage,
                    is_success=True,
                    response_quality="正常"
                )

                # 记录完整的返回内容（与MRC原实现一致）
                log_llm_info(
                    "CORE",
                    "Anthropic LLM返回内容详情",
                    request_id,
                    content=response_content,
                    content_length=len(response_content),
                    is_success=True,
                    response_source="anthropic_api"
                )
            else:
                log_llm_error(
                    "CORE",
                    "Anthropic LLM响应异常或为空",
                    request_id,
                    response_content=response_content,
                    response_time=f"{response_time:.3f}s",
                    api_call_time=f"{api_call_duration:.3f}s",
                    model=model,  # 使用传入的model参数
                    provider="anthropic",
                    usage=usage,
                    is_success=False,
                    response_quality="异常"
                )

            llm_response = LLMResponse(
                content=response_content,
                model=model,
                usage=usage,
                response_time=response_time,
                provider="anthropic"
            )

            # 记录最终响应对象（与MRC原实现一致）
            log_llm_info(
                "CORE",
                "LLMResponse对象构建完成",
                request_id,
                response_object_id=id(llm_response),
                response_content_length=len(llm_response.content),
                response_model=llm_response.model,
                response_provider=llm_response.provider,
                response_time=llm_response.response_time,
                total_processing_time=f"{response_time:.3f}s"
            )

            return llm_response

        except anthropic.APIError as e:
            end_time = time.time()
            total_time = end_time - start_time

            log_llm_error(
                "CORE",
                "Anthropic API错误",
                request_id,
                api_error=str(e),
                api_error_type=e.type if hasattr(e, 'type') else 'Unknown',
                api_status_code=e.status_code if hasattr(e, 'status_code') else 'Unknown',
                response_time=f"{total_time:.3f}s",
                error_details=str(e)
            )

            response_time = time.time() - start_time
            return LLMResponse(
                content=f"Anthropic API错误: {str(e)}",
                model=model or self.default_model,
                usage={'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0},
                response_time=response_time,
                provider="anthropic"
            )

        except Exception as e:
            end_time = time.time()
            total_time = end_time - start_time

            log_llm_error(
                "CORE",
                "LLM响应生成异常",
                request_id,
                error=str(e),
                error_type=type(e).__name__,
                response_time=f"{total_time:.3f}s",
                model=model or self.default_model,
                max_tokens=max_tokens or self.default_max_tokens,
                stack_trace=f"异常位置: {__name__}.generate_response",
                exception_details=str(e)
            )

            response_time = time.time() - start_time
            return LLMResponse(
                content=f"调用失败: {str(e)}",
                model=model or self.default_model,
                usage={'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0},
                response_time=response_time,
                provider="anthropic"
            )

    def quick_test(self) -> bool:
        """
        快速测试函数，验证LLM连接是否正常（与MRC原实现一致）

        Returns:
            bool: 测试是否成功
        """
        try:
            test_prompt = "请简单回答：1+1等于几？"
            response = self.llm_call_with_history(test_prompt)
            success = bool(response and not response.startswith("调用失败"))

            if success:
                logger.info("✅ LLM连接测试成功！")
            else:
                logger.error(f"❌ LLM连接测试失败: {response}")

            return success

        except Exception as e:
            logger.error(f"❌ LLM连接测试失败: {str(e)}")
            return False


# 全局实例（与MRC原实现一致）
simple_llm_service = SimpleLLMService()


def get_llm_service() -> SimpleLLMService:
    """
    获取LLM服务实例（与MRC原实现一致）

    Returns:
        SimpleLLMService: LLM服务实例
    """
    return simple_llm_service


if __name__ == "__main__":
    # 如果直接运行此文件，执行快速测试（与MRC原实现一致）
    print("正在测试简化Anthropic LLM连接...")
    simple_llm_service.quick_test()
