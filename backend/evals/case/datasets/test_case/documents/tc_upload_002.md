# 大文件分片上传与断点续传需求（虚构）

## 1. 范围说明

当单文件超过 **5MB** 时，客户端 **启用分片上传**；小于等于 5MB 仍走直传接口（见 UPLOAD-SINGLE）。合并、校验、续传状态机由本服务统一编排。不包含请假、采购等 **审批** 业务。

## 2. 分片规则

- 默认分片大小：5MB（可配置 2–10MB）
- 每个分片上传须携带：`upload_id`、`part_number`（从 1 开始）、`total_parts`
- 单片失败可单独重传，不影响已完成分片
- 服务端为每个 `upload_id` 保留已接收分片 bitmap，支持 **断点续传**

## 3. 流程

### 3.1 初始化

`POST /upload/multipart/init` → 返回 `upload_id`、建议 `part_size`

### 3.2 上传分片

`PUT /upload/multipart/{upload_id}/parts/{part_number}`，Body 为二进制；响应含 `etag`

### 3.3 合并

全部 part 成功后调用 `POST /upload/multipart/{upload_id}/complete`，服务端按序 **合并** 并计算整体 SHA256

- **合并失败可重试**：若合并超时，客户端可重试 complete；已上传分片保留 24 小时

### 3.4 取消

`DELETE /upload/multipart/{upload_id}` 释放临时空间

## 4. 断点续传场景

用户关闭页面后再次选择同一文件（按文件名+大小+最后修改时间匹配，或用户手动选择「继续上传」）：

1. 客户端查询 `GET /upload/multipart/{upload_id}/status`
2. 跳过已上传 part，仅传缺失部分
3. 全部完成后走合并

## 5. 异常与限制

| 场景 | 要求 |
|------|------|
| 分片顺序错乱 | 服务端拒绝 complete |
| 单片 MD5 不匹配 | 返回 400，要求重传该 part |
| 总大小超过租户配额 | 拒绝 init |
| upload_id 过期 | 24h 未 complete 自动清理 |

## 6. 非功能

- 单租户并发分片上传不超过 10 路
- 合并操作异步化时，须轮询或 WebSocket 通知最终 `file_id`
