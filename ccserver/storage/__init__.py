from .base import StorageAdapter, SessionRecord
from .file_adapter import FileStorageAdapter
from .sqlite_adapter import SQLiteStorageAdapter

__all__ = ["StorageAdapter", "SessionRecord", "FileStorageAdapter", "SQLiteStorageAdapter", "build_storage"]


def build_storage(
    backend: str,
    sessions_base,
    db_path,
    mongo_uri: str = "mongodb://localhost:27017",
    mongo_db: str = "ccserver",
    redis_url: str = "redis://localhost:6379",
    redis_cache_size: int = 100,
    redis_ttl: int = 86400,
) -> StorageAdapter:
    """
    根据 backend 名称创建对应的 StorageAdapter。

    backend: "file"（默认）、"sqlite" 或 "mongo"
    mongo / redis 参数仅在 backend="mongo" 时使用。
    """
    if backend == "file":
        return FileStorageAdapter(sessions_base)
    if backend == "sqlite":
        return SQLiteStorageAdapter(db_path)
    if backend == "mongo":
        from .mongo_adapter import MongoStorageAdapter
        from .redis_cache import RedisMessageCache
        from .cached_adapter import CachedStorageAdapter
        inner = MongoStorageAdapter(mongo_uri, mongo_db)
        cache = RedisMessageCache(redis_url, redis_cache_size, redis_ttl)
        return CachedStorageAdapter(inner, cache)
    raise ValueError(f"Unknown storage backend: '{backend}'，可选值：file / sqlite / mongo")
