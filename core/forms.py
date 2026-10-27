from django import forms

from core.models import PersonalVisionConfiguration, User
from core.services.ai_http import validate_endpoint


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("username", "display_name", "email")
        labels = {"username": "登录用户名", "display_name": "显示称呼", "email": "邮箱（可选）"}

    def clean_username(self):
        return User.normalize_username(self.cleaned_data["username"])


class ImportUsersForm(forms.Form):
    file = forms.FileField(
        label="账号 CSV 文件",
        help_text="UTF-8 编码，列名 username,password,display_name,email；后两列可省略。",
    )
    update_existing = forms.BooleanField(
        label="更新已有账号的资料和密码",
        required=False,
        help_text="默认遇到已有用户名会拒绝导入；更新不会修改管理员权限。",
    )
    dry_run = forms.BooleanField(label="仅检查，不写入账号", required=False, initial=True)


class PersonalAIForm(forms.Form):
    mode = forms.ChoiceField(label="识别方式", choices=PersonalVisionConfiguration.Mode.choices)
    api_url = forms.URLField(
        label="完整 API 地址", max_length=500, required=False, assume_scheme="https"
    )
    model = forms.CharField(label="图片模型名称", max_length=150, required=False)
    api_key = forms.CharField(
        label="API Key",
        max_length=4096,
        required=False,
        strip=True,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password", "spellcheck": "false"}),
        help_text="首次配置或更换地址需输入；同地址留空保留原 Key。保存后不再回显。",
    )
    revision = forms.UUIDField(required=False, widget=forms.HiddenInput)

    def clean(self):
        data = super().clean()
        if data.get("mode") and data["mode"] != "manual":
            if not data.get("api_url"):
                self.add_error("api_url", "请填写完整地址")
            else:
                try:
                    validate_endpoint(data["api_url"])
                except forms.ValidationError as exc:
                    self.add_error("api_url", exc)
            if not data.get("model"):
                self.add_error("model", "请填写图片模型名称")
        return data
