import importlib
from types import SimpleNamespace
from unittest.mock import patch
import pytest

import email_service


@pytest.fixture
def unpatched_email_service():
    """Reload email_service to bypass conftest autouse mock for email testing."""
    importlib.reload(email_service)
    yield email_service
    # Re-apply dummy mocks to protect subsequent test files
    email_service.send_vet_registration_email = lambda *a, **k: None
    email_service.send_vet_decision_email = lambda *a, **k: None


def test_send_vet_registration_email_escapes_xss_in_clinic_name(unpatched_email_service):
    vet = SimpleNamespace(
        username="dr_jane",
        email="jane@example.com",
        license_number="VET-98765",
        clinic_name='<a href="https://evil.com">x</a>',
    )
    with patch("resend.Emails.send") as mock_send:
        unpatched_email_service.send_vet_registration_email(vet)

    mock_send.assert_called_once()
    payload = mock_send.call_args[0][0]
    html_content = payload["html"]

    assert '&lt;a href=&quot;https://evil.com&quot;&gt;x&lt;/a&gt;' in html_content
    assert '<a href="https://evil.com">' not in html_content


def test_send_vet_registration_email_escapes_all_user_fields(unpatched_email_service):
    vet = SimpleNamespace(
        username="<b>Doc</b>",
        email="test<script>@example.com",
        license_number="<script>alert(1)</script>",
        clinic_name="Paws & Claws <img src=x onerror=alert(1)>",
    )
    with patch("resend.Emails.send") as mock_send:
        unpatched_email_service.send_vet_registration_email(vet)

    mock_send.assert_called_once()
    html_content = mock_send.call_args[0][0]["html"]

    assert "<b>Doc</b>" not in html_content
    assert "&lt;b&gt;Doc&lt;/b&gt;" in html_content
    assert "<script>" not in html_content
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_content
    assert "<img" not in html_content
    assert "&lt;img src=x onerror=alert(1)&gt;" in html_content


def test_send_vet_decision_email_escapes_username(unpatched_email_service):
    vet = SimpleNamespace(
        username="<script>alert('pwn')</script>",
        email="vet@example.com",
    )
    with patch("resend.Emails.send") as mock_send:
        unpatched_email_service.send_vet_decision_email(vet, approved=True)

    mock_send.assert_called_once()
    html_content = mock_send.call_args[0][0]["html"]

    assert "<script>" not in html_content
    assert "&lt;script&gt;alert(&#x27;pwn&#x27;)&lt;/script&gt;" in html_content


def test_send_ngo_notification_email_does_not_log_email_in_plaintext(unpatched_email_service, caplog):
    ngo = SimpleNamespace(id=99, name="Safe Paws", email="sensitive-ngo@example.org")
    with patch("resend.Emails.send", side_effect=RuntimeError("Resend API failed")):
        with caplog.at_level("ERROR"):
            result = unpatched_email_service.send_ngo_notification_email(ngo, "Need help with a dog")

    assert result is False
    assert "sensitive-ngo@example.org" not in caplog.text
    assert "NGO 99" in caplog.text
