from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.shortcuts import redirect, render
from django.urls import path, reverse

from core import models
from core.forms import ImportUsersForm
from core.services import accounts


@admin.register(models.User)
class UserAdmin(DjangoUserAdmin):
    fieldsets = DjangoUserAdmin.fieldsets + (("社团资料", {"fields": ("display_name",)}),)
    list_display = ("username", "display_name", "is_staff", "is_active")
    change_list_template = "admin/core/user/change_list.html"

    def get_urls(self):
        return [
            path(
                "import/", self.admin_site.admin_view(self.import_accounts), name="core_user_import"
            )
        ] + super().get_urls()

    def import_accounts(self, request):
        if not self.has_add_permission(request) or not self.has_change_permission(request):
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied
        form = ImportUsersForm(request.POST or None, request.FILES or None)
        if request.method == "POST" and form.is_valid():
            try:
                uploaded = form.cleaned_data["file"]
                if uploaded.size > 1024 * 1024:
                    raise ValidationError("CSV 文件不得超过 1MB")
                result = accounts.import_users(
                    uploaded.read(),
                    update_existing=form.cleaned_data["update_existing"],
                    dry_run=form.cleaned_data["dry_run"],
                )
            except (ValidationError, IntegrityError) as exc:
                form.add_error(
                    None,
                    "账号已被其他操作创建，请重新检查" if isinstance(exc, IntegrityError) else exc,
                )
            else:
                self.message_user(
                    request,
                    f"{'检查通过，尚未写入；取消仅检查后重新提交即可导入' if result['dry_run'] else '导入完成'}：新增 {result['created']}，更新 {result['updated']}",
                )
                return redirect(reverse("admin:core_user_import"))
        return render(
            request,
            "admin/core/user/import.html",
            {
                **self.admin_site.each_context(request),
                "title": "批量导入社团账号",
                "form": form,
                "opts": self.model._meta,
            },
        )


@admin.register(models.VisionConfiguration)
class VisionConfigurationAdmin(admin.ModelAdmin):
    fields = ("mode", "api_url", "model", "key_status")
    readonly_fields = ("key_status",)

    @admin.display(description="API 密钥")
    def key_status(self, obj):
        import os

        return (
            "已通过部署环境配置（不显示密钥）"
            if os.getenv("EATFUL_VISION_API_KEY")
            else "尚未配置；请由技术部设置 EATFUL_VISION_API_KEY"
        )

    def has_add_permission(self, request):
        return super().has_add_permission(request) and not self.model.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(models.Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "organization", "created_by", "created_at")
    search_fields = ("title",)


@admin.register(models.Visit)
class VisitAdmin(admin.ModelAdmin):
    list_display = ("title", "venue", "project", "status", "scheduled_at")
    list_filter = ("status",)


@admin.register(models.Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("kind", "status", "attempts", "available_at", "last_error")
    list_filter = ("kind", "status")
    readonly_fields = ("key", "payload", "result")


@admin.register(models.Revision, models.AuditEvent, models.ExportRun)
class HistoryAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


for model in (
    models.Organization,
    models.ExternalIdentity,
    models.ProjectMember,
    models.Invitation,
    models.Venue,
    models.TimeOption,
    models.Vote,
    models.Attendance,
    models.Asset,
    models.MenuSource,
    models.MenuCandidate,
    models.PaymentCandidate,
    models.ChoiceGroup,
    models.Dish,
    models.Assignment,
    models.ReviewDocument,
    models.Comment,
    models.Suggestion,
    models.Order,
    models.OrderLine,
    models.Adjustment,
    models.Payment,
    models.Refund,
    models.PaymentAllocation,
    models.EvidenceLink,
    models.ExpenseClaim,
    models.Notification,
):
    admin.site.register(model)
