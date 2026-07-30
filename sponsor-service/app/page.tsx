export default function Home() {
  return (
    <main className="shell">
      <section className="service-card" aria-labelledby="service-title">
        <div className="eyebrow">
          <span className="status-dot" aria-hidden="true" />
          服务运行中
        </div>

        <div className="brand-mark" aria-hidden="true">
          <span />
        </div>

        <p className="product-name">Bilibili Drops Helper</p>
        <h1 id="service-title">赞助支付服务</h1>
        <p className="lede">
          本页面仅承载桌面软件的扫码赞助接口，不展示广告，也不会主动弹窗打扰。
        </p>

        <div className="divider" />

        <ol className="flow" aria-label="赞助流程">
          <li>
            <span>01</span>
            <div>
              <strong>软件内自愿发起</strong>
              <p>仅在点击“赞助”后创建二维码。</p>
            </div>
          </li>
          <li>
            <span>02</span>
            <div>
              <strong>微信安全支付</strong>
              <p>付款由 YunGouOS 与微信支付完成。</p>
            </div>
          </li>
          <li>
            <span>03</span>
            <div>
              <strong>成功后显示交流群</strong>
              <p>支付确认后，群号只在软件内显示。</p>
            </div>
          </li>
        </ol>

        <footer>
          <span>v0.5.4</span>
          <span>不保存 Cookie · 不收集 B 站账号</span>
        </footer>
      </section>
    </main>
  );
}