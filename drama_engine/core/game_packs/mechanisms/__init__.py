"""领域机制实现。

每个模块提供一组原子、可复用的机制（effect / condition handler），通过 register 函数
注册进 PluginRegistry。机制粒度以「换个同类游戏还用得上」为准。
"""
