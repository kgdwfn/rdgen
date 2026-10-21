import io
import datetime
from pathlib import Path
from django.http import HttpResponse, JsonResponse, HttpResponseForbidden
from django.shortcuts import render, get_object_or_404, redirect
from django.core.files.base import ContentFile
import os
import secrets
import re
import requests
import base64
import json
import uuid
import pyzipper
from django.conf import settings as _settings
from django.db.models import Q
from .forms import GenerateForm
from .models import GithubRun
from PIL import Image
from urllib.parse import quote

# 固定命名空间：用于按「连接类型 + 平台」派生稳定 uuid。
# 同类型同平台跨版本重建 → 相同 uuid → 旧客户端更新地址不变，能拿到新版。
RDGEN_UUID_NS = uuid.UUID('9f3b2c1a-7d4e-4c8a-9b6f-1e2d3c4b5a6f')

# get_png, download and save_custom_client build file paths from request values;
# only accept a UUID and a plain file name so "../" or absolute paths cannot escape png/ and exe/.
_UUID_RE = re.compile(r'^[0-9a-fA-F-]{36}$')
_NAME_RE = re.compile(r'^[\w.-]+$')

def _safe_parts(uuid_val, filename):
    if not uuid_val or not _UUID_RE.match(uuid_val):
        return False
    if filename is not None and (not _NAME_RE.match(filename) or filename.startswith('.')):
        return False
    return True


# 平台中文标签（用于多构建结果页展示）
_PLATFORM_LABELS = {
    'windows': 'Windows 64 位',
    'windows-x86': 'Windows 32 位',
    'linux': 'Linux',
    'android': 'Android',
    'macos': 'macOS',
}


def _platform_label(platform):
    return _PLATFORM_LABELS.get(platform, platform)


# ── 多平台构建批次（batch）────────────────────────────────────────────────
# 每次提交构建生成一个 batch_id，等待页地址变成 /builds/<batch_id>，因此每次构建
# 地址都不同；刷新、收藏、换设备打开都能还原（不会退回 /generator）。
# 元数据落盘 exe/_batches/<batch_id>.json，与 exe/{uuid}/{file} 的文件式存储一致，
# 无需数据库迁移。
_BATCH_DIR = os.path.join('exe', '_batches')
_BATCH_ID_RE = re.compile(r'^[0-9a-f]{32}$')

# GitHub run 的终态
_TERMINAL_STATUSES = ('success', 'failure', 'cancelled', 'timed_out', 'skipped', 'action_required')


def _new_batch_id():
    return uuid.uuid4().hex


def _batch_path(batch_id):
    return os.path.join(_BATCH_DIR, f"{batch_id}.json")


def _save_batch(batch):
    os.makedirs(_BATCH_DIR, exist_ok=True)
    with open(_batch_path(batch['batch_id']), 'w', encoding='utf-8') as f:
        json.dump(batch, f, ensure_ascii=False, indent=2)


def _load_batch(batch_id):
    """读取批次元数据；batch_id 非法或文件不存在返回 None。"""
    if not batch_id or not _BATCH_ID_RE.match(batch_id):
        return None
    path = _batch_path(batch_id)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _batch_status(batch):
    """按批次汇总各平台实时状态、日志链接与（终态时的）下载链接。"""
    builds = []
    all_terminal = True
    for b in batch.get('builds', []):
        platform = b.get('platform', '')
        filename = b.get('filename', '')
        st = _get_run_status(b.get('uuid', ''))
        status = st['status'] if st.get('found') else 'unknown'
        terminal = status in _TERMINAL_STATUSES
        if not terminal:
            all_terminal = False
        builds.append({
            'uuid': b.get('uuid', ''),
            'platform': platform,
            'platform_label': b.get('platform_label') or _platform_label(platform),
            'filename': filename,
            'status': status,
            'log_url': st.get('github_log_url') or b.get('log_url', ''),
            'links': _download_links_for(platform, b.get('uuid', ''), filename) if terminal else [],
        })
    return {'builds': builds, 'all_terminal': all_terminal}


def _auto_download_link(platform, uuid, filename, full_url):
    """用户未填「自定义更新下载地址」时，按平台自动拼出工具自身的下载链接。

    仅带 uuid + platform，**不带具体 filename**：后端 download 视图会按 uuid 目录下的
    真实文件名 + platform 后缀匹配主包，从而彻底摆脱对 exename(文件名) 的依赖——
    用户构建时填什么文件名，点更新都能拿到当前目录下的正确包（旧客户端同理）。
    多架构平台（Linux / Android / macOS）也借此精确指向该 uuid 的包列表，而非裸首页。
    """
    return f"{full_url}/download?uuid={uuid}&platform={platform}"


def _download_links_for(platform, uuid, filename):
    """返回某平台构建产物的全部下载链接（与 generated.html / failure.html 的文件名规律一致）。

    每项形如 {'url': '/download?uuid=...&filename=...', 'name': '文件名'}，便于结果页展示。
    """
    base = f"/download?uuid={uuid}&filename="
    if platform == 'windows':
        names = [f"{filename}.exe", f"{filename}.msi"]
    elif platform == 'windows-x86':
        names = [f"{filename}.exe"]
    elif platform == 'linux':
        suffixes = [
            "x86_64.deb", "unattended-wayland-x86_64.deb", "x86_64.rpm",
            "suse-x86_64.rpm", "x86_64.pkg.tar.zst", "aarch64.deb",
            "aarch64.rpm", "suse-aarch64.rpm", "aarch64.pkg.tar.zst",
            "x86_64.AppImage", "aarch64.AppImage", "x86_64.flatpak", "aarch64.flatpak",
        ]
        names = [f"{filename}-{s}" for s in suffixes]
    elif platform == 'android':
        names = [f"{filename}-{s}" for s in ("aarch64.apk", "x86_64.apk", "armv7.apk")]
    elif platform == 'macos':
        names = [f"{filename}-{s}" for s in ("x86_64.dmg", "aarch64.dmg")]
    else:
        names = []
    return [{'url': f"{base}{n}", 'name': n} for n in names]


def _platform_suffixes(platform):
    """返回某平台构建产物的文件名后缀集合，用于 download 视图按 uuid 目录解析主包。

    与 _download_links_for 的后缀规律对应，但此处仅用于「无 filename 时从目录真实文件反推」。
    """
    if platform in ('windows', 'windows-x86'):
        return ('.exe', '.msi')
    if platform == 'linux':
        return ('.deb', '.rpm', '.AppImage', '.flatpak', '.pkg.tar.zst', '.zip', '.tar.gz', '.tar.xz')
    if platform == 'android':
        return ('.apk',)
    if platform == 'macos':
        return ('.dmg', '.pkg', '.zip')
    return ()


def generate_custom_client(params, full_url):
    """
    Core generation logic shared by web form and JSON API.

    Args:
        params: dict containing all configuration fields (keys match GenerateForm field names)
        full_url: the full URL of this service (protocol + host)

    Returns:
        dict with 'success' key. On success: also includes 'uuid', 'filename', 'platform', 'log_url'.
        On failure: includes 'error' and optionally 'status_code'.
    """
    user_secret = params.get('sh_secret_field', '')
    selfhosted = (_settings.SH_SECRET == user_secret)
    platform = params.get('platform', 'windows')
    version = params.get('version', '1.4.9')
    delayFix = params.get('delayFix', True)
    xOffline = params.get('xOffline', False)
    hidecm = params.get('hidecm', False)
    removeNewVersionNotif = params.get('removeNewVersionNotif', False)
    server = params.get('serverIP', '')
    serverPort = params.get('serverPort', '')
    key = params.get('key', '')
    apiServer = params.get('apiServer', '')
    urlLink = params.get('urlLink', '')
    downloadLink = params.get('downloadLink', '')
    if not server:
        server = 'rs-ny.rustdesk.com' #default rustdesk server
    if not serverPort:
        serverPort = '21116' #default rustdesk rendezvous port
    if not key:
        key = 'OeVuKk5nlHiXp+APNn0Y3pC1Iwpwn44JGqrQCsWqmBw=' #default rustdesk key
    if not apiServer:
        apiServer = server+":21114"
    if not urlLink:
        urlLink = "https://rustdesk.com"
    # 若用户未填更新地址，则在下方按平台自动生成（见 _auto_download_link），不在此给默认。
    direction = params.get('direction', 'both')
    installation = params.get('installation', 'installationY')
    settings = params.get('settings', 'settingsY')
    appname = params.get('appname', '')
    if not appname:
        appname = "rustdesk"
    # 运行时显示名：只写进 custom_.txt，不进构建链。
    # appname 仍受 ASCII 闸门约束（exe 文件名/注册表/资源/Runner.rc 全英文）；
    # displayname 允许中文，由 Rust 侧读 custom_.txt 的 app-name 后用于标题栏与界面文案。
    displayname = params.get('displayname', '')
    filename = params.get('exename', 'rustdesk')
    compname = params.get('compname', '')
    if not compname:
        compname = "Purslane Ltd"
    androidappid = params.get('androidappid', '')
    if not androidappid:
        androidappid = "com.carriez.flutter_hbb"
    compname = compname.replace("&","\\&")
    permPass = params.get('permanentPassword', '')
    theme = params.get('theme', 'system')
    themeDorO = params.get('themeDorO', 'default')
    passApproveMode = params.get('passApproveMode', 'password-click')
    denyLan = params.get('denyLan', False)
    enableDirectIP = params.get('enableDirectIP', False)
    autoClose = params.get('autoClose', False)
    permissionsDorO = params.get('permissionsDorO', 'default')
    permissionsType = params.get('permissionsType', 'custom')
    enableKeyboard = params.get('enableKeyboard', True)
    enableClipboard = params.get('enableClipboard', True)
    enableFileTransfer = params.get('enableFileTransfer', True)
    enableAudio = params.get('enableAudio', True)
    enableTCP = params.get('enableTCP', True)
    enableRemoteRestart = params.get('enableRemoteRestart', True)
    enableRecording = params.get('enableRecording', True)
    enableBlockingInput = params.get('enableBlockingInput', True)
    enableRemoteModi = params.get('enableRemoteModi', False)
    removeWallpaper = params.get('removeWallpaper', True)
    defaultManual = params.get('defaultManual', '')
    overrideManual = params.get('overrideManual', '')
    enablePrinter = params.get('enablePrinter', True)
    enableCamera = params.get('enableCamera', True)
    enableTerminal = params.get('enableTerminal', True)
    hide_tray_icon = params.get('hide_tray_icon', False)
    hideStopService = params.get('hideStopService', False)
    disableChangePermanentPassword = params.get('disableChangePermanentPassword', False)
    enableIPv6Punch = params.get('enableIPv6Punch', True)
    enableUDPPunch = params.get('enableUDPPunch', True)
    showQualityMonitor = params.get('showQualityMonitor', True)
    preElevateService = params.get('preElevateService', True)
    # ========== 自定义：界面定制字段 ==========
    hideSecuritySettings = params.get('hideSecuritySettings', False)
    hideNetworkSettings = params.get('hideNetworkSettings', False)
    hideRemotePrinterSettings = params.get('hideRemotePrinterSettings', False)
    hideServerSettings = params.get('hideServerSettings', False)
    hideProxySettings = params.get('hideProxySettings', False)
    hideWebsocketSettings = params.get('hideWebsocketSettings', False)
    hideAccountSettings = params.get('hideAccountSettings', False)
    # ========== 自定义结束 ==========

    if all(char.isascii() for char in filename):
        filename = re.sub(r'[^\w\s-]', '_', filename).strip()
        filename = filename.replace(" ","_")
    else:
        filename = "rustdesk"
    if not all(char.isascii() for char in appname):
        appname = "rustdesk"
    # 按「连接类型 + 平台」派生稳定 uuid：同类型同平台跨版本重建 → 相同 uuid，
    # 旧客户端更新地址不变、能拿到新版；不同类型/平台自然隔离。
    myuuid = str(uuid.uuid5(RDGEN_UUID_NS, f"{direction}|{platform}"))

    # 用户未填更新下载地址时，按平台自动拼出「工具域名 + 构建产物」作为更新链接
    effective_download_link = downloadLink
    if not effective_download_link:
        effective_download_link = _auto_download_link(platform, myuuid, filename, full_url)

    try:
        iconfile = params.get('iconfile')
        if not iconfile:
            iconfile = params.get('iconbase64')
        iconlink_url, iconlink_uuid, iconlink_file = save_png(iconfile,myuuid,full_url,"icon.png")
    except:
        print("failed to get icon, using default")
        iconlink_url = "false"
        iconlink_uuid = "false"
        iconlink_file = "false"
    try:
        logofile = params.get('logofile')
        if not logofile:
            logofile = params.get('logobase64')
        logolink_url, logolink_uuid, logolink_file = save_png(logofile,myuuid,full_url,"logo.png")
    except:
        print("failed to get logo")
        logolink_url = "false"
        logolink_uuid = "false"
        logolink_file = "false"
    try:
        privacyfile = params.get('privacyfile')
        if not privacyfile:
            privacyfile = params.get('privacybase64')
        privacylink_url, privacylink_uuid, privacylink_file = save_png(privacyfile,myuuid,full_url,"privacy.png")
    except:
        print("failed to get logo")
        privacylink_url = "false"
        privacylink_uuid = "false"
        privacylink_file = "false"

    ###create the custom.txt json here and send in as inputs below
    decodedCustom = {}
    if direction != "Both":
        decodedCustom['conn-type'] = direction
    if installation == "installationN":
        decodedCustom['disable-installation'] = 'Y'
    if settings == "settingsN":
        decodedCustom['disable-settings'] = 'Y'
    # app-name 决定运行时 APP_NAME -> 安装路径(C:\Program Files\<名>)、开始菜单目录、
    # 安装后的 exe 名、进程名、URI 前缀、注册表键，必须是纯 ASCII，
    # 否则自解压安装器会装不上（x86 走 target_pointer_width=32 分支变成 Program Files (x86)）。
    # 所以这里固定写 ASCII 的 appname；中文显示名走 display-name 键，
    # 由构建期的 lang 替换与 Dart 标题注入负责。
    runtime_appname = appname if appname else "rustdesk"
    if runtime_appname != "":
        decodedCustom['app-name'] = runtime_appname
    if displayname:
        decodedCustom['display-name'] = displayname
    decodedCustom['override-settings'] = {}
    decodedCustom['default-settings'] = {}

    # ========== 自定义：界面定制项始终写入 override-settings ==========
    if hideSecuritySettings:
        decodedCustom['override-settings']['hide-security-settings'] = 'Y'
    if hideNetworkSettings:
        decodedCustom['override-settings']['hide-network-settings'] = 'Y'
    if hideRemotePrinterSettings:
        decodedCustom['override-settings']['hide-remote-printer-settings'] = 'Y'
    if hideServerSettings:
        decodedCustom['override-settings']['hide-server-settings'] = 'Y'
    if hideProxySettings:
        decodedCustom['override-settings']['hide-proxy-settings'] = 'Y'
    if hideWebsocketSettings:
        decodedCustom['override-settings']['hide-websocket-settings'] = 'Y'
    # ========== 自定义结束 ==========

    if permPass != "":
        decodedCustom['password'] = permPass
    if theme != "system":
        if themeDorO == "default":
            if platform == "windows-x86":
                decodedCustom['default-settings']['allow-darktheme'] = 'Y' if theme == "dark" else 'N'
            else:
                decodedCustom['default-settings']['theme'] = theme
        elif themeDorO == "override":
            if platform == "windows-x86":
                decodedCustom['override-settings']['allow-darktheme'] = 'Y' if theme == "dark" else 'N'
            else:
                decodedCustom['override-settings']['theme'] = theme
    decodedCustom['enable-lan-discovery'] = 'N' if denyLan else 'Y'
    #decodedCustom['direct-server'] = 'Y' if enableDirectIP else 'N'
    decodedCustom['allow-auto-disconnect'] = 'Y' if autoClose else 'N'

    if permissionsDorO == "default":
        decodedCustom['default-settings']['access-mode'] = permissionsType
        decodedCustom['default-settings']['enable-keyboard'] = 'Y' if enableKeyboard else 'N'
        decodedCustom['default-settings']['enable-clipboard'] = 'Y' if enableClipboard else 'N'
        decodedCustom['default-settings']['enable-file-transfer'] = 'Y' if enableFileTransfer else 'N'
        decodedCustom['default-settings']['enable-audio'] = 'Y' if enableAudio else 'N'
        decodedCustom['default-settings']['enable-tunnel'] = 'Y' if enableTCP else 'N'
        decodedCustom['default-settings']['enable-remote-restart'] = 'Y' if enableRemoteRestart else 'N'
        decodedCustom['default-settings']['enable-record-session'] = 'Y' if enableRecording else 'N'
        decodedCustom['default-settings']['enable-block-input'] = 'Y' if enableBlockingInput else 'N'
        decodedCustom['default-settings']['allow-remote-config-modification'] = 'Y' if enableRemoteModi else 'N'
        decodedCustom['default-settings']['direct-server'] = 'Y' if enableDirectIP else 'N'
        decodedCustom['default-settings']['verification-method'] = 'use-permanent-password' if hidecm else 'use-both-passwords'
        decodedCustom['default-settings']['approve-mode'] = passApproveMode
        decodedCustom['default-settings']['allow-hide-cm'] = 'Y' if hidecm else 'N'
        decodedCustom['default-settings']['allow-remove-wallpaper'] = 'Y' if removeWallpaper else 'N'
        decodedCustom['default-settings']['enable-remote-printer'] = 'Y' if enablePrinter else 'N'
        decodedCustom['default-settings']['enable-camera'] = 'Y' if enableCamera else 'N'
        decodedCustom['default-settings']['enable-terminal'] = 'Y' if enableTerminal else 'N'
        # ========== 自定义：高级设置项 ==========
        decodedCustom['default-settings']['hide-tray'] = 'Y' if hide_tray_icon else 'N'
        decodedCustom['default-settings']['hide-stop-service'] = 'Y' if hideStopService else 'N'
        decodedCustom['default-settings']['disable-change-permanent-password'] = 'Y' if disableChangePermanentPassword else 'N'
        decodedCustom['default-settings']['enable-ipv6-punch'] = 'Y' if enableIPv6Punch else 'N'
        decodedCustom['default-settings']['enable-udp-punch'] = 'Y' if enableUDPPunch else 'N'
        decodedCustom['default-settings']['show-quality-monitor'] = 'Y' if showQualityMonitor else 'N'
        if direction == 'incoming' and installation == 'installationN':
            decodedCustom['default-settings']['pre-elevate-service'] = 'Y' if preElevateService else 'N'
        # ========== 自定义结束 ==========
        

    else:
        decodedCustom['override-settings']['access-mode'] = permissionsType
        decodedCustom['override-settings']['enable-keyboard'] = 'Y' if enableKeyboard else 'N'
        decodedCustom['override-settings']['enable-clipboard'] = 'Y' if enableClipboard else 'N'
        decodedCustom['override-settings']['enable-file-transfer'] = 'Y' if enableFileTransfer else 'N'
        decodedCustom['override-settings']['enable-audio'] = 'Y' if enableAudio else 'N'
        decodedCustom['override-settings']['enable-tunnel'] = 'Y' if enableTCP else 'N'
        decodedCustom['override-settings']['enable-remote-restart'] = 'Y' if enableRemoteRestart else 'N'
        decodedCustom['override-settings']['enable-record-session'] = 'Y' if enableRecording else 'N'
        decodedCustom['override-settings']['enable-block-input'] = 'Y' if enableBlockingInput else 'N'
        decodedCustom['override-settings']['allow-remote-config-modification'] = 'Y' if enableRemoteModi else 'N'
        decodedCustom['override-settings']['direct-server'] = 'Y' if enableDirectIP else 'N'
        decodedCustom['override-settings']['verification-method'] = 'use-permanent-password' if hidecm else 'use-both-passwords'
        decodedCustom['override-settings']['approve-mode'] = passApproveMode
        decodedCustom['override-settings']['allow-hide-cm'] = 'Y' if hidecm else 'N'
        decodedCustom['override-settings']['allow-remove-wallpaper'] = 'Y' if removeWallpaper else 'N'
        decodedCustom['override-settings']['enable-remote-printer'] = 'Y' if enablePrinter else 'N'
        decodedCustom['override-settings']['enable-camera'] = 'Y' if enableCamera else 'N'
        decodedCustom['override-settings']['enable-terminal'] = 'Y' if enableTerminal else 'N'
        # ========== 自定义：高级设置项 ==========
        decodedCustom['override-settings']['hide-tray'] = 'Y' if hide_tray_icon else 'N'
        decodedCustom['override-settings']['hide-stop-service'] = 'Y' if hideStopService else 'N'
        decodedCustom['override-settings']['disable-change-permanent-password'] = 'Y' if disableChangePermanentPassword else 'N'
        decodedCustom['override-settings']['enable-ipv6-punch'] = 'Y' if enableIPv6Punch else 'N'
        decodedCustom['override-settings']['enable-udp-punch'] = 'Y' if enableUDPPunch else 'N'
        decodedCustom['override-settings']['show-quality-monitor'] = 'Y' if showQualityMonitor else 'N'
        if direction == 'incoming' and installation == 'installationN':
            decodedCustom['override-settings']['pre-elevate-service'] = 'Y' if preElevateService else 'N'
        # ========== 自定义结束 ==========
        if direction == 'incoming':
            decodedCustom['override-settings']['custom-rendezvous-server'] = server
            decodedCustom['override-settings']['api-server'] = apiServer

    if defaultManual:
        for line in defaultManual.splitlines():
            if '=' in line:
                k, value = line.split('=', 1)
                decodedCustom['default-settings'][k.strip()] = value.strip()

    if overrideManual:
        for line in overrideManual.splitlines():
            if '=' in line:
                k, value = line.split('=', 1)
                decodedCustom['override-settings'][k.strip()] = value.strip()
    
    decodedCustomJson = json.dumps(decodedCustom)

    string_bytes = decodedCustomJson.encode("ascii")
    base64_bytes = base64.b64encode(string_bytes)
    encodedCustom = base64_bytes.decode("ascii")

    ####from here run the github action, we need user, repo, access token.
    if platform == 'windows':
        url = 'https://api.github.com/repos/'+_settings.GHUSER+'/'+_settings.REPONAME+'/actions/workflows/generator-windows.yml/dispatches'
        if selfhosted:
            url = 'https://api.github.com/repos/'+_settings.GHUSER+'/'+_settings.REPONAME+'/actions/workflows/sh-generator-windows.yml/dispatches'
    if platform == 'windows-x86':
        url = 'https://api.github.com/repos/'+_settings.GHUSER+'/'+_settings.REPONAME+'/actions/workflows/generator-windows-x86.yml/dispatches'
    elif platform == 'linux':
        url = 'https://api.github.com/repos/'+_settings.GHUSER+'/'+_settings.REPONAME+'/actions/workflows/generator-linux.yml/dispatches'
    elif platform == 'android':
        url = 'https://api.github.com/repos/'+_settings.GHUSER+'/'+_settings.REPONAME+'/actions/workflows/generator-android.yml/dispatches'
    elif platform == 'macos':
        url = 'https://api.github.com/repos/'+_settings.GHUSER+'/'+_settings.REPONAME+'/actions/workflows/generator-macos.yml/dispatches'
    else:
        url = 'https://api.github.com/repos/'+_settings.GHUSER+'/'+_settings.REPONAME+'/actions/workflows/generator-windows.yml/dispatches'
        if selfhosted:
            url = 'https://api.github.com/repos/'+_settings.GHUSER+'/'+_settings.REPONAME+'/actions/workflows/sh-generator-windows.yml/dispatches'

    # 自定义 Logo 显示尺寸：留空=上游默认 300x60；填了则按比例放大约束框
    logo_size_raw = params.get('logo_size')
    logo_max_height = 'false'
    logo_max_width = 'false'
    if logo_size_raw:
        try:
            _h = int(logo_size_raw)
            if 20 <= _h <= 600:
                logo_max_height = str(_h)
                logo_max_width = str(_h * 5)
        except (TypeError, ValueError):
            pass

    inputs_raw = {
        "server":server,
        "serverPort":serverPort,
        "key":key,
        "apiServer":apiServer,
        "custom":encodedCustom,
        "uuid":myuuid,
        "iconlink_url":iconlink_url,
        "iconlink_uuid":iconlink_uuid,
        "iconlink_file":iconlink_file,
        "logolink_url":logolink_url,
        "logolink_uuid":logolink_uuid,
        "logolink_file":logolink_file,
        "privacylink_url":privacylink_url,
        "privacylink_uuid":privacylink_uuid,
        "privacylink_file":privacylink_file,
        # 自定义 Logo 显示框尺寸（上游默认 BoxConstraints(maxWidth:300,maxHeight:60)）
        "logo_max_width":logo_max_width,
        "logo_max_height":logo_max_height,
        "appname":appname,
        # 界面显示名称（可中文）：只用于 UI 文案与窗口标题，
        # 不进入 app-name（安装路径/进程名/注册表等必须 ASCII）。
        "displayname":displayname,
        "genurl":_settings.GENURL,
        "urlLink":urlLink,
        "downloadLink":effective_download_link,
        "delayFix": 'true' if delayFix else 'false',
        "rdgen":'true',
        "xOffline": 'true' if xOffline else 'false',
        "removeNewVersionNotif": 'true' if removeNewVersionNotif else 'false',
        "compname": compname,
        "androidappid":androidappid,
        "filename":filename,
        # ========== 自定义：新增功能开关透传给 workflow ==========
        "hide_tray_icon": 'true' if hide_tray_icon else 'false',
        "hideStopService": 'true' if hideStopService else 'false',
        "disableChangePermanentPassword": 'true' if disableChangePermanentPassword else 'false',
        "enableIPv6Punch": 'true' if enableIPv6Punch else 'false',
        "enableUDPPunch": 'true' if enableUDPPunch else 'false',
        "showQualityMonitor": 'true' if showQualityMonitor else 'false',
        "preElevateService": 'true' if preElevateService else 'false',
        "hideAccountSettings": 'true' if hideAccountSettings else 'false',
        # ========== 自定义结束 ==========
    }

    temp_json_path = f"data_{uuid.uuid4()}.json"
    zip_filename = f"secrets_{uuid.uuid4()}.zip"
    zip_path = "temp_zips/%s" % (zip_filename)
    Path("temp_zips").mkdir(parents=True, exist_ok=True)

    with open(temp_json_path, "w") as f:
        json.dump(inputs_raw, f)

    with pyzipper.AESZipFile(zip_path, 'w', compression=pyzipper.ZIP_LZMA, encryption=pyzipper.WZ_AES) as zf:
        zf.setpassword(_settings.ZIP_PASSWORD.encode())
        zf.write(temp_json_path, arcname="secrets.json")

    if os.path.exists(temp_json_path):
        os.remove(temp_json_path)

    zipJson = {}
    zipJson['url'] = full_url
    zipJson['file'] = zip_filename

    zip_url = json.dumps(zipJson)

    data = {
        "ref":_settings.GHBRANCH,
        "inputs":{
            "version":version,
            "zip_url":zip_url
        },
        "return_run_details": True
    } 
    headers = {
        'Accept':  'application/vnd.github+json',
        'Content-Type': 'application/json',
        'Authorization': 'Bearer '+_settings.GHBEARER,
        'X-GitHub-Api-Version': '2026-03-10'
    }
    new_github_run = GithubRun(
        uuid=myuuid,
        status="Starting generator...please wait"
    )
    try:
        response = requests.post(url, json=data, headers=headers)
        if response.status_code == 204 or response.status_code == 200:
            github_data = response.json()
            print(github_data)
            new_github_run.github_run_id = github_data.get('workflow_run_id')
            new_github_run.status = "in_progress"
            new_github_run.save()

            return {
                "success": True,
                "uuid": myuuid,
                "filename": filename,
                "platform": platform,
                "log_url": github_data.get('html_url')
            }
        else:
            return {
                "success": False,
                "error": "GitHub rejected the start request",
                "status_code": 500
            }
    except Exception as e:
        return {
            "success": False,
            "error": f"Connection error: {str(e)}",
            "status_code": 500
        }


def _get_run_status(uuid_val):
    """
    Core status-check logic shared by web form and JSON API.

    Args:
        uuid_val: the UUID string of the generation run

    Returns:
        dict with 'found', 'status', 'github_log_url', and optionally 'gh_run'.
        If not found, 'found' is False.
    """
    try:
        gh_run = GithubRun.objects.get(uuid=uuid_val)
    except GithubRun.DoesNotExist:
        return {"found": False}

    github_log_url = f"https://github.com/{_settings.GHUSER}/{_settings.REPONAME}/actions/runs/{gh_run.github_run_id}"

    if gh_run.status not in ['success', 'failure', 'cancelled', 'timed_out', 'skipped']:
        headers = {
            "Authorization": f"Bearer {_settings.GHBEARER}",
            "Accept": "application/vnd.github+json"
        }
        api_url = f"https://api.github.com/repos/{_settings.GHUSER}/{_settings.REPONAME}/actions/runs/{gh_run.github_run_id}"
        
        try:
            gh_response = requests.get(api_url, headers=headers)
            if gh_response.status_code == 200:
                gh_data = gh_response.json()
                
                if gh_data['status'] == 'completed':
                    gh_run.status = gh_data['conclusion']
                    gh_run.save()
        except Exception as e:
            print(f"Error checking GitHub: {e}")

    return {
        "found": True,
        "status": gh_run.status,
        "github_log_url": github_log_url,
        "gh_run": gh_run
    }


def generator_view(request):
    if request.method == 'POST':
        form = GenerateForm(request.POST, request.FILES)
        if form.is_valid():
            params = form.cleaned_data
            full_url = f"{_settings.PROTOCOL}://{request.get_host()}" if _settings.GENURL else f"{_settings.PROTOCOL}://{request.get_host()}"
            # 平台多选：每个平台独立构建、各自 uuid 与下载链接
            platforms = params.get('platform') or ['windows']
            builds = []
            for p in platforms:
                single = dict(params)
                single['platform'] = p
                res = generate_custom_client(single, full_url)
                if res.get('success'):
                    res['platform_label'] = _platform_label(res['platform'])
                    builds.append(res)
            if not builds:
                return JsonResponse({"error": "所有平台构建均失败，请检查配置后重试。"}, status=500)
            # 生成批次号并落盘，随后 302 到 /builds/<batch_id>：
            # 每次构建地址都不同，刷新/收藏/换设备都能回到这次构建。
            batch_id = _new_batch_id()
            _save_batch({
                'batch_id': batch_id,
                'version': params.get('version') or '1.4.9',
                'created_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'builds': [
                    {
                        'uuid': b['uuid'],
                        'platform': b['platform'],
                        'platform_label': b.get('platform_label') or _platform_label(b['platform']),
                        'filename': b['filename'],
                        'log_url': b.get('log_url', ''),
                    }
                    for b in builds
                ],
            })
            return redirect(f'/builds/{batch_id}')
        else:
            form = GenerateForm(request.POST, request.FILES)
    else:
        form = GenerateForm()
    #return render(request, 'maintenance.html')
    return render(request, 'generator.html', {'form': form})


def check_for_file(request):
    filename = request.GET.get('filename')
    uuid = request.GET.get('uuid')
    platform = request.GET.get('platform')

    result = _get_run_status(uuid)
    if not result['found']:
        from django.http import Http404
        raise Http404("Run not found")

    gh_run = result['gh_run']
    github_log_url = result['github_log_url']

    if gh_run.status == "success":
        return render(request, 'generated.html', {
            'filename': filename, 
            'uuid': uuid, 
            'platform': platform
        })
        
    elif gh_run.status in ['failure', 'cancelled', 'timed_out', 'skipped', 'action_required']:
        return render(request, 'failure.html', {
            'log_url': github_log_url, 
            'filename': filename, 
            'uuid': uuid, 
            'platform': platform,
            'status': gh_run.status
        })
        
    else:
        return render(request, 'waiting.html', {
            'filename': filename, 
            'uuid': uuid, 
            'status': gh_run.status, 
            'platform': platform, 
            'log_url': github_log_url
        })

def download(request):
    uuid = request.GET.get('uuid', '')
    filename = request.GET.get('filename', '')
    platform = request.GET.get('platform', '')

    # 1) 旧链接格式（带具体 filename）：保持原逻辑，兼容历史包
    if filename:
        if not _safe_parts(uuid, filename):
            return HttpResponseForbidden("Invalid filename")
        file_path = os.path.join('exe', uuid, filename)
        if not os.path.isfile(file_path):
            from django.http import Http404
            raise Http404("File not found")
        with open(file_path, 'rb') as file:
            content = file.read()
        return HttpResponse(content, headers={
            'Content-Type': 'application/vnd.microsoft-portable-executable',
            'Content-Disposition': f'attachment; filename="{filename}"'
        })

    # 2) 新格式：仅 uuid + platform，按目录下真实文件 + platform 后缀解析主包
    #    （彻底摆脱对 exename/文件名的依赖：用户构建填什么文件名都不影响更新）
    if not uuid or not platform:
        return HttpResponseForbidden("Missing uuid or platform")
    if not _safe_parts(uuid, None):
        return HttpResponseForbidden("Invalid uuid")
    exe_dir = os.path.join('exe', uuid)
    if not os.path.isdir(exe_dir):
        from django.http import Http404
        raise Http404("Build not found")
    suffixes = _platform_suffixes(platform)
    candidates = sorted(f for f in os.listdir(exe_dir) if any(f.endswith(s) for s in suffixes))
    if not candidates:
        from django.http import Http404
        raise Http404("No package for this platform")
    if len(candidates) == 1:
        # 单包（如 Windows 的 exe）→ 直接下载当前目录下的真实文件
        fn = candidates[0]
        file_path = os.path.join(exe_dir, fn)
        with open(file_path, 'rb') as file:
            content = file.read()
        return HttpResponse(content, headers={
            'Content-Type': 'application/vnd.microsoft-portable-executable',
            'Content-Disposition': f'attachment; filename="{fn}"'
        })
    # 多架构（Linux / Android / macOS）→ 列表页让用户/客户端选择
    base = f"/download?uuid={uuid}&filename="
    files = [{'url': f"{base}{quote(f)}", 'name': f} for f in candidates]
    return render(request, 'download_list.html', {
        'uuid': uuid,
        'platform': platform,
        'platform_label': _platform_label(platform),
        'files': files,
    })


def check_builds(request):
    """多构建状态轮询：返回每个 uuid 的构建状态与下载链接（JSON）。"""
    uuids = [u for u in request.GET.get('uuids', '').split(',') if u]
    platforms = request.GET.get('platforms', '').split(',')
    filenames = request.GET.get('filenames', '').split(',')
    builds = []
    all_terminal = True
    for i, u in enumerate(uuids):
        plat = platforms[i] if i < len(platforms) else ''
        fname = filenames[i] if i < len(filenames) else ''
        st = _get_run_status(u)
        status = st['status'] if st['found'] else 'unknown'
        terminal = status in ('success', 'failure', 'cancelled', 'timed_out', 'skipped', 'action_required')
        if not terminal:
            all_terminal = False
        links = _download_links_for(plat, u, fname) if terminal else []
        builds.append({
            'uuid': u,
            'platform': plat,
            'platform_label': _platform_label(plat),
            'filename': fname,
            'status': status,
            'log_url': st.get('github_log_url', ''),
            'links': links,
        })
    return JsonResponse({'builds': builds, 'all_terminal': all_terminal})


def builds_result(request):
    """多构建最终结果页：列出每个平台产物下载链接。"""
    uuids = [u for u in request.GET.get('uuids', '').split(',') if u]
    platforms = request.GET.get('platforms', '').split(',')
    filenames = request.GET.get('filenames', '').split(',')
    builds = []
    for i, u in enumerate(uuids):
        plat = platforms[i] if i < len(platforms) else ''
        fname = filenames[i] if i < len(filenames) else ''
        st = _get_run_status(u)
        status = st['status'] if st['found'] else 'unknown'
        builds.append({
            'uuid': u,
            'platform': plat,
            'platform_label': _platform_label(plat),
            'filename': fname,
            'status': status,
            'log_url': st.get('github_log_url', ''),
            'links': _download_links_for(plat, u, fname),
        })
    return render(request, 'multi_result.html', {'builds': builds})


def builds_view(request, batch_id):
    """单次构建批次的等待页：地址 /builds/<batch_id>，每次构建都不同。"""
    batch = _load_batch(batch_id)
    if not batch:
        from django.http import Http404
        raise Http404("构建批次不存在或已过期")
    return render(request, 'multi_wait.html', {
        'batch_id': batch_id,
        'version': batch.get('version', ''),
        'created_at': batch.get('created_at', ''),
        'builds': batch.get('builds', []),
    })


def builds_status(request, batch_id):
    """等待页轮询接口：按批次返回各平台实时状态与下载链接（JSON）。"""
    batch = _load_batch(batch_id)
    if not batch:
        return JsonResponse({'error': 'build batch not found'}, status=404)
    return JsonResponse(_batch_status(batch))


def builds_result_view(request, batch_id):
    """单次构建批次的最终结果页：地址 /builds/<batch_id>/result。"""
    batch = _load_batch(batch_id)
    if not batch:
        from django.http import Http404
        raise Http404("构建批次不存在或已过期")
    status = _batch_status(batch)
    return render(request, 'multi_result.html', {
        'batch_id': batch_id,
        'version': batch.get('version', ''),
        'created_at': batch.get('created_at', ''),
        'builds': status['builds'],
    })


def get_png(request):
    filename = request.GET['filename']
    uuid = request.GET['uuid']
    if not _safe_parts(uuid, filename):
        return HttpResponseForbidden("Invalid filename")
    file_path = os.path.join('png',uuid,filename)
    with open(file_path, 'rb') as file:
        response = HttpResponse(file, headers={
            'Content-Type': 'application/vnd.microsoft.portable-executable',
            'Content-Disposition': f'attachment; filename="{filename}"'
        })

    return response

def create_github_run(myuuid):
    new_github_run = GithubRun(
        uuid=myuuid,
        status="Starting generator...please wait"
    )
    new_github_run.save()

def update_github_run(request):
    data = json.loads(request.body)
    myuuid = data.get('uuid')
    mystatus = data.get('status')
    GithubRun.objects.filter(Q(uuid=myuuid)).update(status=mystatus)
    return HttpResponse('')

def resize_and_encode_icon(imagefile):
    maxWidth = 200
    try:
        with io.BytesIO() as image_buffer:
            for chunk in imagefile.chunks():
                image_buffer.write(chunk)
            image_buffer.seek(0)

            img = Image.open(image_buffer)
            imgcopy = img.copy()
    except (IOError, OSError):
        raise ValueError("Uploaded file is not a valid image format.")

    # Check if resizing is necessary
    if img.size[0] <= maxWidth:
        with io.BytesIO() as image_buffer:
            imgcopy.save(image_buffer, format=imagefile.content_type.split('/')[1])
            image_buffer.seek(0)
            return_image = ContentFile(image_buffer.read(), name=imagefile.name)
        return base64.b64encode(return_image.read())

    # Calculate resized height based on aspect ratio
    wpercent = (maxWidth / float(img.size[0]))
    hsize = int((float(img.size[1]) * float(wpercent)))

    # Resize the image while maintaining aspect ratio using LANCZOS resampling
    imgcopy = imgcopy.resize((maxWidth, hsize), Image.Resampling.LANCZOS)

    with io.BytesIO() as resized_image_buffer:
        imgcopy.save(resized_image_buffer, format=imagefile.content_type.split('/')[1])
        resized_image_buffer.seek(0)

        resized_imagefile = ContentFile(resized_image_buffer.read(), name=imagefile.name)

    # Return the Base64 encoded representation of the resized image
    resized64 = base64.b64encode(resized_imagefile.read())
    #print(resized64)
    return resized64
 
#the following is used when accessed from an external source, like the rustdesk api server
def startgh(request):
    #print(request)
    data_ = json.loads(request.body)
    ####from here run the github action, we need user, repo, access token.
    url = 'https://api.github.com/repos/'+_settings.GHUSER+'/'+_settings.REPONAME+'/actions/workflows/generator-'+data_.get('platform')+'.yml/dispatches'  
    data = {
        "ref": _settings.GHBRANCH,
        "inputs":{
            "server":data_.get('server'),
            "key":data_.get('key'),
            "apiServer":data_.get('apiServer'),
            "custom":data_.get('custom'),
            "uuid":data_.get('uuid'),
            "iconlink":data_.get('iconlink'),
            "logolink":data_.get('logolink'),
            "appname":data_.get('appname'),
            "extras":data_.get('extras'),
            "filename":data_.get('filename')
        }
    } 
    headers = {
        'Accept':  'application/vnd.github+json',
        'Content-Type': 'application/json',
        'Authorization': 'Bearer '+_settings.GHBEARER,
        'X-GitHub-Api-Version': '2026-03-10'
    }
    response = requests.post(url, json=data, headers=headers)
    print(response)
    return HttpResponse(status=204)

def save_png(file, uuid, domain, name):
    file_save_path = "png/%s/%s" % (uuid, name)
    Path("png/%s" % uuid).mkdir(parents=True, exist_ok=True)

    if isinstance(file, str):  # Check if it's a base64 string
        try:
            header, encoded = file.split(';base64,')
            decoded_img = base64.b64decode(encoded)
            file = ContentFile(decoded_img, name=name) # Create a file-like object
        except ValueError:
            print("Invalid base64 data")
            return None  # Or handle the error as you see fit
        except Exception as e:  # Catch general exceptions during decoding
            print(f"Error decoding base64: {e}")
            return None
        
    with open(file_save_path, "wb+") as f:
        for chunk in file.chunks():
            f.write(chunk)
    # imageJson = {}
    # imageJson['url'] = domain
    # imageJson['uuid'] = uuid
    # imageJson['file'] = name
    #return "%s/%s" % (domain, file_save_path)
    return domain, uuid, name

def save_custom_client(request):
    file = request.FILES['file']
    myuuid = request.POST.get('uuid')
    if not _safe_parts(myuuid, file.name):
        return HttpResponseForbidden("Invalid filename")
    file_save_path = "exe/%s/%s" % (myuuid, file.name)
    Path("exe/%s" % myuuid).mkdir(parents=True, exist_ok=True)
    with open(file_save_path, "wb+") as f:
        for chunk in file.chunks():
            f.write(chunk)

    return HttpResponse("File saved successfully!")

def cleanup_secrets(request):
    # Pass the UUID as a query param or in JSON body
    data = json.loads(request.body)
    my_uuid = data.get('uuid')
    
    if not my_uuid:
        return HttpResponse("Missing UUID", status=400)

    # 1. Find the files in your temp directory matching the UUID
    temp_dir = os.path.join('temp_zips')
    
    # We look for any file starting with 'secrets_' and containing the uuid
    for filename in os.listdir(temp_dir):
        if my_uuid in filename and filename.endswith('.zip'):
            file_path = os.path.join(temp_dir, filename)
            try:
                os.remove(file_path)
                print(f"Successfully deleted {file_path}")
            except OSError as e:
                print(f"Error deleting file: {e}")

    return HttpResponse("Cleanup successful", status=200)

def get_zip(request):
    filename = request.GET['filename']
    base_dir = os.path.abspath('temp_zips')
    file_path = os.path.abspath(os.path.join(base_dir, filename))
    if not file_path.startswith(base_dir + os.sep):
        return HttpResponseForbidden("Invalid filename")
    with open(file_path, 'rb') as file:
        response = HttpResponse(file, headers={
            'Content-Type': 'application/vnd.microsoft.portable-executable',
            'Content-Disposition': f'attachment; filename="{filename}"'
        })

    return response
