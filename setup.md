# RDGen 部署与配置指南（本 fork 定制版）

> 适用仓库：`kgdwfn/rdgen`（上游 `bryangerlach/rdgen` 的定制 fork）。
> 本文替代上游原来的英文部署说明，**只描述正式部署方式**：与上游原版一致，所有参数直接写在 `docker-compose.yml` 的 `environment` 里；仓库内只保留占位值，真实凭据不要提交进公开仓库。本 fork 不使用 `.env` 插值。个人另存的定制 compose 属于本机资产，不在本文范围内。
>
> 配套文件：`android_signing/README.md`（安卓固定签名配置清单）。

本文包含：工作原理 → GitHub 侧准备 → 服务器侧部署 → **全部参数逐项说明** → 两个必填字段 → 安卓固定签名自动同步 → 更新升级 → 可选功能 → 产物说明 → 故障速查 → 安全提醒。

---

## 一、这套系统是怎么跑起来的

```
用户在网页填表单 → Django 把配置打成加密 zip（ZIP_PASSWORD）
  → 服务器用 PAT 调 GitHub API 派发 workflow_dispatch
  → GitHub runner 下载 zip、编译 RustDesk 客户端
  → runner 把产物回传到 GENURL 指定的服务器
  → 产物落进 exe/ 目录，网页给出下载链接
```

所以有两个方向的连通性都必须成立：**服务器能访问 `api.github.com`**，**GitHub runner 能访问 `GENURL`**（通常是公网域名 + HTTPS 反代）。任何一边不通，都会卡在"一直在运行中"。

数据库是镜像内的 SQLite（`db.sqlite3`），记录每次构建单。它同样不在挂载卷里，**重建容器会丢构建记录**；需要长期保留时请自行把 `db.sqlite3` 也挂出来。

---

## 二、GitHub 侧准备

### 2.1 开启 Actions

fork 之后先到 `https://github.com/用户名/rdgen/actions`，如果看到 **Enable Actions** 绿色按钮就点一下。没开的话派发会直接报 `Actions has been disabled for this repository`。

### 2.2 创建 fine-grained PAT

路径：头像 → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token。

- Repository access：`Only select repositories`，选中你的 rdgen 仓库（选"所有仓库"也行，但没必要）。
- 权限（仓库级权限，`Repositories` 那一栏的数字是权限条目数，不是仓库数量）：

| 权限项 | Access | 用途 | 不给会怎样 |
| --- | --- | --- | --- |
| `Actions` | Read and write | 派发 `workflow_dispatch`、查询 run 状态 | 派发失败 |
| `Workflows` | Read and write | 上游要求，读取/管理 workflow 文件 | 部分接口 403 |
| `Secrets` | **Read and write** | 安卓固定签名：容器自动读写 3 个 Actions Secret | 安卓同步报「读取仓库加密公钥失败（HTTP 403/404）」，其它平台不受影响 |
| `Contents` | Read | 常规读取；runner 自带 `GITHUB_TOKEN`，不需要写权限 | — |

> 这把 PAT 同时用于服务器环境变量 `GHBEARER`。它一旦拥有 `Secrets: Read and write`，就能改这个仓库的所有 Secret —— 如果只用一次，可在同步成功后把权限降回去；本 fork 的安卓签名由容器**每次派发都自动同步**，所以要常驻保留。

### 2.3 仓库 Actions Secret（GitHub 网页配置）

路径：仓库 → Settings → Secrets and variables → Actions → New repository secret。

| Secret 名 | 值 | 说明 |
| --- | --- | --- |
| `GENURL` | `https://你的域名` | 服务对外地址，runner 用它回传产物；也要与服务器 compose 里的 `GENURL` 一致 |
| `ZIP_PASSWORD` | 一个随机长串 | **必须与服务器 compose 里的 `ZIP_PASSWORD` 完全一致**，否则 runner 解不开加密配置包。生成：`python3 -c "import secrets; print(secrets.token_hex(100))"` |
| `SIGN_BASE_URL`、`SIGN_API_KEY` | 可选 | Windows 代码签名服务，见第八节 |
| 安卓 3 个 Secret | **不要手工配** | 由容器自动同步，见第六节；手工填了也会被覆盖 |

---

## 三、服务器侧部署（Docker Compose，正式方式）

### 3.1 首次部署

```bash
git clone https://github.com/你的用户名/rdgen.git
cd rdgen
vi docker-compose.yml          # 把 environment 里的占位值换成真实值，见第四节
docker compose up -d --build   # 第一次必须 --build
```

浏览器打开 `http://服务器IP:8000`。生产环境建议在前面挂 nginx / Caddy / Traefik 做 HTTPS 反代，`GENURL` 填反代后的公网地址。

> **配置方式与上游原版一致：直接改 `docker-compose.yml` 的 `environment`**，本 fork 不使用 `.env` 插值（那是 2026-10-23 曾短暂引入、现已撤回的做法）。
> 注意这个文件会被 `git pull` 覆盖，而且本仓库是公开仓库 —— **填好真实值的 `docker-compose.yml` 不要 `git add` / commit / push**。如需保留自己的定制配置，请在仓库目录之外自行备份一份，更新代码后再把值对回来（第七节）。
> `.gitignore` 里仍排除 `.env`，只是防止误把本地私有文件提交上去，与配置方式无关。

### 3.2 端口与数据卷

| 宿主机路径 | 容器路径 | 用途 |
| --- | --- | --- |
| `./exe` | `/opt/rdgen/exe` | 构建产物与批次元数据 |
| `./png` | `/opt/rdgen/png` | 上传的图标 |
| `./temp_zips` | `/opt/rdgen/temp_zips` | 加密配置包临时目录 |
| `./android_signing` | `/opt/rdgen/android_signing:ro` | 安卓正式签名密钥，**只读** |
| 端口 `8000` | `8000` | Web 服务 |

> **代码不在任何卷里**。`Dockerfile` 用的是 `COPY . .`，代码被打进镜像。所以 `restart` 或单纯的 `up -d` **不会带上新代码**，必须 `docker compose up -d --build`（或先 `docker build -t rdgen-rdgen .` 再 `up -d`）。
>
> `android_signing/` 里的真实密钥同时被 `.gitignore` 和 `.dockerignore` 排除，不会进 git、也不会被打进镜像。

---

## 四、参数逐项说明（重点）

### 4.1 配置入口只有一个

与上游原版一致，**所有参数都直接写在 `docker-compose.yml` 的 `environment` 里**：`docker compose` 启动时把它们注入容器，`settings.py` 用 `os.environ.get` 读取。没有 `.env` 插值，也没有第二套配置文件。

`environment` 里列出的 10 个变量分两组：4.2 是必填 / 直接影响构建的 5 个，4.3 是一般不用改的 5 个。此外代码还会读 3 个可选变量，它们没有在 compose 里预设，需要时在 `environment` 里自行加一行（4.4）。

> `settings.py` 里的兜底默认值（`ZIP_PASSWORD='insecure'`、`SH_SECRET='secret'` 等）**在 Docker 部署下用不上**：compose 已显式设置了这些名字，`os.environ.get(..., 默认值)` 拿到的是 compose 里的值。只有手工部署（第八节，不经过 compose）才会用到那些兜底值。

### 4.2 必填 / 影响构建的 5 个

| 变量 | 作用 | compose 里的占位值 | 怎么配 | 配错 / 不配的后果 |
| --- | --- | --- | --- | --- |
| `GHUSER` | GitHub 仓库所有者。派发 workflow、轮询状态、同步安卓 Secret 都用它 | `github_username` | 填账号名，如 `kgdwfn` | 空 → 「服务器未配置 GitHub 凭据」 |
| `GHBEARER` | 第 2.2 节那把 fine-grained PAT | `github_access_token` | 填 token 原文 | 空 / 权限不足 → 派发失败或安卓同步 403 |
| `GENURL` | 服务对外地址，用于产物回传与自定义客户端的更新检查地址 | `accessible_url_of_server` | `https://域名`，公网可达、不带尾斜杠 | 错 → runner 回传失败、更新检查指向错误地址 |
| `ZIP_PASSWORD` | 加密配置包（pyzipper AES）的密码；GitHub 侧同名 Secret 供 runner 解密 | `CHANGE_ME_ZIP_PASSWORD` | **换成非空随机串**，且与 GitHub Secret `ZIP_PASSWORD` 完全一致 | **留空 → 提交表单直接 Server Error (500)**；两边不一致 → runner 解不开包 |
| `SH_SECRET` | 自托管构建口令：表单 `sh_secret_field` 等于它才派发到自托管 workflow | `CHANGE_ME_SH_SECRET` | **换成非空随机串**，正常使用时表单不要填 | **空 + 表单空 → 被判定为自托管 → 422 / 无限排队**；不换占位值则任何看到它的人都能触发自托管派发 |

> 这两个 `CHANGE_ME_...` 是 `environment` 取值上唯一有意偏离上游原版的地方：上游写的是空串 `""`，会直接触发第五节的两个故障。其余变量取值与上游原版一致；compose 里其它差异（`TZ`、国内 DNS、`build: .`、安卓只读映射）属于部署层定制，不影响参数含义。

### 4.3 一般不用改的 5 个

| 变量 | 作用 | 当前值 | 什么时候要改 |
| --- | --- | --- | --- |
| `TZ` | 容器时区 | `Asia/Shanghai` | 一般不动 |
| `SECRET_KEY` | Django 会话/签名密钥 | 上游自带的 `django-insecure-…` 占位串 | **正式部署建议改成随机串**（直接在 compose 这一行改）。生成：`python3 -c "import secrets; print(secrets.token_hex(50))"` |
| `GHBRANCH` | 派发到哪个分支的 workflow | `master` | fork 换了默认分支才改 |
| `PROTOCOL` | 生成链接用 http 还是 https | `https` | 纯内网 http 部署时改成 `http` |
| `REPONAME` | 仓库名；安卓 Secret 也同步到这个仓库 | `rdgen` | fork 时改了仓库名才改 |

### 4.4 代码读取、compose 未预设

| 变量 | 作用 | 代码默认值 | 说明 |
| --- | --- | --- | --- |
| `DEBUG` | Django 调试模式 | `False` | 排障可临时设 `True`，生产勿开 |
| `LOG_FORMAT` | `json` 或 `text` | `text` | 接日志采集系统时设 `json`；未处理异常堆栈走 `django.request` logger |
| `ANDROID_SIGNING_DIR` | 容器内安卓密钥目录 | `/opt/rdgen/android_signing` | 改了它就要同步改 compose 的卷挂载目标 |

---

## 五、两个必须非空的字段（必读）

上游原版 compose 里这两个值是空串字面量（`ZIP_PASSWORD: ""`、`SH_SECRET: ""`），会绕过 `settings.py` 的兜底默认值。本 fork 的 compose 已写成非空占位值：

```yaml
ZIP_PASSWORD: "CHANGE_ME_ZIP_PASSWORD"
SH_SECRET: "CHANGE_ME_SH_SECRET"
```

**占位值必须换成你自己的随机串**，否则仍会出问题：

| 现象 | 根因 | 处理 |
| --- | --- | --- |
| 提交表单 `Server Error (500)` | `ZIP_PASSWORD` 为空 → pyzipper 的 WZ_AES 加密拿到空密码直接崩 | compose 里填非空随机串，且与 GitHub Secret 一致，然后 `docker compose up -d`（环境变量变了会重建容器） |
| runner 解不开加密配置包、构建失败 | 服务器 `ZIP_PASSWORD` 与 GitHub Secret `ZIP_PASSWORD` 不一致，或两边都还在用占位值 | 两边改成同一个随机串，重建容器 |
| `Cannot trigger a 'workflow_dispatch' on a disabled workflow`，或 run 一直排队 15 小时、连日志都下载不了 | `SH_SECRET` 空 + 表单 `sh_secret_field` 也空 → `空 == 空` 判定为自托管 → 派发到默认禁用的 `sh-generator-windows.yml`（`runs-on: self-hosted`，没注册 runner 就永远排队） | compose 里填非空随机串；正常用户不填该表单字段 |

生成随机串：

```bash
python3 -c "import secrets; print(secrets.token_hex(100))"   # ZIP_PASSWORD
python3 -c "import secrets; print(secrets.token_hex(16))"    # SH_SECRET
```

**结论：这两个是必填项，不是可选项。** 改完执行 `docker compose up -d`（环境变量变了会重建容器）。

> 手工部署（第八节）不经过 compose，若未显式设置则 `ZIP_PASSWORD` 用代码兜底值 `insecure`、`SH_SECRET` 用 `secret` —— 不会踩空串坑，但不安全，建议显式设置；`ZIP_PASSWORD` 仍要与 GitHub Secret 一致。

---

## 六、安卓固定签名：自动同步（本 fork 新增）

### 6.1 解决什么问题

上游每次安卓构建都用**随机生成的 debug keystore**，证书指纹每次都变。Android 规定同包名的新版本必须同签名才能覆盖安装，所以旧版必须先卸载。本 fork 改为固定一张正式证书，之后可以直接覆盖安装。

### 6.2 怎么配

1. 本仓库的 `android_signing/` 目录**只提交 `README.md`**，真实密钥文件放在宿主机同路径下（已被 `.gitignore` / `.dockerignore` 排除）。目录里放 3 个文件，清单见 `android_signing/README.md`：

   | 文件名 | 对应 GitHub Secret | 内容 |
   | --- | --- | --- |
   | `ANDROID_SIGNING_KEY.b64` | `ANDROID_SIGNING_KEY` | `rdgen-release.jks` 的 base64，一整行、无换行/空格/BOM |
   | `ANDROID_ALIAS.txt` | `ANDROID_ALIAS` | 密钥别名，如 `rdgen` |
   | `ANDROID_KEY_STORE_PASSWORD.txt` | `ANDROID_KEY_STORE_PASSWORD` | keystore 密码 |

2. compose 已有只读映射：`- ./android_signing:/opt/rdgen/android_signing:ro`。
3. PAT 要有 `Secrets: Read and write`（第 2.2 节）。
4. **不需要手工去 GitHub 网页贴 Secret，也不需要额外工具**：每次派发安卓构建前，`rdgenerator/android_secrets.py` 会用 `GHUSER`/`GHBEARER` 取仓库公钥、用 PyNaCl SealedBox 加密后 PUT 这 3 个 Secret，然后才派发 workflow。

### 6.3 关键约束

- **第 4 个 `ANDROID_KEY_PASSWORD` 不要配。** `sign-android-release` 只传 `--ks-pass`，workflow 会自动回退用 store 密码；所以 JKS 的 `storepass` 必须等于 `keypass`。
- 3 个文件**要么全放、要么全不放**：缺文件时容器自动跳过同步，安卓仍按 debug 签名构建，其它平台不受影响。
- **必须复用已有的那张证书**。重新生成 keystore 会换指纹，所有已安装设备又得卸载重装；`rdgen-release.jks` 和密码请离线长期备份。
- 首次从 debug 切到正式签名，仍需卸载一次旧版本；从第二次起可直接覆盖安装。
- 换密钥只需替换目录里的文件，下次构建自动生效。

### 6.4 验收

1. GitHub 仓库 → Secrets 列表出现那 3 个名字。
2. 安卓构建日志 `Check Android signing configuration` 显示 `Android signing key found`。
3. `apksigner verify --print-certs` 打印的 SHA-256 指纹与 `配置清单.txt` 记录一致。

排障：容器内可单独跑 `python rdgenerator/android_secrets.py` 看同步结果；报「缺少 PyNaCl」说明镜像没重建，执行 `docker compose up -d --build`。

---

## 七、更新与升级

```bash
cd rdgen
cp docker-compose.yml ../docker-compose.yml.bak   # 备份到仓库目录之外，避免带凭据的文件被误提交
git pull
# 把备份里 environment 的真实值对回新的 docker-compose.yml
docker compose up -d --build      # 关键：代码在镜像里，必须重建
docker compose ps                # 确认 healthy
```

验证容器里确实是新代码（把关键字换成当次改动里的函数名）：

```bash
docker compose exec rdgen grep -n "android_secrets" /opt/rdgen/rdgenerator/views.py
```

若你自己维护了定制的 compose 或环境变量，**`git pull` 前先备份**：仓库内的 `docker-compose.yml` 会被更新覆盖。上游同步（维护者）时，冲突文件的固定处理策略见仓库记忆，原则是保留本 fork 的定制功能，非冲突部分照常并入。

---

## 八、可选功能

### 8.1 自托管 Windows runner（一般不用）

- `sh-generator-windows.yml` 的显示名是 **「自托管 Windows 客户端生成器」**，`runs-on: self-hosted`，默认应保持禁用；另外两份（64 位 / 32 位 GitHub 托管）是英文名。
- 只有表单里的 `sh_secret_field` 与服务器 `SH_SECRET` 相等时才会派发到它。
- 前提：一台能编译 RustDesk 的 Windows 机器，并按 GitHub 说明注册 self-hosted runner。**没注册 runner 就会永远排队、连日志都没有**，这正是"卡 15 小时"的常见原因。
- 也可从网页保存 json 配置，用 [rdgen-cli](https://github.com/AlekseyLapunov/rdgen-cli) 在配置里加 `"sh_secret_field": "你的SH_SECRET"` 提交。

### 8.2 Windows 代码签名（可选）

在有 USB 签名令牌的 Windows 机器上跑 [signing_api](https://github.com/bryangerlach/signing_api)，然后在仓库加两个 Secret：`SIGN_BASE_URL`（签名服务公网地址）、`SIGN_API_KEY`（服务端 api key）。

### 8.3 手工部署（不用 Docker）

```bash
git clone https://github.com/你的用户名/rdgen.git
cd rdgen
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

环境变量可用 `export` 或 systemd 的 `Environment=` 设置；**至少要有** `GHUSER`、`GHBEARER`、`GENURL`、`ZIP_PASSWORD`（与 GitHub Secret 一致）、`SH_SECRET`（非空随机串）、`SECRET_KEY`。手工部署不经过 compose，若未显式设置这些变量，`ZIP_PASSWORD` 会用代码兜底值 `insecure`、`SH_SECRET` 用 `secret` —— 能用但不安全，建议显式设置。

systemd 单元示例（`/etc/systemd/system/rdgen.service`）：

```ini
[Unit]
Description=Rustdesk Client Generator
[Service]
Type=simple
LimitNOFILE=1000000
WorkingDirectory=/opt/rdgen/
Environment="GHUSER=你的账号"
Environment="GHBEARER=你的PAT"
Environment="GENURL=https://你的域名"
Environment="ZIP_PASSWORD=与GitHub_Secret一致"
Environment="SH_SECRET=非空随机串"
Environment="SECRET_KEY=随机串"
ExecStart=/opt/rdgen/.venv/bin/python3 /opt/rdgen/manage.py runserver 0.0.0.0:8000
Restart=always
RestartSec=10
StandardOutput=file:/var/log/rdgen.log
StandardError=file:/var/log/rdgen.error
[Install]
WantedBy=multi-user.target
```

然后 `sudo systemctl enable --now rdgen.service`。前面挂 nginx / Caddy 做 SSL 反代。

---

## 九、产物目录与构建单号

- 产物落在 `exe/<客户端标识>/` 下，同一客户端标识的新包会**覆盖同名文件**，判断有没有新包要看修改时间，不是看有没有新文件名。
- 32 位十六进制「构建单号」只是**批次号**，对应元数据 `exe/_batches/<单号>.json`，不是目录名。
- Windows 托管编译一般 30–60 分钟；排队期间页面状态靠轮询 GitHub API，容器访问 `api.github.com` 不稳定时状态会卡住，但不影响产物落盘。

---

## 十、故障速查

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `Server Error (500)` | `ZIP_PASSWORD` 为空（或还在用旧版空串 compose）→ pyzipper 崩溃 | 见第五节；另确认容器日志有完整堆栈（`django.request` logger 已补） |
| runner 解不开加密配置包、构建失败 | 服务器 `ZIP_PASSWORD` 与 GitHub Secret 不一致 | 两边对齐后 `docker compose up -d` |
| `Cannot trigger a 'workflow_dispatch' on a disabled workflow` / 无限排队 | `SH_SECRET` 空 + 表单空，误派自托管 workflow | 见第五节 |
| `Actions has been disabled for this repository` | 仓库 Actions 没开 | 第 2.1 节 |
| `Unexpected inputs provided: ["app_version"]` | 64 位 workflow 未声明该 input | 本 fork 派发已不再传它；若仍出现说明容器是旧镜像 → `--build` |
| `Unexpected inputs provided` 其它字段 | workflow 与服务器代码版本不一致 | `git pull` + `--build` 对齐 |
| 改了代码没生效 | `COPY . .`，代码不在卷里 | `docker compose up -d --build` |
| 日志只有 `Server Error (500)` 没有堆栈 | 旧镜像缺 `django.request` handler | 重建镜像 |
| 中文应用名 / 中文文件名 → 下载 500、MSI 闪退 | 上游 `isascii()` 兜底，不支持中文 | 用 ASCII 名称 |
| 首屏单排、刷新才双排 | CDN 上的 Bootstrap CSS 在国内网络加载失败 | 已知问题，当前维持 CDN |
| 安卓每次都要先卸载 | debug 签名，证书每次变 | 配第六节；首次切换仍需卸载一次 |
| 安卓同步报 HTTP 403 | PAT 缺 `Secrets: Read and write` | 第 2.2 节 |
| 报「缺少 PyNaCl」 | 镜像没重建或依赖没装 | `docker compose up -d --build` |
| 容器读不到 `android_signing` 里的文件 | 卷没挂上 / 文件权限太严 | `docker compose config` 核对 volumes、`docker inspect ... Mounts` 确认；文件至少 644、目录 755 |
| 状态一直"运行中"、没有日志下载 | 多半派到了自托管 workflow 且无 runner；或服务器↔GitHub 网络不通 | 核对 run 的 workflow 名；`docker compose logs` 看 `Failed to resolve api.github.com` |

---

## 十一、安全提醒

1. 本仓库是**公开仓库**，安卓签名私钥、`GHBEARER`、任何明文密码都绝不能提交进 git。`.gitignore` 已排除 `android_signing/*`（另保留 `.env` 忽略项只是防止本地私有文件误提交），`.dockerignore` 也排除了密钥目录，防止 `COPY . .` 把私钥打进镜像。
2. 私钥一旦泄露或提交，必须作废重建证书 —— 代价是所有已安装设备都要卸载重装。
3. PAT 按需最小授权；`Secrets: Read and write` 是本 fork 自动同步所需，若不想常驻可只在首次同步时临时提权。
4. 生产环境务必替换 `SECRET_KEY`，关闭 `DEBUG`，并在 `GENURL` 上启用 HTTPS。
