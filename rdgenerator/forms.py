import re

from django import forms
from PIL import Image

# App/company names are interpolated into single/double-quoted bash sed
# scripts in every generator workflow: & \ | corrupt the substitution
# silently, ' " $ ` break shell quoting, CR/LF break sed addressing.
UNSAFE_NAME_CHARS = re.compile(r'[&\\|\'"$`\r\n]')

class GenerateForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # 为所有字段添加 form-control 或 form-check-input 类
        for field_name, field in self.fields.items():
            if isinstance(field.widget, (forms.TextInput, forms.EmailInput, forms.URLInput, forms.NumberInput, forms.Textarea, forms.Select)):
                if 'class' in field.widget.attrs:
                    field.widget.attrs['class'] += ' form-control'
                else:
                    field.widget.attrs['class'] = 'form-control'
            elif isinstance(field.widget, forms.PasswordInput):
                if 'class' in field.widget.attrs:
                    field.widget.attrs['class'] += ' form-control'
                else:
                    field.widget.attrs['class'] = 'form-control'
            elif isinstance(field.widget, forms.FileInput):
                if 'class' in field.widget.attrs:
                    field.widget.attrs['class'] += ' form-control'
                else:
                    field.widget.attrs['class'] = 'form-control'
            elif isinstance(field.widget, forms.CheckboxInput):
                if 'class' in field.widget.attrs:
                    field.widget.attrs['class'] += ' form-check-input'
                else:
                    field.widget.attrs['class'] = 'form-check-input'

        # ========== 新增：为服务器配置字段添加 placeholder ==========
        placeholders = {
            'serverIP': 'rs-ny.rustdesk.com',
            'key': 'OeVuKk5nlHiXp+APNn0Y3pC1Iwpwn44JGqrQCsWqmBw=',
            'apiServer': 'https://api.rustdesk.com:21114',
            'urlLink': 'https://rustdesk.com',
            'downloadLink': '留空则自动生成本工具的更新下载地址',
            'compname': 'Purslane Ltd',
        }
        for field_name, placeholder in placeholders.items():
            if field_name in self.fields:
                self.fields[field_name].widget.attrs['placeholder'] = placeholder
        # ========== 新增结束 ==========

    #Platform（多选：一次性勾选多个平台，每个平台独立构建、各自 uuid）
    sh_secret_field = forms.CharField(required=False)
    platform = forms.MultipleChoiceField(
        choices=[('windows','Windows 64Bit'),('windows-x86','Windows 32Bit'),('linux','Linux'),('android','Android'),('macos','macOS')],
        widget=forms.CheckboxSelectMultiple,
        initial=['windows']
    )
    version = forms.ChoiceField(choices=[('master','nightly'),('1.5.0','1.5.0'),('1.4.9','1.4.9'),('1.4.8','1.4.8'),('1.4.7','1.4.7'),('1.4.6','1.4.6'),('1.4.5','1.4.5'),('1.4.4','1.4.4'),('1.4.3','1.4.3'),('1.4.2','1.4.2'),('1.4.1','1.4.1'),('1.4.0','1.4.0')], initial='1.5.0')
    help_text="'master' is the development version (nightly build) with the latest features but may be less stable"
    delayFix = forms.BooleanField(initial=True, required=False)

    #General
    exename = forms.CharField(label="Name for EXE file", required=True)
    appname = forms.CharField(label="Custom App Name", required=False)
    displayname = forms.CharField(label="Display Name", required=False)
    direction = forms.ChoiceField(widget=forms.RadioSelect, choices=[
        ('incoming', '仅被控（只接受连接）'),
        ('outgoing', '仅主控（只发起连接）'),
        ('both', '双向（主控+被控）')
    ], initial='both')
    installation = forms.ChoiceField(label="Disable Installation", choices=[
        ('installationY', '否，启用安装'),
        ('installationN', '是, 禁用安装')
    ], initial='installationY')
    settings = forms.ChoiceField(label="Disable Settings", choices=[
        ('settingsY', '否，启用设置'),
        ('settingsN', '是, 禁用设置')
    ], initial='settingsY')
    androidappid = forms.CharField(label="Custom Android App ID (replaces 'com.carriez.flutter_hbb')", required=False)

    #Custom Server
    serverIP = forms.CharField(label="Host", required=False)
    serverPort = forms.CharField(label="Port", required=False)
    apiServer = forms.CharField(label="API Server", required=False)
    key = forms.CharField(label="Key", required=False)
    urlLink = forms.CharField(label="Custom URL for links", required=False)
    downloadLink = forms.CharField(label="Custom URL for downloading new versions", required=False)
    compname = forms.CharField(label="Company name",required=False)

    #Visual
    iconfile = forms.FileField(label="Custom App Icon (in .png format)", required=False, widget=forms.FileInput(attrs={'accept': 'image/png'}))
    logofile = forms.FileField(label="Custom App Logo (in .png format)", required=False, widget=forms.FileInput(attrs={'accept': 'image/png'}))
    privacyfile = forms.FileField(label="Custom privacy screen (in .png format)", required=False, widget=forms.FileInput(attrs={'accept': 'image/png'}))
    iconbase64 = forms.CharField(required=False)
    logobase64 = forms.CharField(required=False)
    privacybase64 = forms.CharField(required=False)
    theme = forms.ChoiceField(choices=[
        ('light', 'Light'),
        ('dark', 'Dark'),
        ('system', 'Follow System')
    ], initial='system')
    themeDorO = forms.ChoiceField(choices=[('default', '默认'),('override', '覆盖')], initial='default')

    #Security
    passApproveMode = forms.ChoiceField(choices=[('password','只允许密码访问'),('click','只允许点击访问'),('password-click','允许密码或点击访问')],initial='password-click')
    permanentPassword = forms.CharField(widget=forms.PasswordInput(), required=False)
    denyLan = forms.BooleanField(initial=False, required=False)
    enableDirectIP = forms.BooleanField(initial=False, required=False)
    autoClose = forms.BooleanField(initial=False, required=False)

    #Permissions
    permissionsDorO = forms.ChoiceField(choices=[('default', '默认'),('override', '覆盖')], initial='default')
    permissionsType = forms.ChoiceField(choices=[('custom', '自定义'),('full', '完全访问'),('view','仅共享屏幕')], initial='custom')
    enableKeyboard =  forms.BooleanField(initial=True, required=False)
    enableClipboard = forms.BooleanField(initial=True, required=False)
    enableFileTransfer = forms.BooleanField(initial=True, required=False)
    enableAudio = forms.BooleanField(initial=True, required=False)
    enableTCP = forms.BooleanField(initial=True, required=False)
    enableRemoteRestart = forms.BooleanField(initial=True, required=False)
    enableRecording = forms.BooleanField(initial=True, required=False)
    enableBlockingInput = forms.BooleanField(initial=True, required=False)
    enableRemoteModi = forms.BooleanField(initial=False, required=False)
    hidecm = forms.BooleanField(initial=False, required=False)
    enablePrinter = forms.BooleanField(initial=True, required=False)
    enableCamera = forms.BooleanField(initial=True, required=False)
    enableTerminal = forms.BooleanField(initial=True, required=False)

    #Advanced Settings (formerly Other Settings)
    removeWallpaper = forms.BooleanField(initial=True, required=False)
    
    # New advanced settings (default checked)
    hide_tray_icon = forms.BooleanField(initial=False, required=False, label="隐藏托盘图标")
    hideStopService = forms.BooleanField(initial=False, required=False, label="隐藏常规和托盘上的服务启停")
    disableChangePermanentPassword = forms.BooleanField(initial=False, required=False, label="禁止更改永久密码")
    enableIPv6Punch = forms.BooleanField(initial=True, required=False, label="启用 IPv6 P2P 连接")
    enableUDPPunch = forms.BooleanField(initial=True, required=False, label="启用 UDP 打洞")
    showQualityMonitor = forms.BooleanField(initial=True, required=False, label="显示质量监视器")
    
    # Conditional option (default checked)
    preElevateService = forms.BooleanField(initial=True, required=False, label="便携版自动提升权限")

    defaultManual = forms.CharField(widget=forms.Textarea, required=False)
    overrideManual = forms.CharField(widget=forms.Textarea, required=False)

    #custom added features
    xOffline = forms.BooleanField(initial=False, required=False)
    removeNewVersionNotif = forms.BooleanField(initial=False, required=False)

    # ========== 新增：界面定制功能 ==========
    hideSecuritySettings = forms.BooleanField(initial=False, required=False, label="隐藏安全设置")
    hideNetworkSettings = forms.BooleanField(initial=False, required=False, label="隐藏网络设置")
    hideRemotePrinterSettings = forms.BooleanField(initial=False, required=False, label="隐藏远程打印设置")
    hideServerSettings = forms.BooleanField(initial=False, required=False, label="隐藏服务器设置")
    hideProxySettings = forms.BooleanField(initial=False, required=False, label="隐藏代理设置")
    hideWebsocketSettings = forms.BooleanField(initial=False, required=False, label="隐藏 WebSocket 设置")
    hideAccountSettings = forms.BooleanField(initial=False, required=False, label="隐藏账户设置")
    # ========== 新增结束 ==========

    def clean_iconfile(self):
        print("checking icon")
        image = self.cleaned_data['iconfile']
        if image:
            try:
                img = Image.open(image)
                if img.format != 'PNG':
                    raise forms.ValidationError("Only PNG images are allowed.")
                width, height = img.size
                if width != height:
                    raise forms.ValidationError("Custom App Icon dimensions must be square.")
                return image
            except OSError:
                raise forms.ValidationError("Invalid icon file.")
            except Exception as e:
                raise forms.ValidationError(f"Error processing icon: {e}")

    def _reject_unsafe_name_chars(self, field):
        value = self.cleaned_data.get(field, '')
        if value and UNSAFE_NAME_CHARS.search(value):
            raise forms.ValidationError(
                "Contains characters unsupported in build scripts "
                "(& \\ | ' \" $ `, newlines)."
            )
        return value

    def _reject_non_ascii_name(self, field, label):
        """exename / appname 会进构建链（exe 名、注册表、MSI Product 与安装路径、
        Runner.rc、Cargo.toml 版本信息），非 ASCII 会让 MSI 安装器崩溃。
        views.py 里有兜底回落，但静默丢弃会让用户以为填了生效。这里直接报错。"""
        value = self.cleaned_data.get(field, '')
        if value and not all(char.isascii() for char in value):
            raise forms.ValidationError(
                f"{label} 只能使用英文字母、数字、下划线和连字符（不支持中文等非 ASCII 字符）。"
                "若想让界面显示中文，请填写下方的「界面显示名称」。"
            )
        return value

    def clean_exename(self):
        self._reject_unsafe_name_chars('exename')
        return self._reject_non_ascii_name('exename', '文件名')

    def clean_appname(self):
        self._reject_unsafe_name_chars('appname')
        return self._reject_non_ascii_name('appname', '应用名称')

    def clean_displayname(self):
        return self._reject_unsafe_name_chars('displayname')

    def clean_compname(self):
        return self._reject_unsafe_name_chars('compname')
