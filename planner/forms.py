"""Server-validated forms; frontend validation is only a convenience."""
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from .models import Trip


class RegisterForm(UserCreationForm):
    email = forms.EmailField(label="邮箱（选填）", required=False, max_length=254, widget=forms.EmailInput(attrs={"autocomplete": "email", "placeholder": "方便日后联系，非必填"}))

    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ("username", "email", "password1", "password2")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "用户名"
        self.fields["username"].help_text = "最多 30 个字符，可使用字母、数字、中文和 @ . + - _。"
        self.fields["username"].max_length = 30
        self.fields["username"].widget.attrs.update({"autocomplete": "username", "placeholder": "给自己取个名字", "maxlength": 30})
        self.fields["password1"].label = "密码"
        self.fields["password2"].label = "确认密码"
        for name in ("password1", "password2"):
            self.fields[name].widget.attrs["autocomplete"] = "new-password"

    def clean_username(self):
        username = super().clean_username()
        if username is None:  # Django already attached the duplicate-name error.
            return None
        username = username.strip()
        if len(username) > 30:
            raise forms.ValidationError("用户名最多 30 个字符。")
        if get_user_model().objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("这个用户名已被使用，请换一个。")
        return username


class LoginForm(AuthenticationForm):
    username = forms.CharField(label="用户名", max_length=150, widget=forms.TextInput(attrs={"autocomplete": "username", "autofocus": True}))
    password = forms.CharField(label="密码", strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))
    error_messages = {"invalid_login": "用户名或密码不正确，请检查后重试。", "inactive": "此账号暂不可用。"}


class TripCreateForm(forms.ModelForm):
    class Meta:
        model = Trip
        fields = ("title", "description", "start_date", "days", "travel_mode", "budget", "ev_range_km")
        widgets = {"start_date": forms.DateInput(attrs={"type": "date"}), "description": forms.Textarea(attrs={"rows": 3})}

    def clean_title(self):
        title = self.cleaned_data["title"].strip()
        if not title:
            raise forms.ValidationError("请给行程取个名字。")
        return title


class ImportTripForm(forms.Form):
    file = forms.FileField(label="选择路线文件", help_text="支持本站 JSON 和 GPX 文件，最多 2 MB。", widget=forms.ClearableFileInput(attrs={"accept": ".json,.gpx"}))

    def clean_file(self):
        file = self.cleaned_data["file"]
        if file.size > 2 * 1024 * 1024:
            raise forms.ValidationError("文件不能超过 2 MB。")
        if file.name.rsplit(".", 1)[-1].lower() not in ("json", "gpx"):
            raise forms.ValidationError("请选择 JSON 或 GPX 文件。")
        return file
