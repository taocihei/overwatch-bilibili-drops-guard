# 北京常驻赞助支付服务

这是面向 Ubuntu + systemd 的独立 YunGouOS Native Pay 服务。它只把 `type=1`
返回的 `weixin://` 内容交给桌面端，由桌面端本地绘制二维码，不代理二维码图片。

## 低延迟路径

- SQLite 使用 WAL、`synchronous=NORMAL` 和 10 秒 busy timeout。
- `5 / 10 / 20 / 50 / 100` 元分别维持未分配订单池，默认每档 3 张。
- systemd 启动前同步保证每档至少 1 张，后台线程持续补池。
- 领取在 `BEGIN IMMEDIATE` 事务中完成；一张池订单只会分配给一个安装实例。
- `(install_id, checkout_intent_id, amount_cents)` 唯一索引保证重试和并发幂等。
- 批量预取最多 5 档，按请求顺序返回。池命中返回 HTTP 200；只要有一档现场建单则返回 201。
- 支付回调为主确认，状态查询以全局 11 秒槽位低频向 YunGouOS 补偿核对。

池命中只经过一次本机 HTTP 和一次 SQLite 写事务，不调用 YunGouOS。订单一经分配
就不会被其他安装实例复用，因此不会出现不同用户共享付款结果。

## API

### `POST /api/sponsor/orders`

单笔：

```json
{
  "amount": "5.00",
  "install_id": "install-12345678",
  "checkout_intent_id": "checkout-12345678",
  "app_version": "0.5.23",
  "provider": "yungouos"
}
```

批量把 `amount` 换成 `"amounts": ["5", "10", "20", "50", "100"]`。
成功订单含：

```json
{
  "order_id": "32位随机十六进制ID",
  "amount": "5.00",
  "qr_url": "",
  "fallback_qr_url": "",
  "qr_content": "weixin://wxpay/...",
  "expires_at": "2026-08-05T12:00:00Z",
  "expires_in_seconds": 6500,
  "state_version": 2,
  "allocation": "pool",
  "status_token": "可选HMAC凭证"
}
```

### `GET /api/sponsor/orders/{order_id}`

兼容当前桌面客户端直接按随机订单 ID 查询。新客户端可额外使用
`X-Status-Token` 请求头或 `?token=`；传入错误 token 会返回 403。

### `POST /api/sponsor/callback`

接收 YunGouOS 表单回调，校验 `code/orderNo/outTradeNo/payNo/money/mchId`
六个字段的 MD5 签名，并再次核对本地订单金额。成功固定返回纯文本 `SUCCESS`。

### `GET /api/health`

返回版本和五档可领取池数量，可供 systemd/Nginx 健康检查。

## 本地测试

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

测试全部使用临时 SQLite 和假支付端，不需要真实凭证，也不会创建真实订单。

## Ubuntu 部署

1. 将本目录传到北京服务器，例如 `/root/beijing-sponsor-service-src`。
2. 首次执行 `sudo bash scripts/deploy.sh` 会创建 `/etc/sponsor-service.env` 模板并停止。
3. 编辑 `/etc/sponsor-service.env`，填入真实商户号、支付密钥和已登记的 HTTPS 回调。
4. 再次执行 `sudo bash scripts/deploy.sh`。脚本会建 venv、跑全测、切换 release、
   预填订单池、启动服务并检查 `/api/health`；失败时自动回滚。
5. 将 `deploy/nginx-sponsor.conf` 中的示例域名和证书路径替换后安装，运行
   `sudo nginx -t && sudo systemctl reload nginx`。
6. YunGouOS 后台回调地址与 `SPONSOR_CALLBACK_URL` 必须完全一致。

回滚：

```bash
sudo bash /opt/beijing-sponsor-service/current/scripts/rollback.sh
```

数据库固定放在 `/var/lib/beijing-sponsor-service/orders.sqlite3`，release 切换和回滚
不会覆盖订单。真实凭证只放 `/etc/sponsor-service.env`，不会进入代码或发布包。
