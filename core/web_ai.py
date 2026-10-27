from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_POST

from core.forms import PersonalAIForm
from core.services import ai_credentials, recognition


@login_required
@never_cache
@sensitive_post_parameters("api_key")
@sensitive_variables("config", "form")
def settings_page(request):
    config = ai_credentials.owned_configuration(request.user)
    initial = {
        "mode": config.mode if config else "manual",
        "api_url": config.api_url if config else "",
        "model": config.model if config else "",
        "revision": config.revision if config else None,
    }
    form = PersonalAIForm(request.POST if request.method == "POST" else None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            ai_credentials.save_configuration(
                request.user,
                mode=form.cleaned_data["mode"],
                api_url=form.cleaned_data["api_url"],
                model=form.cleaned_data["model"],
                api_key=form.cleaned_data["api_key"],
                expected_revision=str(form.cleaned_data["revision"] or ""),
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "个人 AI 设置已保存；旧的排队任务已停止，请按需重新发起。")
            return redirect("personal-ai")
    return render(
        request,
        "core/personal_ai.html",
        {
            "form": form,
            "has_key": bool(config and config.encrypted_api_key),
            "vision_status": recognition.configuration_status(request.user),
            "encryption_ready": ai_credentials.encryption_ready(),
            "allowed_hosts": settings.AI_ALLOWED_HOSTS,
        },
        status=400 if request.method == "POST" else 200,
    )


@login_required
@require_POST
@never_cache
def delete(request):
    ai_credentials.delete_configuration(request.user)
    messages.success(request, "个人 AI 配置及密钥已删除，未完成任务已停止。已发送的请求无法撤回。")
    return redirect("personal-ai")


@login_required
@require_POST
@never_cache
def test_connection(request):
    try:
        result = recognition.call_vision(None, "menu", user=request.user)
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise ValidationError("接口有响应但格式校验未通过，请核对协议和模型")
    except ValidationError as exc:
        messages.error(request, "；".join(exc.messages))
    except Exception:
        messages.error(request, "连接测试未成功，请检查个人配置")
    else:
        messages.success(request, "连接测试成功；图片识别效果仍需用实际图片验证。")
    return redirect("personal-ai")
