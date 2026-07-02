"""
env_map — 环境变量集中登记表（唯一一处列全所有受支持的环境变量）。

设计（见 spec §6）：
  ENV_MAP: 环境变量名 → 配置路径（"section.field"）。
  apply_env(): 遍历环境变量，命中则按目标字段类型转换后写入嵌套 dict。

类型转换规则显式列举（不做反射魔法，新人易懂）：
  _INT_PATHS  里的目标 → int(value)
  _BOOL_PATHS 里的目标 → 解析为 bool
  其余（含路径字段）   → 原样字符串（路径在 schema.from_dict 里再转 Path）
"""

from __future__ import annotations

import os


# 环境变量名 → 配置路径
ENV_MAP: dict = {
    # ── model ──
    "CCSERVER_MODEL":            "model.model_id",
    "CCSERVER_API_TYPE":         "model.api_type",
    "CCSERVER_PROVIDER":         "model.provider",
    "CCSERVER_BASE_URL":         "model.base_url",
    "CCSERVER_API_KEY":          "model.api_key",
    "ANTHROPIC_API_KEY":         "model.api_key",       # 便捷别名
    # ── vlm ──
    "CCSERVER_VLM_MODEL":        "vlm.model_id",
    "CCSERVER_VLM_API_KEY":      "vlm.api_key",
    "CCSERVER_VLM_BASE_URL":     "vlm.base_url",
    "CCSERVER_VLM_PROVIDER":     "vlm.provider",
    "CCSERVER_VLM_PRIORITY":     "vlm.priority",
    # ── agent ──
    "CCSERVER_PROMPT_LIB":        "agent.prompt_lib",
    "CCSERVER_MAIN_ROUNDS":       "agent.main_round_limit",
    "CCSERVER_SUB_ROUNDS":        "agent.sub_round_limit",
    "CCSERVER_MAX_DEPTH":         "agent.max_depth",
    "CCSERVER_INJECT_SYSTEM_FILE":"agent.inject_system_file",
    "CCSERVER_APPEND_SYSTEM":     "agent.append_system",
    # ── compaction ──
    "CCSERVER_THRESHOLD":         "compaction.threshold",
    "CCSERVER_KEEP_RECENT":       "compaction.keep_recent",
    # ── tools ──
    "CCSERVER_USER_AGENT_TEAM":   "tools.user_agent_team",
    # ── infra ──
    "CCSERVER_STORAGE_BACKEND":   "infra.storage_backend",
    "CCSERVER_MONGO_URI":         "infra.mongo_uri",
    "CCSERVER_MONGO_DB":          "infra.mongo_db",
    "CCSERVER_REDIS_URL":         "infra.redis_url",
    "CCSERVER_REDIS_CACHE_SIZE":  "infra.redis_cache_size",
    "CCSERVER_REDIS_TTL":         "infra.redis_ttl",
    "CCSERVER_GLOBAL_CONFIG_DIR": "infra.global_config_dir",
    "CCSERVER_SESSIONS_DIR":      "infra.sessions_base",
    "CCSERVER_DB_PATH":           "infra.db_path",
    "CCSERVER_LOG_DIR":           "infra.log_dir",
    "CCSERVER_LOG_LEVEL":         "infra.log_level",
    "CCSERVER_RECORD_DIR":        "infra.record_dir",
    "CCSERVER_PROJECT_DIR":       "infra.project_dir",
}

# 需转 int 的目标路径
_INT_PATHS = {
    "agent.main_round_limit",
    "agent.sub_round_limit",
    "agent.max_depth",
    "compaction.threshold",
    "compaction.keep_recent",
    "infra.redis_cache_size",
    "infra.redis_ttl",
}

# 需转 bool 的目标路径
_BOOL_PATHS = {
    "agent.append_system",
    "tools.user_agent_team",
}


def _to_bool(value: str) -> bool:
    """字符串转 bool：true/1/yes（不区分大小写）为 True。"""
    return str(value).strip().lower() in ("true", "1", "yes")


def _convert(path: str, value: str):
    """按目标路径类型转换环境变量字符串值。"""
    if path in _INT_PATHS:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    if path in _BOOL_PATHS:
        return _to_bool(value)
    return value  # str（含路径，留给 schema.from_dict 转 Path）


def _set_nested(data: dict, path: str, value) -> None:
    """把 value 写入 data 的嵌套路径，如 'model.model_id'。"""
    section, _, field = path.partition(".")
    assert field, f"ENV_MAP 路径必须是 'section.field'：{path}"
    data.setdefault(section, {})[field] = value


def apply_env(data: dict, env_map: dict = ENV_MAP, environ: dict = None) -> dict:
    """
    将环境变量按 env_map 覆盖到 data（就地修改并返回）。

    Args:
        data:    待覆盖的配置 dict（as_dict 形态）
        env_map: 环境变量名 → 配置路径
        environ: 环境变量来源，默认 os.environ（便于测试注入）

    Returns:
        覆盖后的 data（同一对象）
    """
    src = environ if environ is not None else os.environ
    for env_name, path in env_map.items():
        if env_name not in src:
            continue
        raw = src[env_name]
        if raw is None or raw == "":
            continue
        converted = _convert(path, raw)
        if converted is None:
            continue
        _set_nested(data, path, converted)
    return data
