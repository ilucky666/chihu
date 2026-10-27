from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "吃乎工作台"

    def ready(self):
        from core import checks  # noqa: F401
