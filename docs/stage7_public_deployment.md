# Stage7公网部署门禁

## 唯一路线

选择Cloudflare Tunnel。当前学校/机构网络只确认了私网地址和一个出站观测IP，未证明拥有可入站公网地址，因此不使用路由器DMZ或随机端口。

`compose.public.yaml`固定`cloudflared 2026.7.2`及镜像digest，使用`--token-file /run/secrets/cloudflare_tunnel_token`，关闭容器内自动升级，只加入`tunnel_net`且不挂载Docker Socket。`edge`是`tunnel_net`中唯一应用服务，并通过独立`app_net`访问API。Cloudflare控制台的Published application必须且只能指向`http://edge:80`，最后一条规则必须是catch-all 404。官方说明见[Cloudflare Tunnel token-file参数](https://developers.cloudflare.com/tunnel/advanced/run-parameters/)和[凭据文件说明](https://developers.cloudflare.com/tunnel/advanced/local-management/local-tunnel-terms/)。

## 尚缺外部动作

1. 用户拥有的真实域名及Cloudflare账户。
2. 创建/轮换Tunnel Token并写入权限0600的secret文件。
3. 在Cloudflare配置唯一hostname到edge，启用外部HTTP→HTTPS策略。
4. 完成全部秘密轮换及旧凭据失效验证。
5. 提供异机/外部备份目标并完成PostgreSQL和MinIO恢复。
6. 确认共享主机维护窗口。
7. 从手机蜂窝网络或批准VPS完成DNS、证书、Cookie、CSRF、RBAC、429、SSE、上传下载和端口负向验收。

## 激活顺序

先运行Secrets权限、Compose安全、Caddy、依赖、秘密扫描和备份恢复门禁；再由用户配置域名/Tunnel；只启动本项目`edge`和`cloudflared`所需变更。外部HTTPS验证成功后才允许激活HSTS配置。不得启动第二套争用相同数据目录的Compose项目。

当前无正式域名、无Tunnel凭据、无外部验证，生产Compose和Tunnel均未启动。
