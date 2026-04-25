"""
managers/cron/tools.py — build_cron_tools() 工厂函数。

为 CronScheduler 创建三个内置工具实例：
    BTCronCreate / BTCronDelete / BTCronList
"""

from ccserver.builtins.tools.cron_create import BTCronCreate
from ccserver.builtins.tools.cron_delete import BTCronDelete
from ccserver.builtins.tools.cron_list import BTCronList


def build_cron_tools(cron_scheduler) -> dict:
    """
    为指定 CronScheduler 创建工具实例字典。

    Args:
        cron_scheduler: CronScheduler 实例（通常为 session.cron_scheduler）

    Returns:
        {tool_name: tool_instance} 字典，供 ToolManager 合并使用。
    """
    return {
        BTCronCreate.name: BTCronCreate(cron_scheduler),
        BTCronDelete.name: BTCronDelete(cron_scheduler),
        BTCronList.name:   BTCronList(cron_scheduler),
    }
