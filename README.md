# Semantic Router Service

A FastAPI service wrapping [aurelio-labs/semantic-router](https://github.com/aurelio-labs/semantic-router) for intent classification. Uses Azure OpenAI embeddings and Typesense as the persistent vector index. Designed for Azure Container Apps with KEDA autoscaling.

## Architecture

Incoming text queries are classified into semantic routes (intents) using cosine similarity against pre-embedded utterances. The service sits in front of your LLM or agent stack and makes sub-millisecond routing decisions without consuming an LLM call.

```
Client → FastAPI (/route) → SemanticRouter → AzureOpenAIEncoder → Typesense (vector search) → matched route
```

**Key components:**

- **Azure OpenAI** — generates embeddings via `text-embedding-ada-002` (or any deployed model)
- **Typesense** — persistent vector index shared across replicas; survives restarts
- **KEDA** — built into Azure Container Apps; scales replicas on HTTP concurrency
- **Managed Identity** — authenticates to Azure OpenAI via Entra ID; no API keys in production

## Project Structure

```
├── main.py                        # FastAPI entrypoint (uvicorn main:app)
├── app/
│   ├── __init__.py
│   ├── encoder.py                 # Azure OpenAI encoder factory (API key / Entra ID / managed identity)
│   ├── routes.py                  # Semantic route definitions — add your intents here
│   ├── models.py                  # Pydantic response schemas
│   └── typesense_index.py         # Custom BaseIndex subclass for Typesense vector search
├── infra/
│   ├── main.bicep                 # Provisions ACR, ACA environment, container app, RBAC
│   └── deploy-server.yaml         # Build, push, and deploy steps
├── Dockerfile
├── docker-compose.yml             # Local dev: Typesense + service
├── requirements.txt
└── .env.example                   # All environment variables documented
```

## Quick Start (Local)

1. Copy the environment template and fill in your Azure OpenAI credentials:

   ```bash
   cp .env.example .env
   # edit .env with your values
   ```

2. Start Typesense and the service:

   ```bash
   docker compose up --build
   ```

3. Test it:

   ```bash
   # Health check
   curl http://localhost:8000/healthz

   # Classify a query
   curl "http://localhost:8000/route?query=how+do+I+reset+my+password"

   # Batch classify
   curl -X POST http://localhost:8000/route \
     -H "Content-Type: application/json" \
     -d '["what are your pricing plans", "the weather is nice today"]'
   ```

## Deploy to Azure

The `infra/` folder contains everything needed to deploy to Azure Container Apps.

1. **Deploy infrastructure** (creates ACR, ACA environment, container app):

   ```bash
   az deployment group create \
     --resource-group <rg> \
     --template-file infra/main.bicep \
     --parameters \
       environmentName=prod \
       azureOpenAiEndpoint='https://<resource>.openai.azure.com' \
       typesenseHost='<host>' \
       typesenseApiKey='<key>'
   ```

2. **Build and push** the container image:

   ```bash
   ACR_NAME=$(az deployment group show \
     --resource-group <rg> --name main \
     --query 'properties.outputs.acrName.value' -o tsv)

   az acr build --registry $ACR_NAME --image semantic-router:latest .
   ```

3. **Update the container app** to use the new image:

   ```bash
   az containerapp update \
     --name semantic-router-prod \
     --resource-group <rg> \
     --image ${ACR_NAME}.azurecr.io/semantic-router:latest
   ```

See `infra/deploy-server.yaml` for the full step-by-step reference.

## Authentication

The encoder supports three auth methods, checked in order:

| Method | Env Var | Use Case |
|---|---|---|
| Managed Identity | `AZURE_USE_MANAGED_IDENTITY=true` | Production on ACA (recommended) |
| Entra ID token | `AZURE_AD_TOKEN` | Service-to-service with static token |
| API key | `AZURE_OPENAI_API_KEY` | Local development |

When using managed identity, grant the container app's identity the **Cognitive Services OpenAI User** role on your Azure OpenAI resource.

## Health Probes

| Endpoint | Probe Type | Purpose |
|---|---|---|
| `GET /healthz` | Liveness | Is the process alive? ACA restarts the container if this fails. |
| `GET /readyz` | Readiness | Is the router initialized? ACA stops routing traffic until this passes. |
| `GET /startupz` | Startup | Has init finished? Gives cold starts time before liveness checks begin. |

## Adding Routes

Edit `app/routes.py` to add, remove, or modify intents. Each route is a name and a list of example utterances:

```python
new_route = Route(
    name="returns",
    utterances=[
        "I want to return my order",
        "how do I send something back",
        "what is your return policy",
    ],
)
```

On the next deployment, `auto_sync="local"` will detect the change and only re-embed the new/modified routes into Typesense.

## Scaling

KEDA HTTP scaling is configured in the Bicep template. The defaults are:

- **Min replicas:** 1 (always warm)
- **Max replicas:** 10
- **Scale trigger:** 50 concurrent HTTP requests per replica

Adjust via Bicep parameters `minReplicas`, `maxReplicas`, and `httpConcurrency`.

## Environment Variables

See `.env.example` for the full list. All configuration is via environment variables — no config files to mount.
