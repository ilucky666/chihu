import uuid
from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q


def uid():
    return uuid.uuid4()


class Entity(models.Model):
    id = models.UUIDField(primary_key=True, default=uid, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uid, editable=False)
    display_name = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return self.display_name or self.username


class VisionConfiguration(models.Model):
    class Mode(models.TextChoices):
        MANUAL = "manual", "手工录入"
        RESPONSES = "responses", "Responses API"
        CHAT = "chat_completions", "Chat Completions 兼容 API"

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    mode = models.CharField("识别模式", max_length=30, choices=Mode.choices, default=Mode.RESPONSES)
    api_url = models.URLField(
        "完整 API 地址",
        max_length=500,
        blank=True,
        help_text="留空使用部署环境配置；须为 HTTPS，包含 /responses 或 /chat/completions。",
    )
    model = models.CharField(
        "模型名称",
        max_length=150,
        blank=True,
        help_text="填写服务商支持图片输入的模型名称；留空使用部署环境配置。",
    )

    class Meta:
        verbose_name = "图片识别设置"
        verbose_name_plural = "图片识别设置"
        constraints = [
            models.CheckConstraint(condition=Q(id=1), name="single_vision_configuration")
        ]

    def clean(self):
        if self.api_url and not self.api_url.startswith("https://"):
            raise ValidationError({"api_url": "识别服务必须使用 HTTPS"})
        from urllib.parse import urlparse

        parsed = urlparse(self.api_url)
        if parsed.username or parsed.password:
            raise ValidationError(
                {"api_url": "API 地址不能包含用户名或密钥，请通过部署环境保存密钥"}
            )

    def __str__(self):
        return "全站图片识别设置"


class Organization(Entity):
    name = models.CharField(max_length=150)
    slug = models.SlugField(unique=True)

    def __str__(self):
        return self.name


class ExternalIdentity(Entity):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="external_identities")
    provider = models.CharField(max_length=80)
    subject = models.CharField(max_length=255)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["provider", "subject"], name="unique_external_identity")
        ]


class Project(Entity):
    class Status(models.TextChoices):
        PREPARING = "preparing", "筹备"
        ACTIVE = "active", "进行中"
        CLOSING = "closing", "待收尾"
        ARCHIVED = "archived", "已归档"

    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="projects"
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PREPARING)
    deadline = models.DateTimeField(null=True, blank=True)
    budget = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    finance_contact = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="finance_projects"
    )
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="created_projects")

    def __str__(self):
        return self.title


class ProjectMember(Entity):
    class Role(models.TextChoices):
        LEADER = "leader", "组长"
        MEMBER = "member", "组员"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.MEMBER)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["project", "user"], name="unique_project_member")
        ]


class Invitation(Entity):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="invitations")
    token_hash = models.CharField(max_length=64, unique=True)
    role = models.CharField(
        max_length=10, choices=ProjectMember.Role.choices, default=ProjectMember.Role.MEMBER
    )
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    max_uses = models.PositiveIntegerField(default=1)
    uses = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT)


class Venue(Entity):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="venues")
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return self.name


class Visit(Entity):
    class Status(models.TextChoices):
        DRAFT = "draft", "草稿"
        POLLING = "polling", "投票中"
        CONFIRMING = "confirming", "待确认"
        SCHEDULED = "scheduled", "待到店"
        DONE = "done", "已完成"
        CANCELLED = "cancelled", "已取消"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="visits")
    venue = models.ForeignKey(Venue, on_delete=models.PROTECT, related_name="visits")
    title = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    schedule_revision = models.PositiveIntegerField(default=0)
    poll_deadline = models.DateTimeField(null=True, blank=True)
    confirmation_deadline = models.DateTimeField(null=True, blank=True)
    capacity = models.PositiveIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)

    def __str__(self):
        return self.title or f"{self.venue} · {self.project}"


class TimeOption(Entity):
    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name="time_options")
    starts_at = models.DateTimeField()
    meal = models.CharField(max_length=10, choices=[("lunch", "午餐"), ("dinner", "晚餐")])

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["visit", "starts_at"], name="unique_visit_slot")
        ]
        ordering = ["starts_at"]


class Vote(Entity):
    class Choice(models.TextChoices):
        YES = "yes", "能参加"
        MAYBE = "maybe", "待定"
        NO = "no", "不能参加"

    option = models.ForeignKey(TimeOption, on_delete=models.CASCADE, related_name="votes")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="votes")
    choice = models.CharField(max_length=8, choices=Choice.choices)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["option", "user"], name="unique_user_vote")]


class Attendance(Entity):
    class Status(models.TextChoices):
        YES = "yes", "参加"
        NO = "no", "不参加"
        WAITLIST = "waitlist", "候补"

    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name="attendance")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="attendance")
    status = models.CharField(max_length=10, choices=Status.choices)
    schedule_revision = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["visit", "user"], name="unique_visit_attendance")
        ]


class Asset(Entity):
    class Kind(models.TextChoices):
        MENU = "menu", "菜单"
        FOOD = "food", "菜品照片"
        ORDER = "order", "订单"
        PAYMENT = "payment", "付款"
        RECEIPT = "receipt", "票据"
        OTHER = "other", "其他"

    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name="assets")
    dish = models.ForeignKey(
        "Dish", on_delete=models.SET_NULL, null=True, blank=True, related_name="assets"
    )
    owner = models.ForeignKey(User, on_delete=models.PROTECT, related_name="assets")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    file = models.FileField(upload_to="private/%Y/%m/")
    thumbnail = models.FileField(upload_to="thumbnails/%Y/%m/", blank=True)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=80)
    sha256 = models.CharField(max_length=64)
    size = models.PositiveBigIntegerField()
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    caption = models.CharField(max_length=255, blank=True)
    selected = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [models.Index(fields=["visit", "kind", "sha256"])]


class MenuSource(Entity):
    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name="menu_sources")
    asset = models.OneToOneField(Asset, on_delete=models.PROTECT)
    source_type = models.CharField(
        max_length=20, choices=[("menu", "菜单"), ("order", "点单"), ("package", "套餐")]
    )
    recognition_status = models.CharField(max_length=16, default="manual")
    raw_result = models.JSONField(default=dict, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)


class MenuCandidate(Entity):
    source = models.ForeignKey(MenuSource, on_delete=models.CASCADE, related_name="candidates")
    name = models.CharField(max_length=200)
    category = models.CharField(max_length=100, blank=True)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("1.00"))
    unit = models.CharField(max_length=30, blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    price_type = models.CharField(max_length=12, default="unknown")
    selected = models.BooleanField(default=False)
    external_key = models.CharField(max_length=80, blank=True)
    parent_key = models.CharField(max_length=80, blank=True)
    choice_group_name = models.CharField(max_length=150, blank=True)
    needs_review = models.BooleanField(default=True)
    needs_photo = models.BooleanField(default=True)


class PaymentCandidate(Entity):
    asset = models.OneToOneField(Asset, on_delete=models.PROTECT, related_name="payment_candidate")
    status = models.CharField(max_length=20, default="pending")
    direction = models.CharField(max_length=12, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    merchant = models.CharField(max_length=200, blank=True)
    paid_at_text = models.CharField(max_length=100, blank=True)
    reference_hint = models.CharField(max_length=20, blank=True)
    raw_result = models.JSONField(default=dict, blank=True)
    payment = models.OneToOneField("Payment", on_delete=models.SET_NULL, null=True, blank=True)


class ChoiceGroup(Entity):
    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name="choice_groups")
    name = models.CharField(max_length=150)
    required_count = models.PositiveSmallIntegerField(default=1)


class Dish(Entity):
    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name="dishes")
    name = models.CharField(max_length=200)
    category = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("1.00"))
    unit = models.CharField(max_length=30, blank=True)
    parent = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="components"
    )
    choice_group = models.ForeignKey(
        ChoiceGroup, on_delete=models.SET_NULL, null=True, blank=True, related_name="options"
    )
    is_selected = models.BooleanField(default=True)
    needs_review = models.BooleanField(default=True)
    needs_photo = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    source_candidate = models.ForeignKey(
        MenuCandidate, on_delete=models.SET_NULL, null=True, blank=True
    )

    class Meta:
        ordering = ["sort_order", "created_at"]

    def __str__(self):
        return self.name


class Assignment(Entity):
    class Kind(models.TextChoices):
        REVIEW = "review", "测评文案"
        PHOTO = "photo", "菜品照片"

    class Status(models.TextChoices):
        OPEN = "open", "待认领"
        CLAIMED = "claimed", "进行中"
        SUBMITTED = "submitted", "待审核"
        APPROVED = "approved", "已通过"
        WAIVED = "waived", "已豁免"
        CANCELLED = "cancelled", "已取消"

    dish = models.ForeignKey(Dish, on_delete=models.CASCADE, related_name="assignments")
    kind = models.CharField(max_length=8, choices=Kind.choices)
    owner = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="assignments"
    )
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN)
    deadline = models.DateTimeField(null=True, blank=True)
    deadline_revision = models.PositiveIntegerField(default=0)
    reason = models.TextField(blank=True)
    collaborators = models.ManyToManyField(
        User, blank=True, related_name="collaborating_assignments"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["dish", "kind"], name="unique_dish_assignment_kind")
        ]


class ReviewDocument(Entity):
    class Status(models.TextChoices):
        WRITING = "writing", "撰写中"
        REVIEW = "review", "待审核"
        CHANGES = "changes", "待修改"
        APPROVED = "approved", "已通过"
        FINAL = "final", "已定稿"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="documents")
    dish = models.ForeignKey(
        Dish, on_delete=models.CASCADE, null=True, blank=True, related_name="documents"
    )
    author = models.ForeignKey(
        User, on_delete=models.PROTECT, null=True, blank=True, related_name="review_documents"
    )
    is_publication = models.BooleanField(default=True)
    title = models.CharField(max_length=200, blank=True)
    body = models.TextField(blank=True)
    revision = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.WRITING)
    generated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["dish"],
                condition=Q(is_publication=True, dish__isnull=False),
                name="unique_publication_dish",
            ),
            models.UniqueConstraint(
                fields=["project"],
                condition=Q(is_publication=True, dish__isnull=True),
                name="unique_project_document",
            ),
            models.UniqueConstraint(
                fields=["dish", "author"],
                condition=Q(is_publication=False),
                name="unique_author_dish_review",
            ),
        ]


class Revision(Entity):
    document = models.ForeignKey(ReviewDocument, on_delete=models.CASCADE, related_name="history")
    number = models.PositiveIntegerField()
    body = models.TextField()
    actor = models.ForeignKey(User, on_delete=models.PROTECT)
    reason = models.CharField(max_length=100, default="save")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["document", "number"], name="unique_doc_revision")
        ]
        ordering = ["number"]


class Comment(Entity):
    document = models.ForeignKey(ReviewDocument, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(User, on_delete=models.PROTECT)
    revision_number = models.PositiveIntegerField()
    parent = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True, related_name="replies"
    )
    body = models.TextField()
    resolved = models.BooleanField(default=False)


class Suggestion(Entity):
    document = models.ForeignKey(
        ReviewDocument, on_delete=models.CASCADE, related_name="suggestions"
    )
    author = models.ForeignKey(User, on_delete=models.PROTECT)
    base_revision = models.PositiveIntegerField()
    proposed_body = models.TextField()
    note = models.TextField(blank=True)
    status = models.CharField(max_length=12, default="open")
    decided_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="decided_suggestions"
    )


class Order(Entity):
    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name="orders")
    label = models.CharField(max_length=150)
    merchant = models.CharField(max_length=200, blank=True)
    ordered_at = models.DateTimeField(null=True, blank=True)
    expected_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)

    def __str__(self):
        return self.label


class OrderLine(Entity):
    class PriceType(models.TextChoices):
        UNIT = "unit", "单价"
        LINE = "line", "行小计"
        PACKAGE = "package", "套餐价"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="lines")
    dish = models.ForeignKey(
        Dish, on_delete=models.SET_NULL, null=True, blank=True, related_name="order_lines"
    )
    name = models.CharField(max_length=200)
    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("1.00"),
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    unit = models.CharField(max_length=30, blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    price_type = models.CharField(max_length=10, choices=PriceType.choices, default=PriceType.LINE)
    billable = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)


class Adjustment(Entity):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="adjustments")
    label = models.CharField(max_length=150)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    kind = models.CharField(max_length=20, choices=[("discount", "优惠"), ("fee", "附加费用")])


class Payment(Entity):
    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name="payments")
    payer = models.ForeignKey(User, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    method = models.CharField(max_length=80, blank=True)
    reference = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=12, default="confirmed")
    notes = models.TextField(blank=True)


class Refund(Entity):
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="refunds")
    amount = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    refunded_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)


class PaymentAllocation(Entity):
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="allocations")
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="allocations")
    amount = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["payment", "order"], name="unique_payment_order_allocation"
            )
        ]


class EvidenceLink(Entity):
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="evidence_links")
    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, null=True, blank=True, related_name="evidence_links"
    )
    payment = models.ForeignKey(
        Payment, on_delete=models.CASCADE, null=True, blank=True, related_name="evidence_links"
    )
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(order__isnull=False) | Q(payment__isnull=False),
                name="evidence_has_target",
            ),
            models.UniqueConstraint(
                fields=["asset", "order", "payment"],
                name="unique_evidence_link",
                nulls_distinct=False,
            ),
        ]


class ExpenseClaim(Entity):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="expense_claims")
    title = models.CharField(max_length=200)
    status = models.CharField(max_length=16, default="draft")
    visit_ids = models.JSONField(default=list)
    exception_notes = models.JSONField(default=dict)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT)


class ExportRun(Entity):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="exports")
    created_by = models.ForeignKey(User, on_delete=models.PROTECT)
    kind = models.CharField(max_length=16)
    status = models.CharField(max_length=12, default="pending")
    template_version = models.CharField(max_length=50, blank=True)
    scope = models.JSONField(default=dict)
    snapshot = models.JSONField(default=dict)
    file = models.FileField(upload_to="exports/%Y/%m/", blank=True)
    sha256 = models.CharField(max_length=64, blank=True)


class Notification(Entity):
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, null=True, blank=True)
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, null=True, blank=True)
    kind = models.CharField(max_length=30)
    dedupe_key = models.CharField(max_length=255, unique=True)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)


class AuditEvent(Entity):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    actor = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True)
    action = models.CharField(max_length=100)
    target_type = models.CharField(max_length=80)
    target_id = models.UUIDField()
    detail = models.JSONField(default=dict)


class Job(Entity):
    kind = models.CharField(max_length=40)
    key = models.CharField(max_length=255, unique=True)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=12, default="queued")
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=3)
    available_at = models.DateTimeField()
    lease_until = models.DateTimeField(null=True, blank=True)
    result = models.JSONField(default=dict)
    last_error = models.CharField(max_length=500, blank=True)
