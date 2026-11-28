import json
import logging
import re
from django.http import JsonResponse
from django.conf import settings as _settings
from .forms import HEX_COLOR_RE
from .views import (
    generate_custom_client, get_full_url, _get_run_status, _platform_label,
    _new_batch_id, _save_batch, _config_problems,
)

logger = logging.getLogger(__name__)


# Field validation constraints (mirrored from GenerateForm)
PLATFORM_CHOICES = ['windows', 'windows-x86', 'linux', 'android', 'macos']
VERSION_CHOICES = ['master', '1.5.3', '1.5.1', '1.5.0', '1.4.9', '1.4.8', '1.4.7', '1.4.6', '1.4.5', '1.4.4', '1.4.3', '1.4.2', '1.4.1', '1.4.0']
DIRECTION_CHOICES = ['incoming', 'outgoing', 'both']
INSTALLATION_CHOICES = ['installationY', 'installationN']
SETTINGS_CHOICES = ['settingsY', 'settingsN']
THEME_CHOICES = ['light', 'dark', 'system']
THEME_DORO_CHOICES = ['default', 'override']
PASS_APPROVE_MODE_CHOICES = ['password', 'click', 'password-click']
PERMISSIONS_TYPE_CHOICES = ['custom', 'full', 'view']

# Boolean fields
BOOL_FIELDS = [
    'delayFix', 'xOffline', 'hidecm', 'removeNewVersionNotif',
    'denyLan', 'enableDirectIP', 'autoClose',
    'enableKeyboard', 'enableClipboard', 'enableFileTransfer', 'enableAudio',
    'enableTCP', 'enableRemoteRestart', 'enableRecording', 'enableBlockingInput',
    'enableRemoteModi', 'removeWallpaper', 'enablePrinter', 'enableCamera', 'enableTerminal',
]

# Optional string fields (no validation needed, just accept as-is)
OPTIONAL_STR_FIELDS = [
    'sh_secret_field', 'serverIP', 'serverPort', 'key', 'apiServer', 'urlLink', 'downloadLink',
    'appname', 'displayname', 'compname', 'androidappid', 'permanentPassword',
    'defaultManual', 'overrideManual',
    'iconbase64', 'logobase64', 'privacybase64', 'logo_size',
    'app_version',
    'themeColor', 'themeSurfaceLight', 'themeSurfaceDark', 'themeMeColor',
]


def _failure_reason(failure):
    """把单个平台的失败记录拼成一句可读说明（响应与日志共用）。

    generate_custom_client() 会把 GitHub 的 HTTP 状态码/响应体、签名同步失败原因
    放在 error / detail 里；这里统一拼接并限长，避免把超长响应体整段吐给调用方。
    """
    reason = str(failure.get('error') or '未知错误').strip()
    detail = str(failure.get('detail') or '').strip()
    if detail:
        reason += "：" + detail
    return reason[:500]


def validate_generate_params(data):
    """
    Validate JSON API parameters against the same constraints as GenerateForm.

    Returns:
        tuple: (cleaned_data dict, errors dict)
        If errors is non-empty, validation failed.
    """
    errors = {}
    cleaned = {}

    # Required string field
    exename = data.get('exename', '')
    if not exename:
        errors['exename'] = 'This field is required.'
    else:
        cleaned['exename'] = exename

    # Build-chain names must be pure ASCII: they end up in the exe name, registry keys,
    # MSI Product / install path, Runner.rc and Cargo.toml metadata. Non-ASCII makes the
    # MSI installer crash, so reject it up front instead of silently falling back.
    for field, label in (('exename', 'exename'), ('appname', 'appname')):
        value = data.get(field, '')
        if value and not all(c.isascii() for c in value):
            errors[field] = (
                f'{label} must be ASCII only (letters, digits, underscore, hyphen); '
                "non-ASCII characters break the MSI installer. "
                "Use the 'displayname' field for a non-ASCII UI name."
            )

    # Choice fields
    choice_validations = {
        'version': (VERSION_CHOICES, '1.5.3'),
        'direction': (DIRECTION_CHOICES, 'both'),
        'installation': (INSTALLATION_CHOICES, 'installationY'),
        'settings': (SETTINGS_CHOICES, 'settingsY'),
        'theme': (THEME_CHOICES, 'system'),
        'themeDorO': (THEME_DORO_CHOICES, 'default'),
        'passApproveMode': (PASS_APPROVE_MODE_CHOICES, 'password-click'),
        # permissionsDorO is no longer used: permissions are always written to
        # override-settings so they apply to existing installations.  The old
        # field is still accepted in incoming JSON and simply ignored.
        'permissionsType': (PERMISSIONS_TYPE_CHOICES, 'custom'),
    }
    for field, (choices, default) in choice_validations.items():
        value = data.get(field, default)
        if value not in choices:
            errors[field] = f'Invalid choice. Must be one of: {choices}'
        else:
            cleaned[field] = value

    # platform: now a list (multi-select). Validate every entry against the whitelist.
    plat = data.get('platform', ['windows'])
    if isinstance(plat, str):
        plat = [plat]
    if not isinstance(plat, list):
        plat = [plat] if plat else []
    invalid = [p for p in plat if p not in PLATFORM_CHOICES]
    if invalid:
        errors['platform'] = f'Invalid platform(s): {invalid}. Must be a subset of {PLATFORM_CHOICES}.'
    else:
        cleaned['platform'] = plat

    # Boolean fields
    for field in BOOL_FIELDS:
        value = data.get(field, False)
        if not isinstance(value, bool):
            errors[field] = 'Must be a boolean value.'
        else:
            cleaned[field] = value

    # Optional string fields
    for field in OPTIONAL_STR_FIELDS:
        cleaned[field] = data.get(field, '')

    # Free-text names flow into single/double-quoted bash sed scripts
    # (same rule as GenerateForm.clean_appname/clean_compname).
    for field in ('appname', 'compname'):
        value = cleaned.get(field, '')
        if isinstance(value, str) and re.search(r'[&\\|\'"$`\r\n]', value):
            errors[field] = (
                'Contains characters unsupported in build scripts '
                '(& \\ | \' " $ `, newlines).'
            )

    # Theme colors flow into Dart 0xAARRGGBB literals in the workflow patch.
    # Accept #RRGGBB/RRGGBB and store the canonical form; blank = stock.
    for field in ('themeColor', 'themeSurfaceLight', 'themeSurfaceDark', 'themeMeColor'):
        value = cleaned.get(field, '')
        if not value:
            continue
        if not isinstance(value, str) or not HEX_COLOR_RE.match(value.strip()):
            errors[field] = (
                'Must be a 6-digit hex color such as #0071FF, or blank for the '
                'stock RustDesk color.'
            )
        else:
            cleaned[field] = '#' + value.strip().lstrip('#').upper()

    # File fields are not used in API mode (base64 fields are used instead)
    cleaned['iconfile'] = None
    cleaned['logofile'] = None
    cleaned['privacyfile'] = None

    return cleaned, errors


def api_generate(request):
    """
    POST /api/generate

    Accepts a JSON body with client configuration parameters and triggers
    the custom client generation process via GitHub Actions.

    Returns JSON with success status, uuid, filename, platform, and log_url.
    On failure returns 500 with error / message / failures (per-platform reason)
    / config_problems; a partially successful multi-platform request carries
    partial=true and the same failures list.
    """
    if request.method != 'POST':
        return JsonResponse({"success": False, "error": "Method not allowed. Use POST."}, status=405)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError) as e:
        return JsonResponse({"success": False, "error": f"Invalid JSON: {str(e)}"}, status=400)

    cleaned, errors = validate_generate_params(data)
    if errors:
        return JsonResponse({
            "success": False,
            "error": "Validation errors",
            "details": errors
        }, status=400)

    # Build full_url the same way as generator_view
    full_url = get_full_url(request)

    platforms = cleaned.get('platform') or ['windows']
    builds = []
    failures = []
    for p in platforms:
        single = dict(cleaned)
        single['platform'] = p
        try:
            res = generate_custom_client(single, full_url)
        except Exception as e:
            # 派发函数内部未捕获的异常：完整堆栈写进容器日志，对外只给类型+摘要，
            # 不能让 /api/generate 退化成一页 HTML 500。
            logger.exception("平台 %s 派发时发生未处理异常", p)
            failures.append({
                "platform": p,
                "platform_label": _platform_label(p),
                "error": "派发过程发生未处理异常",
                "detail": "%s: %s" % (type(e).__name__, str(e)[:300]),
            })
            continue
        if res.get('success'):
            res['status_url'] = f"/api/status?uuid={res['uuid']}&platform={res['platform']}&filename={res['filename']}"
            res['platform_label'] = _platform_label(res['platform'])
            builds.append(res)
        else:
            # 不再丢掉 generate_custom_client 返回的 error / detail：
            # 容器日志和 JSON 响应都要留下可排查的原因。
            failure = {
                "platform": p,
                "platform_label": _platform_label(p),
                "error": str(res.get('error') or '未知错误'),
                "detail": str(res.get('detail') or ''),
            }
            logger.error("平台 %s 构建派发失败：%s", p, _failure_reason(failure))
            failures.append(failure)

    if not builds:
        config_problems = [m for _, m in _config_problems()]
        reasons = [
            "%s：%s" % (f['platform_label'], _failure_reason(f))
            for f in failures[:5]
        ]
        message = "所有平台构建均失败，请检查配置后重试。"
        if reasons:
            message += " 原因：" + "；".join(reasons)
        if config_problems:
            message += " 另：" + "；".join(config_problems)
        return JsonResponse({
            "success": False,
            # 保留原有汇总文案，避免老调用方按字符串判断时失效；细节在下面三个字段。
            "error": "所有平台构建均失败",
            "message": message,
            "failures": failures,
            "config_problems": config_problems,
        }, status=500, json_dumps_params={"ensure_ascii": False})
    # 记录批次，便于通过 /builds/<id> 回看（与 web 表单一致）
    import datetime
    batch_id = _new_batch_id()
    _save_batch({
        'batch_id': batch_id,
        'version': cleaned.get('version') or '1.5.3',
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
    if len(builds) == 1:
        r = dict(builds[0])
        r['batch_url'] = f'/builds/{batch_id}'
        # 部分平台失败时显式报出，不能因为有一个成功就静默忽略其余失败。
        if failures:
            r['partial'] = True
            r['failures'] = failures
        return JsonResponse(r, json_dumps_params={"ensure_ascii": False})
    result = {"success": True, "multi": True, "batch_url": f'/builds/{batch_id}', "builds": builds}
    if failures:
        result['partial'] = True
        result['failures'] = failures
    return JsonResponse(result, json_dumps_params={"ensure_ascii": False})


def api_status(request):
    """
    GET /api/status

    Checks the generation status for a given UUID.
    Returns JSON with status, uuid, and optional log_url/filename/platform.
    """
    if request.method != 'GET':
        return JsonResponse({"success": False, "error": "Method not allowed. Use GET."}, status=405)

    uuid_val = request.GET.get('uuid')
    if not uuid_val:
        return JsonResponse({"error": "Missing required parameter: uuid"}, status=400)

    filename = request.GET.get('filename', '')
    platform = request.GET.get('platform', '')

    result = _get_run_status(uuid_val)

    if not result['found']:
        return JsonResponse({"error": "Run not found"}, status=404)

    response_data = {
        "status": result['status'],
        "uuid": uuid_val,
        "log_url": result['github_log_url'],
    }
    if filename:
        response_data['filename'] = filename
    if platform:
        response_data['platform'] = platform

    return JsonResponse(response_data)
