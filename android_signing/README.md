# 安卓固定签名 · 密钥目录说明与配置清单

这个目录专门存放 **RustDesk 安卓客户端的正式签名密钥**。容器会在每次派发安卓构建前，
自动把这里的三个值同步成 GitHub 仓库的 Actions Secret，runner 再拿去给 APK 签名。
这样一来，每次生成的 APK 都用同一张证书，新版本可以直接覆盖安装，不必先卸载旧版本。

> **本目录里的真实密钥文件绝不进 git**（`.gitignore` 已排除，只保留这份说明）。
> 本仓库是公开仓库，私钥一旦提交就必须作废重建，所有已安装设备会因此无法覆盖安装。

---

## 一、配置清单（三个必填项）

| 序号 | 目录里的文件名 | 对应的 GitHub Secret | 内容 | 是否必填 |
| --- | --- | --- | --- | --- |
| 1 | `ANDROID_SIGNING_KEY.b64` | `ANDROID_SIGNING_KEY` | 正式签名文件 `rdgen-release.jks` 的 base64，一整行，不带换行/空格/BOM | 必填 |
| 2 | `ANDROID_ALIAS.txt` | `ANDROID_ALIAS` | 密钥别名，一整行，例如 `rdgen` | 必填 |
| 3 | `ANDROID_KEY_STORE_PASSWORD.txt` | `ANDROID_KEY_STORE_PASSWORD` | keystore 密码，一整行 | 必填 |
| — | `rdgen-release.jks` | 不上传 | 正式签名文件本体，仅作离线备份 | 可选 |

**不需要配置** `ANDROID_KEY_PASSWORD`：签名动作只用 `--ks-pass`，不传 `--key-pass`，
workflow 会自动回退使用 keystore 密码。多配一项反而容易和上面不一致。

三项要么全放、要么一个都别放：只放一部分时，容器不会同步，安卓构建仍按原来的
debug 签名进行（不会报错，但新版本仍需卸载旧版本）。

## 二、目录映射

`docker-compose.yml` 已把本目录映射进容器（只读，容器不会改写密钥文件）：

```yaml
volumes:
  - ./android_signing:/opt/rdgen/android_signing:ro
```

也就是说，真实密钥放在 NAS 上与 `docker-compose.yml` 同级的 `android_signing/` 目录里。
如需换位置，可在容器环境变量里设 `ANDROID_SIGNING_DIR` 指向新路径。

## 三、自动同步是怎么跑起来的

```
用户点「生成」→ 容器读取本目录的 3 个文件
              → 用仓库公钥加密，PUT 成 3 个 Actions Secret
              → 派发 generator-android.yml
              → runner 用正式证书签名 APK，并打印证书指纹
```

- 同步用的是 `docker-compose.yml` 里已有的 `GHUSER` / `GHBEARER`，
  **不需要另外建令牌，也不需要去 GitHub 网页手工贴 Secret**。
- 前提是这把 PAT 具有仓库级 `Secrets: Read and write` 权限。
- 同步是覆盖写，每次派发安卓构建都会重新同步一次；换了新密钥只需替换目录里的文件，
  下次构建自动生效。
- 目录为空或文件不全时自动跳过，其它平台构建完全不受影响。

## 四、验证是否生效

1. GitHub 仓库 → Settings → Secrets and variables → Actions，应能看到上面 3 个 Secret。
2. 安卓构建日志里 `Check Android signing configuration` 显示 `Android signing key found`。
3. `Prefer signed APK when available` 步骤 `apksigner verify --print-certs` 打印的
   SHA-256 指纹，应与本目录 `配置清单.txt` 里记录的证书指纹一致。

## 五、注意事项

1. **必须复用已有的那张证书**。如果重新生成 keystore，证书指纹会变，所有已安装设备
   必须卸载旧版本才能装新版本。
2. `rdgen-release.jks` 与密码请离线永久备份，建议分开存放。
3. 首次从 debug 签名切到正式签名，仍需卸载一次旧版本；从第二次起即可直接覆盖安装。
4. 密钥文件不要提交 git、不要发到聊天/邮件；配置清单里不写明文密码。
5. 若容器日志出现「读取仓库加密公钥失败」，说明 PAT 缺 `Secrets: Read and write` 权限；
   若出现「缺少 PyNaCl」，说明镜像没重建，执行 `docker compose up -d --build`。
