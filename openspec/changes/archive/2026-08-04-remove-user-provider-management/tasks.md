## 1. 后端移除

- [x] 1.1 删除用户 Provider API、Service、schemas 与 repository 方法
- [x] 1.2 删除 Agent run 的用户 Provider runtime snapshot 解析
- [x] 1.3 从诊断、capabilities、设置导入导出和审计域移除 providers
- [x] 1.4 新增迁移删除用户 Provider 与模型用途绑定表

## 2. 前端收敛

- [x] 2.1 删除 Provider CRUD、凭据、连接测试和用途绑定 API client
- [x] 2.2 将模型设置改为平台模型目录只读视图
- [x] 2.3 更新设置导航、概览和诊断中的 Provider 文案

## 3. 验证与文档

- [x] 3.1 更新后端测试，验证旧 Provider 路由不可用且聊天使用平台模型
- [x] 3.2 运行后端完整测试与迁移检查
- [x] 3.3 运行前端 lint/build 并执行产品文案审查
- [x] 3.4 更新设置与模型架构文档
