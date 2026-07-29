"""Unity Agent: a Bezi-like natural-language layer over the Unity Editor.

This package extends the InplusCode harness with Unity Editor capabilities via
MCP for Unity. It adds new tools, permissions, and domain knowledge without
forking the core agent loop.

Public entry point:
    build_unity_runtime(tools, handlers, config=None) -> UnityRuntime
"""

from __future__ import annotations

from .bootstrap import UnityRuntime, build_unity_runtime

__all__ = ["UnityRuntime", "build_unity_runtime"]  # init.py中的 __all__ 用于指定包的公共接口, 这是对外暴露的类和函数列表
# 主要影响from unity_agent import * 时的行为，只会导入 __all__ 中列出的类和函数
