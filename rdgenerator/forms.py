from django import forms
from PIL import Image

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
            'downloadLink': 'https://rustdesk.com/download',
            'compname': 'Purslane Ltd',
        }
        for field_name, placeholder in placeholders.items():
            if field_name in self.fields:
                self.fields[field_name].widget.attrs['placeholder'] = placeholder
        # ========== 新增结束 ==========

    #Platform
    sh_secret_field = forms.CharField(required=False)
    platform = forms.ChoiceField(choices=[('windows','Windows 64Bit'),('windows-x86','Windows 32Bit'),('linux','Linux'),('android','Android'),('macos','macOS')], initial='windows')
    version = forms.ChoiceField(choices=[('master','nightly'),('1.4.8','1.4.8'),('1.4.7','1.4.7'),('1.4.6','1.4.6'),('1.4.5','1.4.5'),('1.4.4','1.4.4'),('1.4.3','1.4.3'),('1.4.2','1.4.2'),('1.4.1','1.4.1'),('1.4.0','1.4.0'),('1.3.9','1.3.9'),('1.3.8','1.3.8'),('1.3.7','1.3.7'),('1.3.6','1.3.6'),('1.3.5','1.3.5'),('1.3.4','1.3.4'),('1.3.3','1.3.3')], initial='1.4.8')
    help_text="'master' is the development version (nightly build) with the latest features but may be less stable"
    delayFix = forms.BooleanField(initial=True, required=False)

    #General
    exename = forms.CharField(label="Name for EXE file", required=True)
    appname = forms.CharField(label="Custom App Name", required=False)
    direction = forms.ChoiceField(widget=forms.RadioSelect, choices=[
        ('incoming', '仅被控（只接受连接）'),
        ('outgoing', '仅主控（只发起连接）'),
        ('both', '双向（主控+被控）')
    ], initial='both')
    installation = forms.ChoiceField(label="Disable Installation", choices=[
        ('installationY', '否，启用安装'),
        ('installationN', '是, 禁用安装')
    ], initial='installationY')
    androidappid = forms.CharField(label="Custom Android App ID (replaces 'com.carriez.flutter_hbb')", required=False)

    #Custom Server
    serverIP = forms.CharField(label="Host", required=False)
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
    cycleMonitor = forms.BooleanField(initial=False, required=False)
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
