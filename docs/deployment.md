# GitHub Actions 部署

PR 和推送到 `main` 时运行 pytest 并构建 Docker 镜像。测试通过后，可以在 Actions → CI and deploy → Run workflow 选择 `main` 手动部署。首次成功后，在**仓库级** Actions Variables 中设置 `DEPLOY_ENABLED=true`，后续推送到 `main` 会自动部署。

## 服务器

- 部署账号需要能通过 SSH 登录，能够运行 Git、Docker Compose（支持 `up --wait`）和 curl。GitHub 托管运行器需要能访问 SSH 端口，服务器需要能访问 GitHub 和镜像及依赖仓库。
- 已有站点使用当前 Compose 目录，保留原目录名、Compose 项目名、`.env` 和 SQLite 命名卷。已有目录需要是 `main` 分支的干净 Git 工作副本。首次部署前备份现有 SQLite。
- 新服务器先用部署账号将 `https://github.com/Suvalor/chuhai-news.git` 克隆到部署目录，按 [README](../README.md#docker-部署) 在服务端准备 `.env`，并配置反代和 HTTPS。反代目标是 `127.0.0.1:8090`。

## GitHub 配置

在 Settings → Environments → `production` 中，将允许部署的分支限定为 `main`，并保存以下值。

| 类型 | 名称 | 内容 |
| --- | --- | --- |
| Variable | `DEPLOY_HOST` | 服务器 IP 或 SSH 域名 |
| Variable | `DEPLOY_USER` | SSH 部署账号 |
| Variable | `DEPLOY_PORT` | SSH 端口，可省略，默认 22 |
| Variable | `DEPLOY_PATH` | Compose 项目的服务器绝对路径 |
| Secret | `DEPLOY_SSH_KEY` | 专用于部署的无口令 SSH 私钥，对应公钥需在部署账号的 `authorized_keys` 中 |
| Secret | `DEPLOY_KNOWN_HOSTS` | 核验过的服务器 SSH 主机公钥记录。22 端口用 `host key-type public-key`，其他端口用 `[host]:port key-type public-key` |

通过服务器控制台或已信任的 SSH 会话核对主机公钥。私钥直接录入 GitHub Secret。`ADMIN_PASSWORD`、`WEBHOOK_SECRET` 和其他应用配置留在服务器 `.env` 内。

部署会更新到本次测试通过的 `main` 提交，通过 Docker 健康检查后访问服务器本机首页。首次运行后，再从外部访问站点 HTTPS 地址，确认反代正常。部署失败会在 Actions 标红。需要回退应用时，在 GitHub revert 对应提交并重新部署。若改动涉及 SQLite 结构，先核对数据库兼容性及可用备份。
