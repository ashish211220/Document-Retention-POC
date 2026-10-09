import os
import logging
from typing import Iterable, Dict
from datetime import datetime, timezone

from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from azure.core.exceptions import ResourceNotFoundError, HttpResponseError, ClientAuthenticationError

logger = logging.getLogger(__name__)

EXPIRY_WARNING_DAYS = 30


class SecretLoadError(Exception):
   
    pass


def _get_vault_name(setting_name: str) -> str:
    
    return setting_name.replace("_", "-")


def _build_client(vault_url: str) -> SecretClient:
   
    credential = DefaultAzureCredential()
    return SecretClient(vault_url=vault_url, credential=credential)


def _fetch_secret_from_vault(client: SecretClient, vault_name: str, setting_name: str) -> str:

    try:
        secret = client.get_secret(vault_name)
        if secret.value is None:
            raise SecretLoadError(f"Secret '{setting_name}' exists in vault but has no value.")
        
        if secret.properties.expires_on:
            days_remaining = (secret.properties.expires_on - datetime.now(timezone.utc)).days
            if days_remaining <= EXPIRY_WARNING_DAYS:
                logger.warning(
                    f"Secret '{setting_name}' will expire in {days_remaining} days."
                )
                
        return secret.value
    except ResourceNotFoundError:
        raise SecretLoadError(f"Secret '{setting_name}' not found in Key Vault.")
    except ClientAuthenticationError as e:
        raise SecretLoadError(
            f"Access denied or not authenticated to Key Vault for '{setting_name}'. "
            f"Run `az login`. Details: {e.message}"
        )
    except HttpResponseError as e:
        raise SecretLoadError(f"HTTP error retrieving secret '{setting_name}': {e.message}")


def get_secrets(names: Iterable[str]) -> Dict[str, str]:

  
    app_env = os.getenv("APP_ENV", "local").lower()
    vault_url = os.getenv("KEY_VAULT_URL")
    
    secrets: Dict[str, str] = {}
    sources: Dict[str, str] = {}
    
    if app_env == "test" and not vault_url:
        for name in names:
            secrets[name] = os.getenv(name, "")
            sources[name] = "test environment"
        return secrets
        
    if not vault_url:
        raise ValueError("KEY_VAULT_URL environment variable is required.")
            
    client = _build_client(vault_url)
    try:
        for name in names:
            vault_name = _get_vault_name(name)
            secrets[name] = _fetch_secret_from_vault(client, vault_name, name)
            sources[name] = "key vault"
    finally:
        client.close()
        
    logger.info(f"Loaded secrets for environment '{app_env}' from vault '{vault_url}'. Sources: {sources}")
    return secrets
