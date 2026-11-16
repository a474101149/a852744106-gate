# VPN Gate SSTP 节点自动优选（个人自用部署）

每 30 分钟由 GitHub Actions 自动抓取 VPN Gate 的 SSTP 节点，经自建的 Cloudflare 检测 Worker 逐个验证可用性，按国家分组、标注住宅/机房，生成给 edgetunnel 链式代理使用的清单（nodes.txt / hosts.txt / chains.txt）。含个人 UUID 的完整订阅 sub.txt 不发布到站点，只存为工作流的私有 artifact（需登录本账号下载）。

个人配置（检测 Worker 地址、edgetunnel 域名与 UUID、优选入口池、产物地址）全部通过仓库的 Secrets / Variables 注入，代码内不含任何个人参数。

基于开源项目：cmliu/edgetunnel、lsh8848/cm-Workers-CheckSocks5、jerylihub/gate（流水线脚本），数据源为 VPN Gate。
