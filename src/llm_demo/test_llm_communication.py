#!/usr/bin/env python3
"""
llm_communication.py 功能测试文件

测试llm_communication.py中的每个函数和类
无需环境检查，专注于功能验证
"""

import os
import sys
import logging
import time
from typing import List, Dict, Any

# 设置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def print_section(title: str):
    """打印格式化的章节标题"""
    print(f"\n{'='*60}")
    print(f" {title}")
    print('='*60)

def print_result(test_name: str, result: any, success: bool = True):
    """打印测试结果"""
    status = "✅" if success else "❌"
    print(f"{status} {test_name}")
    if result is not None:
        print(f"   结果: {str(result)[:100]}{'...' if len(str(result)) > 100 else ''}")

def test_imports():
    """测试模块导入"""
    print_section("测试1: 模块导入")

    try:
        from llm_communication import (
            SimpleLLMService,
            LLMResponse,
            get_llm_service,
            simple_llm_service
        )
        print_result("基础导入成功", None, True)
        return {
            'SimpleLLMService': SimpleLLMService,
            'LLMResponse': LLMResponse,
            'get_llm_service': get_llm_service,
            'simple_llm_service': simple_llm_service
        }
    except ImportError as e:
        print_result("模块导入失败", str(e), False)
        return None

def test_data_classes(classes: dict):
    """测试数据类"""
    print_section("测试2: 数据类")

    if not classes:
        print_result("跳过数据类测试", "前序测试失败", False)
        return

    # 测试LLMResponse
    try:
        response = classes['LLMResponse'](
            content="测试内容",
            model="test-model",
            usage={'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30},
            response_time=1.5,
            provider="test-provider"
        )
        print_result("LLMResponse创建成功", f"内容: {response.content}, 模型: {response.model}", True)

        # 测试属性访问
        print_result("LLMResponse属性访问",
                    f"provider: {response.provider}, time: {response.response_time}", True)

    except Exception as e:
        print_result("LLMResponse测试失败", str(e), False)

def test_service_initialization():
    """测试服务初始化"""
    print_section("测试3: 服务初始化")

    try:
        from llm_communication import SimpleLLMService

        # 测试无参数初始化
        service1 = SimpleLLMService()
        print_result("无参数初始化成功", f"默认模型: {service1.default_model}", True)

        # 测试带参数初始化
        service2 = SimpleLLMService(api_key="test-key")
        print_result("带参数初始化成功", f"API密钥已设置: {bool(service2.api_key)}", True)

        return service1, service2

    except Exception as e:
        print_result("服务初始化失败", str(e), False)
        return None, None

def test_basic_methods(service):
    """测试基本方法"""
    print_section("测试4: 基本方法")

    if not service:
        print_result("跳过基本方法测试", "服务初始化失败", False)
        return

    # 测试llm_call方法
    try:
        prompt = "测试提示词"
        print(f"🧪 调用llm_call: {prompt}")

        start_time = time.time()
        response = service.llm_call(prompt)
        end_time = time.time()

        print_result("llm_call方法执行",
                    f"响应: {response[:50]}..., 耗时: {end_time-start_time:.2f}秒",
                    True)

    except Exception as e:
        print_result("llm_call方法失败", str(e), False)

    # 测试simple_llm方法
    try:
        prompt = "测试simple_llm提示词"
        print(f"🧪 调用simple_llm: {prompt}")

        response = service.simple_llm(prompt, model="claude-3-sonnet-20240229", max_tokens=100)
        print_result("simple_llm方法执行",
                    f"响应: {response[:50]}...",
                    True)

    except Exception as e:
        print_result("simple_llm方法失败", str(e), False)

def test_advanced_methods(service):
    """测试高级方法"""
    print_section("测试5: 高级方法")

    if not service:
        print_result("跳过高级方法测试", "服务初始化失败", False)
        return

    # 测试llm_call_with_history
    try:
        history = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！有什么可以帮助你的吗？"}
        ]
        prompt = "现在的问题是：什么是人工智能？"

        print(f"🧪 调用llm_call_with_history: 历史消息{len(history)}条")

        response = service.llm_call_with_history(prompt, history)
        print_result("llm_call_with_history方法执行",
                    f"响应: {response[:50]}...",
                    True)

    except Exception as e:
        print_result("llm_call_with_history方法失败", str(e), False)

    # 测试generate_response
    try:
        messages = [
            {"role": "user", "content": "请解释Python是什么"},
            {"role": "assistant", "content": "Python是一种编程语言"},
            {"role": "user", "content": "它有什么特点？"}
        ]

        print(f"🧪 调用generate_response: 消息{len(messages)}条")

        response = service.generate_response(messages)
        print_result("generate_response方法执行",
                    f"内容: {response.content[:50]}..., 模型: {response.model}",
                    True)

        # 测试LLMResponse对象
        print_result("LLMResponse对象验证",
                    f"提供商标记: {response.provider}, 响应时间: {response.response_time:.2f}s",
                    True)

    except Exception as e:
        print_result("generate_response方法失败", str(e), False)

def test_utility_methods():
    """测试工具方法"""
    print_section("测试6: 工具方法")

    try:
        from llm_communication import get_llm_service, simple_llm_service

        # 测试get_llm_service
        service = get_llm_service()
        print_result("get_llm_service函数",
                    f"返回类型: {type(service).__name__}",
                    True)

        # 测试全局服务实例
        print_result("全局服务实例",
                    f"类型: {type(simple_llm_service).__name__}",
                    True)

        # 测试quick_test方法
        test_result = simple_llm_service.quick_test()
        print_result("quick_test方法",
                    f"测试结果: {test_result}",
                    True)

    except Exception as e:
        print_result("工具方法失败", str(e), False)

def test_error_handling():
    """测试错误处理"""
    print_section("测试7: 错误处理")

    try:
        from llm_communication import SimpleLLMService

        service = SimpleLLMService()

        # 测试空提示词
        try:
            response = service.llm_call("")
            print_result("空提示词处理",
                        f"响应: {response[:50]}...",
                        True)
        except Exception as e:
            print_result("空提示词异常", str(e), True)

        # 测试非常长的提示词
        try:
            long_prompt = "请解释" + "很长" * 1000
            start_time = time.time()
            response = service.llm_call(long_prompt)
            end_time = time.time()
            print_result("长提示词处理",
                        f"响应长度: {len(response)}, 耗时: {end_time-start_time:.2f}s",
                        True)
        except Exception as e:
            print_result("长提示词异常", str(e), True)

        # 测试无效历史记录
        try:
            invalid_history = [{"role": "invalid", "content": "test"}]
            response = service.llm_call_with_history("测试", invalid_history)
            print_result("无效历史记录处理",
                        f"响应: {response[:50]}...",
                        True)
        except Exception as e:
            print_result("无效历史记录异常", str(e), True)

    except Exception as e:
        print_result("错误处理测试失败", str(e), False)

def test_performance():
    """测试性能"""
    print_section("测试8: 性能测试")

    try:
        from llm_communication import simple_llm_service

        test_prompts = [
            "什么是Python？",
            "解释机器学习",
            "什么是数据库？"
        ]

        response_times = []

        for i, prompt in enumerate(test_prompts):
            print(f"🧪 性能测试 {i+1}/{len(test_prompts)}: {prompt}")

            start_time = time.time()
            response = simple_llm_service.llm_call(prompt)
            end_time = time.time()

            response_time = end_time - start_time
            response_times.append(response_time)

            success = response and not response.startswith("调用失败")
            print_result(f"测试{i+1}完成",
                        f"耗时: {response_time:.2f}s, 成功: {success}",
                        success)

        if response_times:
            avg_time = sum(response_times) / len(response_times)
            min_time = min(response_times)
            max_time = max(response_times)

            print_result("性能统计",
                        f"平均: {avg_time:.2f}s, 最快: {min_time:.2f}s, 最慢: {max_time:.2f}s",
                        True)

    except Exception as e:
        print_result("性能测试失败", str(e), False)

def main():
    """主测试函数"""
    print("🚀 llm_communication.py 完整功能测试")
    print("测试所有函数和类的功能")

    # 执行所有测试
    classes = test_imports()
    test_data_classes(classes)
    service1, service2 = test_service_initialization()
    test_basic_methods(service1)
    test_advanced_methods(service2)
    test_utility_methods()
    test_error_handling()
    test_performance()

    print_section("测试完成总结")
    print("📋 测试覆盖的功能:")
    print("✅ 模块导入")
    print("✅ 数据类 (LLMResponse)")
    print("✅ 服务初始化")
    print("✅ 基本方法 (llm_call, simple_llm)")
    print("✅ 高级方法 (llm_call_with_history, generate_response)")
    print("✅ 工具方法 (get_llm_service, quick_test)")
    print("✅ 错误处理")
    print("✅ 性能测试")

    print("\n🎉 所有功能测试完成！")
    print("💡 如果某些测试失败，可能是因为:")
    print("   1. 缺少 anthropic 包: pip install anthropic")
    print("   2. 没有配置认证方式 (环境变量或Claude CLI)")
    print("   3. 网络连接问题")

if __name__ == "__main__":
    main()