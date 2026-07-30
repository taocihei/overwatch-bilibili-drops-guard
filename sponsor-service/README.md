# 赞助支付服务

这是“守望先锋 B站直播挂宝”桌面客户端使用的 YunGouOS 扫码赞助后端。

## 接口

- `GET /api/health`：健康检查。
- `POST /api/sponsor/orders`：创建 ¥3、¥6 或 ¥10 的赞助订单。
- `GET /api/sponsor/orders/{order_id}`：查询本服务记录的订单状态。
- `GET /api/sponsor/qr/{order_id}`：通过 HTTPS 返回支付二维码图片。
- `POST /api/sponsor/callback`：接收并验证 YunGouOS 支付回调。

## 安全设计

- 商户号和支付密钥只通过生产环境变量注入，不写入源码或客户端。
- 只有金额、商户号、订单号和商品描述参与 YunGouOS 下单签名。
- 回调必须同时通过签名、商户号、订单号和金额校验。
- 桌面客户端只查询本服务的 D1 订单状态，不轮询 YunGouOS 查询接口。
- 二维码代理只允许加载 YunGouOS HTTPS 图片。

## 本地运行

```powershell
npm install
npm run dev
```

本地完整支付测试需自行配置以下环境变量：

```text
YUNGOUOS_MCH_ID
YUNGOUOS_PAY_KEY
```

不要把真实凭据写入 `.env` 后提交。
