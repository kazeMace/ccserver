"""
loader — 配置加载器：集中定义三作用域层叠优先级（唯一一处）。

层叠链（见 spec §5）：
  ProcessConfig.load:  DEFAULTS → 全局文件 → 环境变量
  resolve_session:     ProcessConfig → 项目文件 → 会话/请求覆盖
  resolve_agent:       SessionConfig → AgentDef 覆盖 → spawn 覆盖

合并语义：
  deep_merge —— dict 深合并；标量/list 后者覆盖前者；
                permissions 段例外，走专用合并（deny/ask 并集、allow 覆盖）。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Optional

from loguru import logger

from .schema import CcServerConfig
from .env_map import apply_env, ENV_MAP


# ─── 合并 ────────────────────────────────────────────────────────────────────


def _merge_permissions(base: dict, over: dict) -> dict:
    """
    permissions 专用合并（保留旧 settings 的更严格语义）：
      deny  = base ∪ over   （并集，更严格）
      allow = over 存在则覆盖 base（None/缺省则沿用 base）
      ask   = base ∪ over   （并集）
    """
    base = base or {}
    over = over or {}

    def _union(a, b):
        seen = []
        for x in (a or []) + (b or []):
            if x not in seen:
                seen.append(x)
        return seen

    out = dict(base)
    out["deny"] = _union(base.get("deny"), over.get("deny"))
    out["ask"] = _union(base.get("ask"), over.get("ask"))
    if "allow" in over and over.get("allow") is not None:
        out["allow"] = list(over["allow"])
    else:
        out["allow"] = list(base.get("allow", []) or [])
    return out


def deep_merge(base: dict, over: dict) -> dict:
    """
    深合并两个配置 dict，返回新 dict（不修改入参）。

      - dict 值递归合并
      - 标量 / list 值：over 整体覆盖 base
      - "permissions" 段：走 _merge_permissions（deny/ask 并集、allow 覆盖）
    """
    if not over:
        return copy.deepcopy(base)
    out = copy.deepcopy(base)
    for key, over_val in over.items():
        if key == "permissions":
            out[key] = _merge_permissions(out.get(key, {}), over_val)
        elif isinstance(over_val, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], over_val)
        else:
            out[key] = copy.deepcopy(over_val)
    return out


# ─── 读取文件 ────────────────────────────────────────────────────────────────


def _load_json(path: Optional[Path], label: str) -> dict:
    """读取单个 JSON 配置文件，返回 dict（文件不存在/解析失败返回空 dict）。"""
    if path is None:
        return {}
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        logger.debug("config loaded | label={} path={}", label, path)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.error("Failed to parse config | label={} path={} error={}", label, path, e)
        return {}


# ─── ProcessConfig（进程级共享底座）─────────────────────────────────────────


class ProcessConfig:
    """
    PROCESS 作用域配置：进程启动时解析一次、跨 Session 共享。

    承载 infra + 默认 model + 密钥（用 CcServerConfig 同一类型表达完整底座）。
    """

    @staticmethod
    def load(global_file: Optional[Path] = None, environ: dict = None) -> CcServerConfig:
        """
        解析进程级配置：DEFAULTS → 全局文件 → 环境变量。

        Args:
            global_file: 全局配置文件路径；None 时用默认 ~/.ccserver/settings.json
            environ:     环境变量来源（测试可注入），默认 os.environ

        Returns:
            CcServerConfig（进程级底座；后续 resolve_session 在其上叠加）
        """
        # 1. 内置默认值（来自 dataclass 默认）
        data = CcServerConfig().as_dict()

        # 全局文件路径默认值：DEFAULTS 里的 global_config_dir/settings.json
        if global_file is None:
            global_dir = Path(data["infra"]["global_config_dir"])
            global_file = global_dir / "settings.json"

        # 2. 全局文件
        data = deep_merge(data, _load_json(global_file, label="global"))

        # 3. 环境变量
        data = apply_env(data, ENV_MAP, environ=environ)

        cfg = CcServerConfig.from_dict(data)
        logger.debug(
            "ProcessConfig loaded | model={} storage={} prompt_lib={}",
            cfg.model.model_id, cfg.infra.storage_backend, cfg.agent.prompt_lib,
        )
        return cfg


# ─── SESSION / AGENT 作用域解析 ──────────────────────────────────────────────


def resolve_session(
    process_cfg: CcServerConfig,
    project_file: Optional[Path] = None,
    overrides: Optional[dict] = None,
) -> CcServerConfig:
    """
    SESSION 作用域解析：ProcessConfig → 项目文件 → 会话/请求覆盖。

    Args:
        process_cfg: 进程级底座
        project_file: <project_root>/.ccserver/settings.local.json
        overrides:    会话/请求级编程式覆盖（最高优先级）

    Returns:
        CcServerConfig（该会话完整配置）
    """
    data = process_cfg.as_dict()
    data = deep_merge(data, _load_json(project_file, label="project"))
    if overrides:
        data = deep_merge(data, overrides)
    return CcServerConfig.from_dict(data)


def resolve_agent(
    session_cfg: CcServerConfig,
    agent_overrides: Optional[dict] = None,
    spawn_overrides: Optional[dict] = None,
) -> CcServerConfig:
    """
    AGENT 作用域解析：SessionConfig → AgentDef 覆盖 → spawn 覆盖。

    Args:
        session_cfg:     会话配置
        agent_overrides: AgentDef.overrides() 产出的部分覆盖 dict
        spawn_overrides: spawn/create_root 传参覆盖（最高优先级）

    Returns:
        CcServerConfig（该 agent 最终生效配置）
    """
    data = session_cfg.as_dict()
    if agent_overrides:
        data = deep_merge(data, agent_overrides)
    if spawn_overrides:
        data = deep_merge(data, spawn_overrides)
    return CcServerConfig.from_dict(data)


# ─── 进程级配置单例（供无 session 的 infra/叶子消费点使用）──────────────────

_process_config_singleton: Optional[CcServerConfig] = None


def get_process_config(reload: bool = False) -> CcServerConfig:
    """
    返回进程级共享配置（ProcessConfig.load 的缓存结果）。

    用于 log/storage/mcp 等在 Session 之前就需要 infra 配置、
    或没有 session 句柄的叶子组件。整个进程只解析一次（除非 reload=True）。

    Args:
        reload: True 时强制重新加载（测试或运行时重配可用）

    Returns:
        CcServerConfig（进程级底座）
    """
    global _process_config_singleton
    if _process_config_singleton is None or reload:
        _process_config_singleton = ProcessConfig.load()
    return _process_config_singleton

