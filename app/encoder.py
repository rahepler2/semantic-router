"""
Azure OpenAI encoder factory.

Supports three auth methods (checked in order):
  1. Managed Identity / Entra ID token provider  (AZURE_USE_MANAGED_IDENTITY=true)
  2. Entra ID static token                       (AZURE_AD_TOKEN env var)
  3. API key                                     (AZURE_OPENAI_API_KEY env var)
"""

import os
from typing import Optional

from semantic_router.encoders import AzureOpenAIEncoder
from semantic_router.utils.logger import logger


def _get_token_provider():
    """Return an azure-identity DefaultAzureCredential token provider."""
    try:
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider
    except ImportError:
        raise ImportError(
            "azure-identity is required for managed identity auth. "
            "Install with: pip install azure-identity"
        )
    credential = DefaultAzureCredential()
    return get_bearer_token_provider(
        credential, "https://cognitiveservices.azure.com/.default"
    )


def build_encoder() -> AzureOpenAIEncoder:
    """Build an AzureOpenAIEncoder from environment variables."""

    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    if not endpoint:
        raise EnvironmentError("AZURE_OPENAI_ENDPOINT is required.")

    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01")
    deployment = os.environ.get("AZURE_EMBEDDING_DEPLOYMENT", "text-embedding-ada-002")

    # ── Auth strategy ────────────────────────────────────────────────────
    use_managed_identity = (
        os.environ.get("AZURE_USE_MANAGED_IDENTITY", "").lower() == "true"
    )
    ad_token: Optional[str] = os.environ.get("AZURE_AD_TOKEN")
    api_key: Optional[str] = os.environ.get("AZURE_OPENAI_API_KEY")

    if use_managed_identity:
        logger.info("Using Managed Identity / DefaultAzureCredential for auth.")
        return AzureOpenAIEncoder(
            azure_endpoint=endpoint,
            api_version=api_version,
            deployment_name=deployment,
            azure_ad_token_provider=_get_token_provider(),
        )
    elif ad_token:
        logger.info("Using static Entra ID token for auth.")
        return AzureOpenAIEncoder(
            azure_endpoint=endpoint,
            api_version=api_version,
            deployment_name=deployment,
            azure_ad_token=ad_token,
        )
    elif api_key:
        logger.info("Using API key for auth.")
        return AzureOpenAIEncoder(
            azure_endpoint=endpoint,
            api_version=api_version,
            deployment_name=deployment,
            api_key=api_key,
        )
    else:
        raise EnvironmentError(
            "No Azure OpenAI auth configured. Set one of: "
            "AZURE_USE_MANAGED_IDENTITY=true, AZURE_AD_TOKEN, or AZURE_OPENAI_API_KEY."
        )
