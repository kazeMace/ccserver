"""
managers/cron/tools.py — build_cron_tools() 工厂函数。

为 TaskScheduler 创建四个内置工具实例：
    BTCronCreate / BTCronDelete / BTCronList / BTCronUpdate

向后兼容：返回的工具字典键名不变，旧代码可直接使用。
"""

from ccserver.builtins.tools.cron_create import BTCronCreate
from ccserver.builtins.tools.cron_delete import BTCronDelete
from ccserver.builtins.tools.cron_list import BTCronList
from ccserver.builtins.tools.cron_update import BTCronUpdate


def build_cron_tools(cron_scheduler) -> dict:
    """
    为指定 TaskScheduler 创建工具实例字典。

    Args:
        cron_scheduler: TaskScheduler 实例（通常为 session.cron_scheduler）

    Returns:
        {tool_name: tool_instance} 字典，供 ToolManager 合并使用。
    """
    return {
        BTCronCreate.name: BTCronCreate(cron_scheduler),
        BTCronDelete.name: BTCronDelete(cron_scheduler),
        BTCronList.name:   BTCronList(cron_scheduler),
        BTCronUpdate.name: BTCronUpdate(cron_scheduler),
    }
