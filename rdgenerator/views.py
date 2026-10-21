import io
from pathlib import Path
from django.http import HttpResponse, JsonResponse, HttpResponseForbidden
from django.shortcuts import render, get_object_or_404
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


def _auto_download_link(platform, uuid, filename, full_url):
    """用户未填「自定义更新下载地址」时，按平台自动拼出工具自身的下载链接。

    - Windows / Windows-x86：直接指向本次构建的 exe 文件，客户端点「检查更新」即可下载。
    - 多架构平台（Linux / Android / macOS）：构建时无法预知最终设备 CPU 架构，
      给不出单一 arch 的具体文件 URL，故指向工具首页，由用户/客户端重新获取。
    """
    if platform in ('windows', 'windows-x86'):
        return f"{full_url}/download?uuid={uuid}&filename={filename}.exe"
    return full_url


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
    myuuid = str(uuid.uuid4())

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
            # 统一走多构建等待 / 结果流程（单平台同样适用）
            return render(request, 'multi_wait.html', {
                'builds': builds,
                'uuids': ",".join(b['uuid'] for b in builds),
                'platforms': ",".join(b['platform'] for b in builds),
                'filenames': ",".join(b['filename'] for b in builds),
            })
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
    filename = request.GET['filename']
    uuid = request.GET['uuid']
    if not _safe_parts(uuid, filename):
        return HttpResponseForbidden("Invalid filename")
    file_path = os.path.join('exe', uuid, filename)
    with open(file_path, 'rb') as file:
        content = file.read()
    response = HttpResponse(content, headers={
        'Content-Type': 'application/vnd.microsoft.portable-executable',
        'Content-Disposition': f'attachment; filename="{filename}"'
    })
    return response


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
