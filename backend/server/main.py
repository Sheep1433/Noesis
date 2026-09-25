"""server.main：web app 的兼容导出（worker-role-split Phase 3）。

单进程全干入口已退役（``backend/app.py`` 删除）。进程角色三分：
``web.py`` / ``control.py`` / ``worker.py`` 三入口，装配实现位于
``server/bootstrap/entries.py``。本模块保留 ``app`` 导出供测试基建
（TestClient / CSRF 挂载守卫）引用——web 面承载全部业务路由。
"""

from server.bootstrap.entries import build_web_app

app = build_web_app()

__all__ = ["app"]
