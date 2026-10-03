import csv
import io

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction

from core.models import User


@transaction.atomic
def import_users(content, *, update_existing=False, dry_run=False):
    if len(content) > 1024 * 1024:
        raise ValidationError("CSV 文件不得超过 1MB")
    try:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")), strict=True)
        headers = reader.fieldnames or []
        if not {"username", "password"}.issubset(headers) or len(headers) != len(set(headers)):
            raise ValidationError("CSV 必须包含不重复的 username,password 列")
        if set(headers) - {"username", "password", "display_name", "email"}:
            raise ValidationError("CSV 包含不支持的列")
        rows = list(reader)
    except (UnicodeDecodeError, csv.Error) as exc:
        raise ValidationError("请上传有效的 UTF-8 CSV 文件") from exc
    if not rows or len(rows) > 200:
        raise ValidationError("每次需导入 1–200 个账号")
    prepared, seen = [], set()
    for number, row in enumerate(rows, 2):
        if None in row or any(value is None for value in row.values()):
            raise ValidationError(f"第 {number} 行的列数不正确")
        username = User.normalize_username(row["username"].strip())
        if not username or username in seen:
            raise ValidationError(f"第 {number} 行用户名为空或重复")
        seen.add(username)
        user = User.objects.select_for_update().filter(username=username).first()
        is_new = user is None
        if user and (not update_existing or user.is_staff or user.is_superuser):
            raise ValidationError(f"第 {number} 行用户名已存在或属于管理员，不能导入覆盖")
        if is_new:
            user = User(username=username)
            user.set_unusable_password()
        user.display_name = row.get("display_name", user.display_name).strip()
        user.email = row.get("email", user.email).strip()
        try:
            user.full_clean()
            validate_password(row["password"], user=user)
        except ValidationError as exc:
            raise ValidationError(f"第 {number} 行：{'；'.join(exc.messages)}") from exc
        prepared.append((user, row["password"], is_new))
    if not dry_run:
        for user, password, _ in prepared:
            user.set_password(password)
            user.save()
    return {
        "created": sum(is_new for _, _, is_new in prepared),
        "updated": sum(not is_new for _, _, is_new in prepared),
        "dry_run": dry_run,
    }
