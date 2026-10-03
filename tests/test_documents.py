import pytest
from django.core.exceptions import ValidationError

from core.models import User
from core.services import documents
from core.services.dishes import create_dish
from core.services.projects import create_project, create_visit


@pytest.mark.django_db
def test_status_change_blocks_stale_save_and_restore_preserves_history():
    user = User.objects.create_user(username="doc-leader")
    project = create_project(user, "文案")
    visit = create_visit(user, project, "店")
    dish = create_dish(user, visit, "菜")
    doc = documents.document_for_dish(user, dish)
    first = documents.save_document(user, doc, "第一稿", 0)
    approved = documents.set_status(user, first, "approved")
    assert approved.revision == 2
    with pytest.raises(documents.RevisionConflict):
        documents.save_document(user, first, "旧页面修改", 1)
    final = documents.set_status(user, approved, "final")
    with pytest.raises(ValidationError):
        documents.save_document(user, final, "不能覆盖", final.revision)
    restored = documents.restore(user, final, 1, final.revision)
    assert restored.body == "第一稿"
    assert restored.revision == 4
    assert list(doc.history.values_list("number", flat=True)) == [1, 2, 3, 4]


@pytest.mark.django_db
def test_html_preview_strips_scripts_and_javascript_links():
    output = documents.preview("<script>alert(1)</script> [点我](javascript:alert(1))")
    assert "<script>" not in output
    assert 'href="javascript:' not in output
