# XNAT Mobile API v1

XNAT Panel v1.3.2 正式提供 Mobile API v1，主要供 XNAT Android v1.0.0 使用。

Base prefix: `/api/v1`

浏览器 Web Panel 继续使用 Session + CSRF；Mobile API 使用 Bearer Token。两套入口复用同一用户、余额、套餐、VPS、Host Agent、任务队列、工单与审计数据。

## 认证

- `GET /api/v1/health`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/logout`
- `GET /api/v1/me`
- `GET /api/v1/dashboard`

登录复用既有登录失败/IP 封禁、TOTP 和 LoginSession。Bearer Token 只以 SHA-256 hash 存储于现有 `login_sessions` 表。默认有效期 30 天，可通过 `MOBILE_TOKEN_TTL_DAYS` 调整。

## 服务器

- `GET /api/v1/servers`
- `GET /api/v1/servers/{server_id}`
- `POST /api/v1/servers/{server_id}/action` — `start | stop | reboot`
- `GET /api/v1/system-images`
- `POST /api/v1/servers/{server_id}/reinstall`
- `POST /api/v1/servers/{server_id}/ports`
- `DELETE /api/v1/servers/{server_id}/ports/{mapping_id}`

端口管理继续遵守 TCP/UDP 开关、禁用端口、每台服务端口数量、Host NAT 端口池和生命周期限制。重装复用既有 `reinstall_server` Job 与 Host Agent 链路。

## 账务

- `GET /api/v1/billing`

返回余额摘要、订单、余额流水和充值记录。

## 支持工单

- `GET /api/v1/tickets`
- `POST /api/v1/tickets`
- `GET /api/v1/tickets/{ticket_id}`
- `POST /api/v1/tickets/{ticket_id}/reply`
- `POST /api/v1/tickets/{ticket_id}/close`

## 购买服务器

- `GET /api/v1/catalog`
- `POST /api/v1/purchase/quote`
- `POST /api/v1/purchase`

购买流程复用 Web Panel 的套餐、库存、SystemImage、Coupon、余额、订单、节点调度和 `provision_server` Job。最终购买请求支持 `request_id` 幂等键，同一用户重复提交相同 request_id 时返回原订单，不再次扣款或创建服务。

## 兼容性

- Panel：v1.3.2
- Mobile API：v1
- XNAT Android：v1.0.0
- Host Agent：v1.1.0
- Agent API：v1

本 API 不要求 Host Agent 增加新的 Agent API 版本，也不包含破坏性数据库迁移。
