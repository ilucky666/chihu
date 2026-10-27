import base64

import pytest

from core.services import ai_credentials


@pytest.fixture(autouse=True)
def test_ai_encryption(settings):
    # Public test key, never a deployment credential.
    settings.AI_ENCRYPTION_KEYS = base64.urlsafe_b64encode(b"A" * 32).decode()


@pytest.fixture
def personal_ai():
    def create(user, *, mode="responses", key="test-personal-key", url=None, model="image-model"):
        return ai_credentials.save_configuration(
            user,
            mode=mode,
            api_url=url
            or "https://api.openai.com/v1/"
            + ("responses" if mode == "responses" else "chat/completions"),
            model=model,
            api_key=key,
        )

    return create
