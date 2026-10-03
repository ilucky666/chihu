from django import forms

from core.models import User


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
