import os
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

from azure.core.exceptions import ResourceNotFoundError, ClientAuthenticationError
from app.keyvault import get_secrets, SecretLoadError, _get_vault_name

FAKE_SECRET_VALUE = "fake-secret-value-never-log-this"
TEST_VAULT_URL = "https://kv-test.vault.azure.net/"

@pytest.fixture
def mock_env():
    with patch.dict(os.environ, {
        "KEY_VAULT_URL": TEST_VAULT_URL,
        "APP_ENV": "local",
        "MY_SECRET": "env-fallback-value"
    }, clear=True):
        yield

@pytest.fixture
def mock_client():
    with patch("app.keyvault._build_client") as mock:
        client = MagicMock()
        mock.return_value = client
        yield client

def test_1_vault_returns_all_secrets(mock_env, mock_client, caplog):
    import logging
    caplog.set_level(logging.INFO)
    mock_secret = MagicMock()
    mock_secret.value = FAKE_SECRET_VALUE
    mock_secret.expires_on = None
    mock_client.get_secret.return_value = mock_secret
    
    secrets = get_secrets(["MY_SECRET"])
    
    assert secrets["MY_SECRET"] == FAKE_SECRET_VALUE
    assert "key vault" in caplog.text
    assert FAKE_SECRET_VALUE not in caplog.text

def test_2_local_fallback_off_unreachable(mock_env, mock_client, caplog):
    mock_client.get_secret.side_effect = ClientAuthenticationError(message="Not logged in")
    
    with pytest.raises(SecretLoadError) as exc:
        get_secrets(["MY_SECRET"])
        
    assert "Access denied" in str(exc.value)
    assert FAKE_SECRET_VALUE not in str(exc.value)
    assert "env-fallback-value" not in str(exc.value)

def test_3_local_unset_url(mock_env):
    del os.environ["KEY_VAULT_URL"]
    
    with pytest.raises(ValueError, match="KEY_VAULT_URL environment variable is required."):
        get_secrets(["MY_SECRET"])

def test_5_prod_vault_failure_raises(mock_env, mock_client):
    os.environ["APP_ENV"] = "prod"
    mock_client.get_secret.side_effect = ClientAuthenticationError(message="Not logged in")
    
    with pytest.raises(SecretLoadError) as exc:
        get_secrets(["MY_SECRET"])
        
    assert "Access denied" in str(exc.value)

def test_6_prod_unset_url(mock_env):
    os.environ["APP_ENV"] = "prod"
    del os.environ["KEY_VAULT_URL"]
    
    with pytest.raises(ValueError, match="KEY_VAULT_URL environment variable is required."):
        get_secrets(["MY_SECRET"])

def test_7_expiring_secret_warning(mock_env, mock_client, caplog):
    mock_secret = MagicMock()
    mock_secret.value = FAKE_SECRET_VALUE
    # Expires in 10 days
    mock_secret.expires_on = datetime.now(timezone.utc) + timedelta(days=10)
    mock_client.get_secret.return_value = mock_secret
    
    secrets = get_secrets(["MY_SECRET"])
    
    assert secrets["MY_SECRET"] == FAKE_SECRET_VALUE
    assert "will expire in" in caplog.text
    assert FAKE_SECRET_VALUE not in caplog.text

def test_8_setting_to_vault_name():
    assert _get_vault_name("SHAREPOINT_CLIENT_SECRET") == "SHAREPOINT-CLIENT-SECRET"
    assert _get_vault_name("DATABASE_URL") == "DATABASE-URL"

def test_9_no_fake_values_in_logs_or_exceptions(mock_env, mock_client, caplog):
    mock_secret = MagicMock()
    mock_secret.value = FAKE_SECRET_VALUE
    mock_secret.expires_on = None
    mock_client.get_secret.return_value = mock_secret
    
    secrets = get_secrets(["MY_SECRET"])
    assert secrets["MY_SECRET"] == FAKE_SECRET_VALUE
    
    assert FAKE_SECRET_VALUE not in caplog.text
