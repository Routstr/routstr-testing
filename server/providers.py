"""Upstream provider registry endpoint (ROU-153).

`GET /api/providers` lists the providers/*.yaml profiles as JSON so the Web
UI Run-modal can populate its "Upstream provider" dropdown and know which
masked key fields to expose. Provider API keys are never read or returned
here — only the *names* of the env vars the harness expects.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request

from runner import providers as provider_registry
from runner.cost import load_model_pricing

from .schemas import (
    ProviderModel,
    ProviderRequiredEnv,
    ProviderSummary,
)

router = APIRouter(prefix="/api/providers", tags=["providers"])


def _providers_dir(request: Request) -> Path:
    config = request.app.state.config
    explicit = getattr(config, "providers_dir", None)
    if explicit is not None:
        return Path(explicit)
    # Default: a `providers/` dir alongside the scenarios dir's parent.
    return config.scenarios_dir.parent / "providers"


def _models_for(providers_dir: Path, provider) -> list[ProviderModel]:
    if not provider.models_file:
        return []
    models_path = providers_dir.parent / provider.models_file
    pricing_keys = load_model_pricing(models_path)
    return [ProviderModel(id=mid, name=mid) for mid in pricing_keys]


@router.get("", response_model=list[ProviderSummary])
def list_providers(
    request: Request,
    providers_dir: Path = Depends(_providers_dir),
) -> list[ProviderSummary]:
    out: list[ProviderSummary] = []
    for provider in provider_registry.list_providers(providers_dir):
        out.append(
            ProviderSummary(
                id=provider.id,
                name=provider.name,
                upstream_base_url=provider.upstream_base_url,
                api_key_env=provider.api_key_env,
                required_env=[
                    ProviderRequiredEnv(
                        name=item.name,
                        secret=item.is_secret,
                        has_default=item.default is not None,
                    )
                    for item in provider.required_env
                ],
                models=_models_for(providers_dir, provider),
                notes=provider.notes.strip(),
            )
        )
    return out
