import hashlib
import io
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

from core.models import Asset
from core.services.access import membership

MAX_PIXELS = 40_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS
ALLOWED = {
    "JPEG": ("image/jpeg", "jpg"),
    "PNG": ("image/png", "png"),
    "WEBP": ("image/webp", "webp"),
}


def save_image(user, visit, uploaded, kind, dish=None, caption=""):
    membership(user, visit.project)
    if kind not in Asset.Kind.values:
        raise ValidationError("图片类别无效")
    if dish and dish.visit_id != visit.id:
        raise ValidationError("菜品不属于此活动")
    if kind in (Asset.Kind.PAYMENT, Asset.Kind.RECEIPT) and dish:
        raise ValidationError("财务证明不能绑定菜品")
    if uploaded.size > settings.MAX_UPLOAD_SIZE or uploaded.size < 1:
        raise ValidationError("图片大小无效")
    raw = uploaded.read()
    if len(raw) > settings.MAX_UPLOAD_SIZE:
        raise ValidationError("图片过大")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in ALLOWED or image.width * image.height > MAX_PIXELS:
                raise ValidationError("只支持限制尺寸内的 JPG、PNG、WEBP 图片")
            content_type, extension = ALLOWED[image.format]
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            image = ImageOps.exif_transpose(image)
            width, height = image.size
            image.thumbnail((960, 960))
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            thumbnail_bytes = io.BytesIO()
            image.save(thumbnail_bytes, "JPEG", quality=78, optimize=True)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValidationError("图片无法读取") from exc
    digest = hashlib.sha256(raw).hexdigest()
    if Asset.objects.filter(visit=visit, sha256=digest).exists():
        raise ValidationError("相同图片已上传，请使用现有图片关联")
    asset = Asset(
        visit=visit,
        dish=dish,
        owner=user,
        kind=kind,
        original_name=uploaded.name[:255],
        content_type=content_type,
        sha256=digest,
        size=len(raw),
        width=width,
        height=height,
        caption=caption[:255],
    )
    random_name = uuid.uuid4().hex
    asset.file.save(f"{random_name}.{extension}", ContentFile(raw), save=False)
    asset.thumbnail.save(
        f"{random_name}-preview.jpg", ContentFile(thumbnail_bytes.getvalue()), save=False
    )
    asset.save()
    return asset
