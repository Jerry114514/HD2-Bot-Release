"""统一图片渲染基建：设计 Token、流式布局、分页合图。

业务插件统一 ``from astrbot_plugin_hd2_common.ui import ...``，禁止再反向
import ``astrbot_plugin_hd2_war_report.report_card`` 的绘图内部实现。
"""
from astrbot_plugin_hd2_common.ui import components, layout, pages, tokens  # noqa: F401
