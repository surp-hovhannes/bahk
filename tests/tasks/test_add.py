import pytest
from hub.tasks import add

@pytest.mark.django_db
def test_add_task():
    result = add.delay(4, 6)
    assert result.get(timeout=10) == 10