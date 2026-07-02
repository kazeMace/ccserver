"""
tests/test_poller_timed_set.py — TeamMailboxPoller._TimedSet 测试。

覆盖：
  - add / contains 基础操作
  - TTL 过期后条目被清除
  - 过期条目不影响未过期条目
  - 重复 add 更新时间戳
"""

import time
import pytest

from ccserver.team.poller import _TimedSet


class TestTimedSet:

    def test_add_and_contains(self):
        """添加后应能找到。"""
        s = _TimedSet(ttl_seconds=10)
        s.add("a")
        assert "a" in s

    def test_not_contains_before_add(self):
        """未添加的 key 应不在集合中。"""
        s = _TimedSet(ttl_seconds=10)
        assert "x" not in s

    def test_expired_item_not_found(self):
        """TTL 过期后条目应被清除，contains 返回 False。"""
        s = _TimedSet(ttl_seconds=0.01)  # 极短 TTL
        s.add("key")
        time.sleep(0.05)  # 等待过期
        assert "key" not in s
        assert "key" not in s._data

    def test_unexpired_item_survives(self):
        """TTL 未到期的条目应保留。"""
        s = _TimedSet(ttl_seconds=100)
        s.add("keep")
        assert "keep" in s

    def test_expired_items_dont_affect_fresh(self):
        """过期条目清除时不影响未过期条目。"""
        s = _TimedSet(ttl_seconds=0.01)
        s.add("old")
        time.sleep(0.05)
        s.add("new")  # 触发 _purge，清除 old
        assert "old" not in s
        assert "new" in s

    def test_readd_refreshes_timestamp(self):
        """重新 add 应更新时间戳，延长存活时间。"""
        s = _TimedSet(ttl_seconds=0.05)
        s.add("key")
        time.sleep(0.03)
        s.add("key")  # 刷新时间戳
        time.sleep(0.03)
        # 从首次 add 算起超过 0.05s，但刷新后不应过期
        assert "key" in s

    def test_purge_keeps_latest_ordered(self):
        """批量添加后只有最旧的过期条目被清除。"""
        s = _TimedSet(ttl_seconds=0.05)
        s.add("old1")
        s.add("old2")
        time.sleep(0.06)
        s.add("fresh")
        assert "old1" not in s
        assert "old2" not in s
        assert "fresh" in s
