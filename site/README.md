# horosa.org 静态站（GitHub Pages）

四个页面：`index.html`（首页）、`privacy.html`（隐私政策）、`terms.html`（服务条款）、`support.html`（支持）。
纯静态，无脚本、无 Cookie。`CNAME` 指 `horosa.org`。

发布者 Hongxu Dong（与 OpenAI 个人实名一致）、支持邮箱 maxwelldhx@gmail.com 已填入，`plugin.json` 用同一组值；`scripts/verify_site.py` 见到占位符即拒。
生效日期写的是定稿日，实际上线晚于它时改成上线日。服务包源码放在公开仓库 `Horace-Maxwell/horosa-public-service`，由 `scripts/export_public_source.py` 导出；条款第 4 节和页脚都链到它，AGPL 第 13 条靠这个链接兑现。文本的事实（不存储、无日志、Cloudflare 转发、AGPL）与 `docs/PUBLIC_CONTAINER.md` 一致；
改任一方都要同步另一方。

发布：新建公开仓库（例如 `Horace-Maxwell/horosa.org`），把本目录内容放到仓库根，GitHub Pages 选 `main` 分支根目录；
Cloudflare DNS 加 `horosa.org` 的 A/AAAA 记录指向 GitHub Pages（或 `www` 的 CNAME），Pages 里开 Enforce HTTPS。
GitHub Pages 自己要求 `/privacy` 这类无后缀路径可访问：Pages 会把 `/privacy` 解析到 `privacy.html`。
