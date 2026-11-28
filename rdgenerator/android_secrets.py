# -*- coding: utf-8 -*-
"""安卓固定签名：把映射目录里的签名材料同步成 GitHub 仓库 Actions Secret。

为什么不放进仓库
----------------
本仓库是公开仓库，签名私钥一旦提交进 git 就必须作废重建，已安装的设备会
因为证书不一致而无法覆盖安装。真实密钥只放在 docker-compose 映射出来的
目录里（默认 /opt/rdgen/android_signing），容器在派发安卓构建前用 GitHub API
把它同步成仓库级 Secret；runner 侧 generator-android.yml 的
「Check Android signing configuration」原样读取 secrets.* 即可，无需改动。

为什么需要 PyNaCl
-----------------
GitHub 的 repository secret 不接受明文，必须先用仓库公钥做 libsodium
sealed box 加密后 PUT。PyNaCl 已列入 requirements.txt。

模块不依赖 Django，可单独执行便于排障：
    python rdgenerator/android_secrets.py
"""
from __future__ import annotations

import base64
import binascii
import logging
import os

import requests

logger = logging.getLogger("rdgenerator.android_secrets")

API = "https://api.github.com"
TIMEOUT = 25

# Secret 名 -> 映射目录里的文件名。三个缺一不可，否则 workflow 会判定
# 「部分配置」直接报错退出。第四个 ANDROID_KEY_PASSWORD 不配即可（workflow 会自动回退）。
SECRET_FILES = {
    "ANDROID_SIGNING_KEY": "ANDROID_SIGNING_KEY.b64",
    "ANDROID_ALIAS": "ANDROID_ALIAS.txt",
    "ANDROID_KEY_STORE_PASSWORD": "ANDROID_KEY_STORE_PASSWORD.txt",
}


def _read_value(path):
    # utf-8-sig 顺带吃掉 Windows 记事本可能写进去的 BOM
    with open(path, "r", encoding="utf-8-sig") as f:
        return f.read().strip()


def _load_material(directory):
    """返回 (值字典, 错误说明)。目录为空/缺文件时返回 enabled=False 的提示。"""
    if not os.path.isdir(directory):
        return None, "未找到安卓签名目录 %s，本次仍按原来的 debug 签名构建" % directory

    values = {}
    missing = []
    for secret_name, filename in SECRET_FILES.items():
        path = os.path.join(directory, filename)
        if not os.path.isfile(path):
            missing.append(filename)
            continue
        values[secret_name] = _read_value(path)

    if missing:
        return None, "安卓签名目录缺少文件：%s，本次仍按原来的 debug 签名构建" % "、".join(missing)
    for secret_name, value in values.items():
        if not value:
            return None, "%s 内容为空，请检查映射目录里的密钥文件" % SECRET_FILES[secret_name]

    # base64 必须先验一次，避免把带换行/说明文字的文本同步上去，
    # 否则 runner 侧 apksigner 会以一个看不懂的错误失败。
    try:
        raw = base64.b64decode(values["ANDROID_SIGNING_KEY"], validate=True)
    except (binascii.Error, ValueError):
        return None, "ANDROID_SIGNING_KEY.b64 不是合法的 base64（应为一整行，不带换行、空格或 BOM）"
    if len(raw) < 500:
        return None, "ANDROID_SIGNING_KEY.b64 解码后只有 %d 字节，不像是 keystore 文件" % len(raw)
    return values, None


def _request(method, path, token, payload=None):
    return requests.request(
        method,
        API + path,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "rdgen-android-signing-sync",
        },
        json=payload,
        timeout=TIMEOUT,
    )


def sync(directory=None, owner=None, repo=None, token=None):
    """同步一次。返回 {'enabled', 'ok', 'message', 'synced'}。

    enabled=False 且 ok=True 表示「没放密钥」，属于正常的未启用状态，
    调用方应继续按原流程派发（保持 debug 签名），不阻断其它平台构建。
    """
    directory = directory or os.environ.get("ANDROID_SIGNING_DIR", "/opt/rdgen/android_signing")
    owner = owner or os.environ.get("GHUSER", "")
    repo = repo or os.environ.get("REPONAME", "rdgen")
    token = token or os.environ.get("GHBEARER", "")

    values, err = _load_material(directory)
    if err:
        return {"enabled": False, "ok": True, "message": err, "synced": []}

    if not owner or not token:
        return {
            "enabled": True, "ok": False,
            "message": "服务器未配置 GitHub 凭据（GHUSER / GHBEARER），无法同步安卓签名密钥",
            "synced": [],
        }

    try:
        from nacl import encoding, public
    except ImportError:
        return {
            "enabled": True, "ok": False,
            "message": "服务器缺少 PyNaCl，无法加密 Actions Secret；请执行 docker compose up -d --build 重建镜像",
            "synced": [],
        }

    base = "/repos/%s/%s/actions/secrets" % (owner, repo)
    try:
        r = _request("GET", base + "/public-key", token)
        if r.status_code != 200:
            return {
                "enabled": True, "ok": False,
                "message": "读取仓库加密公钥失败（HTTP %d）；请确认 PAT 具备 Secrets: Read and write 权限" % r.status_code,
                "synced": [],
            }
        key_info = r.json()
        box = public.SealedBox(
            public.PublicKey(key_info["key"].encode("utf-8"), encoding.Base64Encoder())
        )

        synced = []
        for secret_name in SECRET_FILES:
            encrypted = box.encrypt(values[secret_name].encode("utf-8"))
            payload = {
                "encrypted_value": base64.b64encode(encrypted).decode("ascii"),
                "key_id": str(key_info["key_id"]),
            }
            rr = _request("PUT", base + "/" + secret_name, token, payload)
            # 201=新建，204=覆盖更新；两者都算成功
            if rr.status_code not in (201, 204):
                return {
                    "enabled": True, "ok": False,
                    "message": "写入 Secret %s 失败（HTTP %d）：%s" % (
                        secret_name, rr.status_code, (rr.text or "")[:180]),
                    "synced": synced,
                }
            synced.append(secret_name)

        message = "安卓固定签名已就绪：已同步 %d 个仓库 Secret（%s）" % (
            len(synced), "、".join(synced))
        logger.info(message)
        return {"enabled": True, "ok": True, "message": message, "synced": synced}
    except requests.RequestException as e:
        return {
            "enabled": True, "ok": False,
            "message": "访问 GitHub API 失败（%s），请检查服务器网络后重试" % type(e).__name__,
            "synced": [],
        }
    except Exception as e:
        logger.exception("安卓签名密钥同步异常")
        return {
            "enabled": True, "ok": False,
            "message": "安卓签名密钥同步异常：%s" % e,
            "synced": [],
        }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    result = sync()
    print(("成功：" if result["ok"] else "失败：") + result["message"])
    if not result["ok"]:
        raise SystemExit(1)
