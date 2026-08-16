# XNAT v1.1.0 详细使用文档

> 本文档负责详细说明 XNAT 的安装和运维。  
> 根目录 `README.md` 保持简洁，具体操作以这里为准。

---

# 1. 推荐部署顺序

```text
1. 全新 Debian 12 安装 Panel
2. 配置 Panel 域名 / HTTPS / Cloudflare
3. 记下 Panel Server 的真实公网 IPv4
4. 全新 Debian 12 安装 Host
5. Host 安装器输入 Panel 真实公网 IPv4
6. Host 安装器确认 natpool 大小
7. 登录 Panel 后台添加 Host Agent
8. Agent 连接检测成功
9. 在 Panel 后台配置该节点 NAT 端口池
10. 创建测试 VPS 验证 SSH / NAT / 磁盘 / 流量
```

---

# 2. Panel 安装

```bash
apt-get update && apt-get install -y curl ca-certificates && \
bash <(curl -fsSL https://raw.githubusercontent.com/kkx999/xnat/main/scripts/bootstrap-panel.sh)
```

### 每一段是什么意思

```bash
apt-get update
```

刷新 Debian 软件包索引。

```bash
apt-get install -y curl ca-certificates
```

安装：

- `curl`：从 GitHub 下载安装脚本。
- `ca-certificates`：验证 HTTPS 证书。
- `-y`：自动确认安装。

```bash
curl -fsSL URL
```

参数：

- `-f`：HTTP 下载失败时返回错误。
- `-s`：不显示下载进度。
- `-S`：发生错误仍显示错误信息。
- `-L`：允许跟随重定向。

```bash
bash <(...)
```

把下载到的 bootstrap 脚本交给 Bash 执行。

默认会解析 GitHub 最新正式 Release，然后安装对应正式版本。

---

# 3. 指定版本安装 Panel

```bash
XNAT_VERSION=1.1.0 \
bash <(curl -fsSL https://raw.githubusercontent.com/kkx999/xnat/main/scripts/bootstrap-panel.sh)
```

```text
XNAT_VERSION=1.1.0
```

表示固定安装 Release `v1.1.0`，不自动跟随以后发布的新版本。

---

# 4. Host 一键安装

```bash
apt-get update && apt-get install -y curl ca-certificates && \
bash <(curl -fsSL https://raw.githubusercontent.com/kkx999/xnat/main/scripts/bootstrap-host.sh)
```

Host 不需要把 `PANEL_IP`、`NATPOOL_GB`、NAT 端口范围全部写进一条复杂命令。

普通用户直接运行上面这一条，然后按提示操作。

---

# 5. Host 安装第 1 步：Panel 公网 IPv4

安装器会显示类似：

```text
XNAT Host 安装 · 1/2

请输入 XNAT Panel Server 的【真实公网 IPv4】。

这个 IP 是运行 XNAT Panel 的 VPS/服务器公网地址，
用于限制 Host Agent 29443/TCP：
只有这台 Panel 才能访问管理接口。

请不要填写：
- Panel 域名
- Cloudflare IP
- 当前 Host 自己的 IP

Panel 公网 IPv4:
```

例如 Panel VPS 公网 IP：

```text
165.154.240.243
```

就输入：

```text
165.154.240.243
```

## 为什么一定要这个 IP

Host Agent 默认管理端口：

```text
29443/TCP
```

Agent 能执行：

- 创建 VPS
- 删除 VPS
- 重装 VPS
- 改 root 密码
- 修改 CPU / 内存 / 磁盘
- 管理 NAT 端口

因此普通用户没有访问 `29443` 的理由。

XNAT 会自动配置本机防火墙：

```text
Panel 公网 IP → 29443 ✅
127.0.0.1     → 29443 ✅
其他来源       → 29443 ❌
```

这就是为什么必须输入 **Panel 服务器真实公网 IPv4**。

---

# 6. Host 安装第 2 步：natpool

安装器会检测 Host 磁盘，例如：

```text
当前根分区总容量：约 80 GB
当前可用空间：    约 72 GB
建议给系统保留：  至少 12 GB
推荐 natpool：     60 GiB

请输入 natpool 大小 [60]:
```

## natpool 是什么

`natpool` 是 XNAT 为 Incus 创建的 **LVM Thin 存储池**。

用户购买的：

```text
2 GB VPS
4 GB VPS
8 GB VPS
```

这些 VPS 系统盘都从 `natpool` 中分配。

例如输入：

```text
60
```

表示计划给用户 VPS 磁盘使用约：

```text
60 GiB
```

的 Thin Pool。

## 为什么不能把整块磁盘全部给 natpool

Host 自己还需要空间存放：

- Debian 系统
- 软件包
- 日志
- 临时文件
- XNAT Agent
- 系统更新

因此脚本会自动给系统预留安全空间，并给出推荐值。

直接按回车：

```text
[60]:
```

就采用推荐的 `60 GiB`。

---

# 7. 为什么 Host 安装时不再设置 NAT 端口池

以前可能会写：

```text
PORT_START=30000
PORT_END=39999
```

新版正式流程不再这样做。

原因是 **NAT 用户端口池属于节点业务配置，不属于 Host 基础安装配置。**

Host 安装只负责：

```text
Incus
LVM Thin
natpool
incusbr0
Host Agent
Agent TLS
Agent Token
29443 防火墙
systemd
2 GiB 磁盘验证
```

NAT 端口范围在 Host 与 Panel 连接成功之后，再由管理员在 Panel 后台规划。

---

# 8. Host 安装完成

安装结束会显示：

```text
Agent URL
Agent Token
Public IP
Storage
Bridge
Panel allow
```

同时保存在：

```bash
cat /root/xnat-host-agent-credentials.txt
```

### 命令解释

```bash
cat
```

输出文本文件内容。

这个凭据文件包含 Agent Token，因此不要公开截图，也不要提交到 GitHub。

---

# 9. Panel 后台添加 Host

登录 Panel：

```text
后台
→ 宿主机节点
→ 添加宿主机节点
```

填写：

### 节点名称

例如：

```text
Tokyo01
```

只是后台显示名称。

### 区域

例如：

```text
Tokyo
```

### Host Agent URL

例如：

```text
https://161.248.63.61:29443
```

这是 Host 安装完成后输出的 Agent URL。

### Agent Token

填写：

```text
/root/xnat-host-agent-credentials.txt
```

中的 Agent Token。

### 宿主机公网 IP

通常可以留空。

XNAT 在连接 Agent 成功后会读取 Agent 上报的真实公网 IPv4。

只有自动识别不正确时才手动填写。

### TLS 校验

Host Agent 默认使用自签 TLS，所以默认：

```text
自签证书 / 不校验
```

即可。

点击：

```text
添加并检测节点
```

正常应该检测到：

```text
ONLINE
Agent Version
Agent API
CPU
Memory
Storage
VPS Count
```

---

# 10. 在 Panel 后台配置 NAT 端口池

节点连接成功后，展开：

```text
NAT 端口池
```

例如输入：

```text
起始端口：30000
结束端口：39999
```

然后点击：

```text
保存并同步到 Agent
```

XNAT 会同时完成：

```text
Panel 检查范围
↓
检查是否包含 Agent 管理端口
↓
检查是否会排除已有 VPS 正在使用的端口
↓
通过 Agent API 同步到 Host
↓
Agent 再做第二次安全检查
↓
Agent 保存本机节点配置
↓
Panel 保存数据库
```

成功后节点会显示：

```text
30000-39999
总计 10000
已使用 0
剩余 10000
```

---

# 11. 为什么 Panel 和 Agent 都保存 NAT 端口范围

这是双层安全校验。

假设节点设置：

```text
30000-39999
```

Panel 分配：

```text
31000
```

Panel 判断：

```text
31000 ∈ 30000-39999 ✅
```

Agent 收到以后也会判断：

```text
31000 ∈ 30000-39999 ✅
```

才真正执行 Incus proxy。

如果异常请求要求 Agent 使用：

```text
22
```

或：

```text
45000
```

Agent 会直接拒绝。

---

# 12. 修改 NAT 端口范围

例如原来：

```text
30000-39999
```

希望改成：

```text
30000-49999
```

直接在 Panel 后台修改并保存。

不需要：

- SSH 登录 Host
- 修改 `.env`
- 重装 Agent
- 重装 Incus

## 缩小范围时的保护

如果已有 VPS 正在使用：

```text
35000
```

管理员试图把端口池改成：

```text
40000-49999
```

Panel 会拒绝。

Agent 也会检查 Incus 当前 proxy 设备，再拒绝一次。

这样不会因为修改端口池导致现有 VPS NAT 失效。

---

# 13. 云厂商安全组

Panel 后台配置：

```text
30000-39999
```

只代表 XNAT 允许使用这个业务范围。

如果 Host 服务商有安全组 / 云防火墙，还需要让服务商允许相同范围。

例如：

```text
TCP 30000-39999
UDP 30000-39999
```

如果不需要 UDP NAT，可以不开放 UDP。

XNAT 不会自动调用不同 VPS 商家的安全组 API。

---

# 14. 高级自动化安装

普通用户建议使用交互式：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/kkx999/xnat/main/scripts/bootstrap-host.sh)
```

自动化部署仍支持提前传参数：

```bash
PANEL_IP=165.154.240.243 \
NATPOOL_GB=60 \
bash <(curl -fsSL https://raw.githubusercontent.com/kkx999/xnat/main/scripts/bootstrap-host.sh)
```

### `PANEL_IP`

Panel Server 的真实公网 IPv4。

提前提供后安装器不会再询问。

### `NATPOOL_GB`

希望创建的 `natpool` 容量，单位 GiB。

提前提供后安装器不会再询问。

注意：

> NAT 用户端口池仍然不通过 Host 环境变量设置，应在节点连接 Panel 后从后台配置。

---

# 15. 指定 v1.1.0 安装 Host

```bash
XNAT_VERSION=1.1.0 \
bash <(curl -fsSL https://raw.githubusercontent.com/kkx999/xnat/main/scripts/bootstrap-host.sh)
```

`XNAT_VERSION=1.1.0`：

固定下载 Release tag：

```text
v1.1.0
```

然后仍然会进入正常的交互式 Panel IP + natpool 流程。

---

# 16. Panel 域名 / HTTPS / Cloudflare

Panel 推荐架构：

```text
用户
 ↓ HTTPS
Cloudflare
 ↓ HTTPS
Nginx :443
 ↓
127.0.0.1:8000
 ↓
XNAT Panel
```

查看：

```bash
xnat domain status
```

设置 / 更换：

```bash
xnat domain set nat.example.com
```

证书续期：

```bash
xnat domain renew
```

Cloudflare 推荐：

```text
SSL/TLS → Full (strict)
```

---

# 17. xnat 管理命令

直接：

```bash
xnat
```

自动识别 Panel 或 Host。

常用：

```bash
xnat status
```

查看状态。

```bash
xnat logs
```

实时日志。

```bash
xnat doctor
```

系统诊断。

```bash
xnat update check
```

检查当前组件更新。

```bash
xnat update
```

更新当前组件。

Panel 不会因为自身升级就强迫所有 Host Agent 同时升级。

---

# 18. Host Agent Token

轮换：

```bash
xnat token rotate
```

会：

```text
备份旧配置
生成新 Token
写入 Agent
重启 Agent
健康检查
```

然后必须在 Panel 节点设置里更新新 Token。

---

# 19. Panel IP 变化

如果以后换了 Panel Server，新的真实公网 IP 是：

```text
1.2.3.4
```

在 Host：

```bash
xnat panel-ip 1.2.3.4
```

作用：

- 更新 Agent 配置。
- 更新 Host `29443` 防火墙允许来源。

---

# 20. 数据库备份与恢复

Panel 后台：

```text
运维
→ 数据库备份
```

支持：

- 立即创建 SQLite 备份
- 下载已有备份
- 服务器已有备份直接恢复
- 从本地上传 `.db` 后校验并恢复

上传的数据库在恢复前会检查：

```text
SQLite 3 文件头
PRAGMA integrity_check
PRAGMA foreign_key_check
当前 XNAT 所需的数据表
当前 XNAT 所需的字段
```

校验通过后仍不会立即覆盖数据库，需要输入：

```text
RESTORE
```

再次确认。

恢复时 XNAT 会先自动创建：

```text
panel-pre-restore-YYYYMMDD-HHMMSS.db
```

作为恢复前快照。

如果恢复过程或恢复后的 SQLite 校验失败，会自动尝试回滚到恢复前数据库。

后台上传恢复只替换：

```text
panel.db
```

不会覆盖：

```text
.env
APP_SECRET
域名配置
Telegram / SMTP 密钥
服务器本机配置
```

命令行同样可以管理备份：

```bash
xnat backup
```

创建备份。

```bash
xnat backup list
```

查看备份。

```bash
xnat restore 备份文件名.db
```

恢复指定备份。

Panel 数据库：

```text
/opt/xnat/panel/data/panel.db
```

Panel 配置：

```text
/opt/xnat/panel/.env
```

数据库与 `.env` 都应保留异机备份。

---

# 21. 故障排查

Panel：

```bash
xnat doctor
```

Host：

```bash
xnat doctor
```

Panel 本机 health：

```bash
curl -s http://127.0.0.1:8000/health
```

Agent 本机 health：

```bash
curl -k https://127.0.0.1:29443/health
```

从 Panel 测 Host：

```bash
curl -k --connect-timeout 5 https://HOST_IP:29443/health
```

其他非 Panel 机器访问 `29443` 应该失败或超时。

---

# 22. Incus 常用检查

查看实例：

```bash
incus list
```

查看 Storage：

```bash
incus storage list
```

查看 natpool：

```bash
incus storage info natpool
```

查看网络：

```bash
incus network list
```

进入实例：

```bash
incus exec 实例名 -- bash
```

查看实例磁盘：

```bash
df -h /
```

---

# 23. 敏感信息

不要公开：

```text
.env
APP_SECRET
管理员密码
Agent Token
panel.db
Telegram Bot Token
SMTP 密码
API Key
TLS 私钥
```

Host Agent `29443` 不是用户端口。

Panel `8000` 是内部端口。

SSH `22` 不由 XNAT 自动限制，避免管理员因为公网 IP 变化被锁在服务器外。

---

# 24. 部署验收

正式使用前建议确认：

```text
[ ] Panel HTTPS 正常
[ ] Cloudflare Full (strict)
[ ] Panel 8000 只监听本机
[ ] Host Agent 29443 只允许 Panel
[ ] Host natpool 为 LVM Thin
[ ] 2 GiB 安装测试通过
[ ] Panel 能检测 Host ONLINE
[ ] NAT 端口池已从 Panel 后台保存并同步
[ ] 创建测试 VPS 成功
[ ] VPS SSH NAT 正常
[ ] VPS 磁盘配额正确
[ ] CPU / 内存限制正确
[ ] 流量统计正常
[ ] VPS 删除后 NAT / Incus 清理正常
```

# 25. 管理员调整 VPS 流量额度

后台路径：

```text
服务器
→ 对应 VPS
→ 调整流量
```

管理员可以直接修改当前服务的基础流量额度。

例如当前：

```text
已用：120 GB
额度：500 GB
```

可以修改为：

```text
1000 GB
```

也可以填写：

```text
0
```

表示该 VPS 不限制月流量。

保存以后 Panel 会立即重新计算流量状态，并同步当前带宽策略。

如果新的额度低于或等于当前已使用量，例如：

```text
已用：120 GB
新额度：100 GB
```

后台不会直接保存，必须勾选：

```text
如果新额度低于当前已用流量，确认立即执行超额限速策略
```

确认以后保存，该 VPS 会立即进入超额流量策略，默认限制为：

```text
1 Mbps
```

### 重置已用流量

调整额度时可以同时勾选：

```text
同时把当前 30 天周期已用流量重置为 0
```

也可以单独点击：

```text
仅重置流量周期
```

重置以后：

```text
RX / TX 累计重新开始
临时流量奖励清零
自动限速状态重新计算
新的 30 天流量周期从当前时间开始
```

---

# 26. 管理员自定义 VPS 到期时间

后台不再使用固定的：

```text
+30 天
```

进入：

```text
服务器
→ 对应 VPS
→ 调整到期
```

有两种方式。

## 26.1 按天续期

输入：

```text
增加天数：45
```

XNAT 会从当前有效到期时间继续增加 45 天。

如果服务器已经过期，则从当前时间开始增加。

允许范围：

```text
1 - 3650 天
```

## 26.2 直接指定到期日期

可以直接选择：

```text
2026-12-31 23:59
```

Panel 会按照 `APP_TIMEZONE` 转换后保存为 UTC。

如果管理员把到期时间设置到当前时间以前，必须额外输入：

```text
EXPIRE NOW
```

这是防止误操作的二次确认。

确认后，如果 VPS 当前正在运行，XNAT 会立即尝试停止该 VPS。

所有到期时间修改都会写入审计日志，包括：

```text
修改前时间
修改后时间
调整方式
增加天数
是否触发停机
```

---

# 27. 磁盘扩容与缩容保护

管理员资源调整支持：

```text
CPU：可以增加，也可以减少
内存：可以增加，也可以减少
磁盘：只允许增加
```

例如当前磁盘：

```text
6 GB
```

尝试改为：

```text
4 GB
```

前端会明确提示：

```text
磁盘仅支持扩容，当前容量为 6 GB。
请输入大于或等于 6 GB 的容量。
```

同时 Panel 后端仍会再次校验，所以不能通过绕过浏览器验证执行磁盘缩容。

---

# 28. USDT 充值独立设置

USDT 配置已经从“站点设置”独立出来：

```text
后台 → 业务 → USDT 充值
```

每个网络可以选择两种充值模式：

```text
自动充值
→ 用户创建订单
→ XNAT 查询链上交易
→ 达到校验条件后自动增加余额

人工充值
→ 用户创建订单
→ 用户转入精确 USDT 数量
→ 用户提交 TxHash
→ 管理员核对后人工确认入账
```

TRON 人工模式只需要：

```text
TRON 收款地址
```

TRON 自动模式还需要：

```text
TronGrid API Key
```

TronGrid API Key 只用于读取 TRON 主网交易，不是钱包私钥，也不具备转账权限。

Polygon 人工模式只需要 Polygon 收款地址；自动模式还需要可用的 Polygon RPC。

## Token 合约地址

TRON USDT 与 Polygon USDT0 合约地址会继续显示在后台，但属于 **系统只读字段**。

它们用于判断链上转账是否确实来自 XNAT 支持的官方 USDT Token。管理员不需要填写，也不能修改。后端始终使用 XNAT 内置常量，不接受浏览器提交的自定义合约地址。

## 通道测试

保存配置后可以使用：

```text
测试 TRON 配置
测试 Polygon 配置
```

人工模式只校验收款地址；自动模式会额外检查 TronGrid API Key 或 Polygon RPC。测试不会发送 USDT，也不会操作钱包。

> XNAT 永远不需要钱包私钥、助记词、Keystore 私钥或钱包密码。


# 29. 通知服务独立设置

通知配置已经从“站点设置”独立出来。

后台：

```text
系统
→ 通知服务
```

## 29.1 SMTP

支持配置：

```text
SMTP Host
SMTP Port
用户名
SMTP 密码
发件地址
STARTTLS
```

SMTP 密码使用 `APP_SECRET` 加密保存。

保存以后，可以在同一页面输入一个测试邮箱并点击：

```text
发送测试邮件
```

测试成功会收到：

```text
[XNAT] SMTP 测试
```

## 29.2 Telegram

配置全站共用：

```text
Telegram Bot Token
```

用户自己的 Chat ID 仍然由每个用户在账户中心设置。

管理员可以在通知服务页面临时输入 Chat ID，然后点击：

```text
发送 Telegram 测试
```

测试 Chat ID 只用于本次测试，不会自动覆盖用户资料。

## 29.3 全站通知规则

通知服务页面可以分别控制 Email / Telegram 是否发送以下事件：

```text
VPS / 开通 / 资源
流量告警
到期 / 续费
充值 / 支付
工单
安全事件
系统通知
```

规则是全站开关。

最终是否发送还会同时检查用户自己的通知偏好：

```text
全站规则开启
+
用户启用该通知渠道
+
该渠道配置完整
=
发送
```

## 29.4 最近发送记录

通知服务页面下方会显示最近通知记录：

```text
用户
类型
标题
Email 状态
Telegram 状态
时间
```

常见状态包括：

```text
pending       等待发送
sent          已发送
disabled      用户关闭该渠道
rule_off      管理员全站规则关闭
unconfigured  渠道尚未完整配置
failed:...    发送失败及简短错误
```

---

# 30. 站点设置现在负责什么

拆分以后，“站点设置”主要保留：

```text
首页内容
开放注册
PUBLIC_BASE_URL
域名 / HTTPS 状态
登录与管理员安全
NAT 全局安全策略
```

其中：

```text
USDT 自动充值 → 独立页面
SMTP / Telegram → 独立通知服务页面
站点公告 → 独立“公告管理”页面；用户右上角可查看历史公告
```

这样后续运维时不需要在一个很长的设置页面里寻找业务配置。

---




## USDT 无 TxHash 强制补单

当链上 API/RPC 不可用、或管理员已经通过其他可信凭证人工确认到账时，可以在充值订单的“手动补单”面板中展开“高级危险操作”。

无 TxHash 补单：

- 不要求交易哈希；
- 不执行链上自动校验；
- 不伪造 TxHash；
- 不写入 `chain_transactions`；
- 始终按订单创建时的人民币金额入账；
- 同一订单只允许入账一次；
- 必须填写补单原因；
- 必须输入 `FORCE CREDIT`；
- 余额流水和审计日志会明确标记为“无链上凭证人工补单”。

该功能仅用于最后兜底，不建议替代正常的 TxHash 校验补单。

---

# 31. v1.0.2 → v1.1.0 原地升级

正式兼容基线是 **XNAT Panel v1.0.2 final**。升级前不需要删除旧 Panel，也不要手工修改 SQLite。

正式 v1.1.0 Tag 发布后，v1.0.2 Panel 首选直接执行：

```bash
xnat update 1.1.0
```

v1.0.2 的更新命令会下载 v1.1.0 Tag，并调用新版本自身的 `scripts/upgrade-panel.sh` 完成备份、数据库迁移、健康检查与失败回滚。

如果采用手动源码包升级，把完整的 v1.1.0 源码包上传到 Panel，例如解压到：

```text
/root/xnat-v1.1.0
```

然后执行：

```bash
cd /root/xnat-v1.1.0
bash scripts/upgrade-panel-from-v1.0.2.sh
```

专用脚本会先确认现有 Panel 版本是 `1.0.2`，然后调用通用升级器。升级器会执行以下安全流程：

```text
SQLite quick_check
↓
备份 panel.db / .env / 旧代码 / systemd / xnat 命令
↓
更新 Panel 代码
↓
安装/更新 Python 依赖
↓
执行 additive schema migration
↓
确认 v1.1.0 新字段全部存在
↓
重建 Panel / maintenance systemd 单元
↓
启动 Panel
↓
/health 必须返回 v1.1.0
↓
再次执行 SQLite quick_check
```

升级不会删除 `data/`，因此原有用户、余额、订单、VPS、Host、套餐、充值、通知和备份目录都会保留。`.env` 也不会被覆盖；如果 v1.0.2 使用了自定义 `PANEL_BIND_HOST` 或 `PANEL_PORT`，升级会继续使用原值。

v1.1.0 的新数据库字段全部采用新增字段迁移，不重建旧表。到期自动永久删除在升级后默认仍为关闭状态。

如果更新流程出现错误，脚本会尝试恢复升级前的数据库、`.env`、代码、systemd 单元和管理脚本，然后重新启动旧 Panel。升级前快照统一保存在：

```text
/root/xnat-backups/
```

> Panel v1.1.0 仍支持 Agent API v1；现有 Host Agent v1.0.0 不需要因为本次 Panel 升级而重装。

---

# 32. v1.1.0 运营可靠性

## 32.1 节点维护 / Drain

节点后台可以进入“维护模式”。维护中的节点不会接收新 VPS，但现有 VPS 不会自动停止，开关机、重启、重装与 NAT 管理仍可按实例状态正常使用。退出维护后恢复调度。

## 32.2 调度资源水位

每个 Host 可以分别设置 CPU、内存和 natpool 存储调度阈值。默认均为 `90%`；设置为 `0` 表示关闭该项水位限制。达到阈值后只阻止新调度，不会主动停止已有 VPS。

Panel 还会在分配套餐时检查预计内存和逻辑磁盘分配，避免新实例把节点推过配置上限。

## 32.3 运维异常通知

系统通知类型会覆盖：Host Agent 离线 / 恢复、natpool 存储达到水位、NAT 公网端口池余量不足、后台任务达到最大重试次数、定时数据库备份失败。

通知会进入管理员账户的通知记录；Email / Telegram 是否实际发送仍受“通知服务 → 系统通知”全站规则和管理员自己的渠道偏好控制。

## 32.4 VPS 到期生命周期

默认策略：

```text
提前提醒：7 / 3 / 1 天
宽限期：0 天
到期后自动停机：开启
续费后自动恢复：开启
自动永久删除：关闭
```

自动删除是危险能力，**v1.1.0 全新安装和升级后都默认关闭**。只有管理员主动开启后，系统才会在“宽限期 + 删除等待天数”结束后排队删除；删除前还会按配置发送最终提醒。

续费会取消尚未开始执行的“生命周期自动删除”任务，并可以恢复由到期策略自动停止的 VPS。手动删除任务不会被续费静默覆盖。

## 32.5 独立流量周期

每台 VPS 可以使用：

```text
rolling30  每 30 天滚动重置
monthly    每月固定 1-28 日重置
```

修改 VPS 到期时间不会改变流量周期。修改某台 VPS 的周期模式时，后台会要求确认并立即开始一个新的流量周期。

## 32.6 用户控制台交互

用户端增加轻量 Hover、按钮处理中状态、复制结果反馈、Toast、折叠过渡和流量条动画。动画仅用于反馈状态，不使用背景粒子、持续发光或大面积霓虹效果；系统开启“减少动态效果”时会自动降低动画。

## 32.7 v1.0.x 数据库升级

v1.1.0 采用仅新增字段的迁移方式。Panel 启动时会自动检测并补齐 Host 调度字段、流量周期字段和到期生命周期标记。恢复 v1.0.x 的 SQLite 备份后，也会自动执行同一迁移。


---

<div align="center">

## XNAT

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**

</div>
