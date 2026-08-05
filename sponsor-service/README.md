# 赞助支付服务

这是“守望先锋 B站直播挂宝”桌面客户端使用的 YunGouOS 扫码赞助后端。

## 接口

- `GET /api/health`：健康检查。
- `POST /api/sponsor/orders`：创建 ¥1–9999 的赞助订单；客户端预设 ¥5、¥10、¥20、¥50 和 ¥100，也可输入其他金额。
- `GET /api/sponsor/orders/{order_id}`：查询本服务记录的订单状态。
- `GET /api/sponsor/qr/{order_id}`：通过 HTTPS 返回支付二维码图片。
- `POST /api/sponsor/callback`：接收并验证 YunGouOS 支付回调。

## 安全设计

- 商户号和支付密钥只通过生产环境变量注入，不写入源码或客户端。
- 只有金额、商户号、订单号和商品描述参与 YunGouOS 下单签名。
- 回调必须同时通过签名、商户号、订单号和金额校验。
- 桌面客户端只查询本服务的 D1 订单状态，不轮询 YunGouOS 查询接口。
- 二维码代理只允许加载 YunGouOS HTTPS 图片。
- 北京备案回调节点连续两次探测失败后，新订单自动切换到本站回调；熔断状态保存在 D1，不依赖单个 Worker 内存。
- 回调节点探测在响应后执行，熔断期间仍以逐订单 YunGouOS 签名查单作为支付结果补偿。

## 本地运行

```powershell
npm install
npm run dev
```

本地完整支付测试需自行配置以下环境变量：

```text
YUNGOUOS_MCH_ID
YUNGOUOS_PAY_KEY
SPONSOR_CALLBACK_URL=https://api.codeboc.cn/yungouos/callback
SPONSOR_CALLBACK_HEALTH_URL=https://api.codeboc.cn/yungouos/health
SPONSOR_POOL_REFILL_TOKEN=<至少 32 位随机值>
```

不要把真实凭据写入 `.env` 后提交。
