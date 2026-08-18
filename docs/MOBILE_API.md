# XNAT Mobile API v1

XNAT Panel `v1.4.2` 继续保持 **Mobile API v1**。本轮仅增加向后兼容字段与新接口，不改变既有 Android v1.1.0 已使用的接口语义，也不要求 Host Agent / Agent API 升级。

Base prefix: `/api/v1`

浏览器 Web Panel 继续使用 Session + CSRF；Mobile API 使用 Bearer Token。两套入口复用同一用户、余额、套餐、VPS、Host Agent、任务队列、充值、工单与审计数据。

## 认证

- `GET /api/v1/health`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/logout`
- `GET /api/v1/me`
- `GET /api/v1/dashboard`

登录复用既有登录失败/IP 封禁、TOTP 和 LoginSession。Bearer Token 只以 SHA-256 hash 存储于 `login_sessions` 表。默认有效期 30 天，可通过 `MOBILE_TOKEN_TTL_DAYS` 调整。

## 服务器

- `GET /api/v1/servers`
- `GET /api/v1/servers/{server_id}`
- `POST /api/v1/servers/{server_id}/action` — `start | stop | reboot`
- `GET /api/v1/system-images`
- `POST /api/v1/servers/{server_id}/reinstall`
- `POST /api/v1/servers/{server_id}/delete`
- `POST /api/v1/servers/{server_id}/traffic/reset`
- `POST /api/v1/servers/{server_id}/ports`
- `DELETE /api/v1/servers/{server_id}/ports/{mapping_id}`

服务器 payload 在既有字段上新增：

- `plan_name`
- `status_label`
- `traffic_cycle_start` / `traffic_cycle_end`
- `traffic_cycle_mode` / `traffic_cycle_day`
- `traffic_reset_price_cents`
- `traffic_reset_available`
- `traffic_reset_reason`

既有 `display_id / country / country_name / region / region_code / network_line / nat_port` 保留不变。删除确认继续以稳定展示编号（例如 `TYO-0002`）为主；旧内部实例名仅作为历史兼容别名。

删除请求 JSON：

```json
{"confirm_name":"TYO-0002"}
```

删除复用既有 `delete_server` Job；重复提交时返回正在执行的同一任务，不重复入队。

流量重置复用 Web Panel 的完整付费逻辑：仅流量用尽后允许执行，检查生命周期、套餐重置价格和余额，创建 `traffic_reset` 订单、扣除余额、开启新流量周期、恢复带宽、写入审计并发送通知。带宽即时恢复失败时，流量与账务仍按既有 Web 逻辑完成，并通过 `provider_warning` 返回后台重试提示。

## 套餐与购买

- `GET /api/v1/catalog`
- `POST /api/v1/purchase/quote`
- `POST /api/v1/purchase`

套餐 payload 保留 `region / network_line / nat_port`，并新增 `traffic_reset_price_cents`。购买流程继续复用库存、SystemImage、Coupon、余额、订单、Host 调度与 `provision_server` Job。`request_id` 幂等语义不变。

## 原生 USDT 充值

- `GET /api/v1/recharge/config`
- `POST /api/v1/recharges`
- `GET /api/v1/recharges/{recharge_id}`
- `POST /api/v1/recharges/{recharge_id}/cancel`
- `POST /api/v1/recharges/{recharge_id}/txid`

`GET /recharge/config` 只下发 App 展示需要的公开配置：启用状态、CNY/USDT 汇率、充值范围、订单有效期以及 TRON / Polygon 通道状态；不会下发 RPC、API Key 或其他运行时秘密。

创建充值订单示例：

```json
{"chain":"tron","amount":"50.00"}
```

订单详情会返回精确 USDT 数量、收款地址、Token 合约、状态中文映射、剩余秒数和是否允许取消。自动模式仍由 Panel 后台扫链；人工模式通过 `/txid` 提交 64 位交易哈希。取消与异常支付保护直接复用现有充值状态机。

## 账务

- `GET /api/v1/billing`
- `GET /api/v1/billing?month=2026-08`

不带 `month` 时保持旧客户端兼容：返回最近订单、余额流水和充值记录。带 `YYYY-MM` 时返回该自然月完整记录，并额外返回：

- `selected_month`
- `available_months`
- 订单 `status_label / kind_label`
- 流水 `kind_label`
- 充值 `status_label / expected_usdt_text / rate_text`

账务汇总中的 `total_spend_cents` 同时统计有效的 `paid` 与 `completed` 订单，避免新购订单使用 `paid` 状态时被漏计。

## 支持工单

- `GET /api/v1/tickets`
- `POST /api/v1/tickets`
- `GET /api/v1/tickets/{ticket_id}`
- `POST /api/v1/tickets/{ticket_id}/reply`
- `POST /api/v1/tickets/{ticket_id}/close`

## 兼容性

- Panel：v1.4.2
- Mobile API：v1
- XNAT Android：v1.1.0 继续兼容；v1.2.0 可接入本轮新增接口
- Host Agent：v1.1.1
- Agent API：v1

本轮无破坏性数据库迁移，不新增 Host Agent 协议要求。
