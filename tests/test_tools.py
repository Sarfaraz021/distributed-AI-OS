from core.idempotency import make_idem_key
from core.tools import email_line_count, send_email


def test_idempotency_key_is_stable():
    a = make_idem_key("run-1", 2, "send_email", {"to": "a@b.c", "body": "hi"})
    b = make_idem_key("run-1", 2, "send_email", {"body": "hi", "to": "a@b.c"})
    assert a == b
    assert a != make_idem_key("run-1", 2, "send_email", {"to": "a@b.c", "body": "bye"})


def test_send_email_writes_once_for_same_key(memory_gateway, tmp_path, monkeypatch):
    from core.config import get_settings

    monkeypatch.setenv("LLM_BACKEND", "fake")
    get_settings.cache_clear()
    settings = get_settings()
    settings.data_dir = tmp_path
    tmp_path.joinpath("emails").mkdir()

    run_id = "run-email-once"
    first = send_email("user@example.com", "hello", run_id=run_id, step_index=2)
    second = send_email("user@example.com", "hello", run_id=run_id, step_index=2)
    assert first == second
    assert email_line_count(run_id) == 1
