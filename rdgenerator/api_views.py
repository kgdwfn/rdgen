import json
import re
from django.http import JsonResponse
from django.conf import settings as _settings
from .views import generate_custom_client, _get_run_status, _platform_label


# Field validation constraints (mirrored from GenerateForm)
PLATFORM_CHOICES = ['windows', 'windows-x86', 'linux', 'android', 'macos']
VERSION_CHOICES = ['master', '1.4.9', '1.4.8', '1.4.7', '1.4.6', '1.4.5', '1.4.4', '1.4.3', '1.4.2', '1.4.1', '1.4.0']
DIRECTION_CHOICES = ['incoming', 'outgoing', 'both']
INSTALLATION_CHOICES = ['installationY', 'installationN']
SETTINGS_CHOICES = ['settingsY', 'settingsN']
THEME_CHOICES = ['light', 'dark', 'system']
THEME_DORO_CHOICES = ['default', 'override']
PASS_APPROVE_MODE_CHOICES = ['password', 'click', 'password-click']
PERMISSIONS_DORO_CHOICES = ['default', 'override']
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
]


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
        'version': (VERSION_CHOICES, '1.4.9'),
        'direction': (DIRECTION_CHOICES, 'both'),
        'installation': (INSTALLATION_CHOICES, 'installationY'),
        'settings': (SETTINGS_CHOICES, 'settingsY'),
        'theme': (THEME_CHOICES, 'system'),
        'themeDorO': (THEME_DORO_CHOICES, 'default'),
        'passApproveMode': (PASS_APPROVE_MODE_CHOICES, 'password-click'),
        'permissionsDorO': (PERMISSIONS_DORO_CHOICES, 'default'),
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
    full_url = f"{_settings.PROTOCOL}://{request.get_host()}" if _settings.GENURL else f"{_settings.PROTOCOL}://{request.get_host()}"

    platforms = cleaned.get('platform') or ['windows']
    builds = []
    for p in platforms:
        single = dict(cleaned)
        single['platform'] = p
        res = generate_custom_client(single, full_url)
        if res.get('success'):
            res['status_url'] = f"/api/status?uuid={res['uuid']}&platform={res['platform']}&filename={res['filename']}"
            res['platform_label'] = _platform_label(res['platform'])
            builds.append(res)

    if not builds:
        return JsonResponse({"success": False, "error": "所有平台构建均失败"}, status=500)
    if len(builds) == 1:
        return JsonResponse(builds[0])
    return JsonResponse({"success": True, "multi": True, "builds": builds})


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
