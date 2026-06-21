question:
block-proxy 是怎样依赖 anyproxy 的




answer:
结论：block-proxy 不是依赖官方 anyproxy 包，而是依赖私有/自维护 fork 包 @bachi/anyproxy。这个
  fork 的仓库就是 /Users/hfy/jayli/anyproxy，包名也写成 @bachi/anyproxy，描述里注明基于 AnyProxy
  4.1.3。

  核心依赖关系是三层：

  1. 包依赖
     /Users/hfy/jayli/block-proxy/package.json:9 声明：

     "@bachi/anyproxy": "^0.1.8"
      的包名是 @bachi/anyproxy，main 是 proxy.js。
     /Users/hfy/jayli/anyproxy/package.json:2 的包名是 @bachi/anyproxy，main 是 proxy.js。

  2. 运行时直接嵌入 AnyProxy 代理引擎
     /Users/hfy/jayli/block-proxy/proxy/proxy.js:2 直接：

     const AnyProxy = require('@bachi/anyproxy');

     启动时先用 AnyProxy.utils.certMgr 管理根证书，然后用 new AnyProxy.ProxyServer(options) 创建
     HTTP/HTTPS MITM 代理实例，见 /Users/hfy/jayli/block-proxy/proxy/proxy.js:511。

  3. block-proxy 把自己的业务逻辑作为 AnyProxy rule 注入
     getAnyProxyOptions() 返回 AnyProxy 配置，里面的 rule 是真正的集成边界，见 /Users/hfy/jayli/
     block-proxy/proxy/proxy.js:931。它注入了：
      - responseRules：告诉 AnyProxy 哪些响应需要收集完整 body，供响应重写使用。
      - beforeDealHttpsRequest：决定某个 HTTPS CONNECT 是否走 MITM 解密，见 /Users/hfy/jayli/
        block-proxy/proxy/proxy.js:1033。

      - beforeSendRequest：做认证、设备/IP/MAC 规则、域名/URL 拦截、本地管理页响应、请求重写，
        见 /Users/hfy/jayli/block-proxy/proxy/proxy.js:1109。

      - beforeSendResponse：做响应重写，见 /Users/hfy/jayli/block-proxy/proxy/proxy.js:1251。
      - onError / onConnectError：兜底处理代理请求错误。

  anyproxy fork 也为 block-proxy 做了定制配合，不只是普通第三方库使用。例如：

  - /Users/hfy/jayli/anyproxy/proxy.js:356 导出 ProxyServer 和 utils.certMgr，正好被 block-proxy
    使用。

  - /Users/hfy/jayli/anyproxy/lib/requestHandler.js:74 有 responseRules 匹配逻辑。
  - /Users/hfy/jayli/anyproxy/lib/requestHandler.js:372 会把原始客户端 IP 填到 req.sourceIp，
    block-proxy 后面用它做设备级规则。

  - /Users/hfy/jayli/anyproxy/lib/requestHandler.js:539 调用 userRule.beforeSendRequest(...)。
  - /Users/hfy/jayli/anyproxy/lib/requestHandler.js:553 根据 responseRules 调整响应收集阈值。
  - /Users/hfy/jayli/anyproxy/lib/requestHandler.js:589 调用 userRule.beforeSendResponse(...)。
  - /Users/hfy/jayli/anyproxy/lib/requestHandler.js:681 在 CONNECT 阶段调用
    userRule.beforeDealHttpsRequest(...) 来决定 MITM 还是透明隧道。

  还有一个旁路依赖：block-proxy 自己实现了 SOCKS5/TLS 服务，但 TCP CONNECT 最终会转发到本机
  AnyProxy HTTP 代理端口。见 /Users/hfy/jayli/block-proxy/socks5/server.js:123，它连接 127.0.0.1:
  ${proxy_port} 并发送 HTTP CONNECT，所以 SOCKS5 入口也复用了 AnyProxy 作为下游代理核心。

  需要注意的版本状态：当前 block-proxy/node_modules/@bachi/anyproxy 是 pnpm
  安装目录下的包副本，不是直接 symlink 到 /Users/hfy/jayli/anyproxy；但我比对了关键文件 proxy.js
  和 lib/requestHandler.js，内容一致。package-lock.json 里还有旧的官方 anyproxy@4.1.3
  痕迹，但它的版本号和依赖都落后于当前
  package.json/pnpm-lock.yaml，不应作为当前真实依赖关系判断。

以上信息来自于项目：
- /Users/hfy/jayli/anyproxy
- /Users/hfy/jayli/block-proxy
