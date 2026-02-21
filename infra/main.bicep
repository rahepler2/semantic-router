// ============================================================================
// main.bicep — Semantic Router Service on Azure Container Apps
// ============================================================================
// Provisions:
//   - Azure Container Registry (ACR)
//   - Log Analytics workspace
//   - Container Apps Environment
//   - Container App with:
//       • System-assigned managed identity (→ Azure OpenAI RBAC, no API keys)
//       • Health / readiness / startup probes against FastAPI endpoints
//       • KEDA HTTP autoscaling rule
//       • Secrets for Typesense connection
//
// Usage:
//   az deployment group create \
//     --resource-group <rg> \
//     --template-file infra/main.bicep \
//     --parameters environmentName=dev \
//                  azureOpenAiEndpoint='https://<resource>.openai.azure.com' \
//                  typesenseHost='<typesense-host>' \
//                  typesenseApiKey='<key>'
// ============================================================================

// ── Parameters ──────────────────────────────────────────────────────────────

@description('Environment name used as a suffix for all resources.')
param environmentName string

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Azure OpenAI endpoint URL.')
param azureOpenAiEndpoint string

@description('Azure OpenAI API version.')
param azureOpenAiApiVersion string = '2024-02-01'

@description('Azure OpenAI embedding deployment name.')
param azureEmbeddingDeployment string = 'text-embedding-ada-002'

@description('Typesense host (IP or FQDN).')
param typesenseHost string

@description('Typesense port.')
param typesensePort string = '8108'

@description('Typesense protocol.')
param typesenseProtocol string = 'https'

@description('Typesense API key.')
@secure()
param typesenseApiKey string

@description('Typesense collection name for route embeddings.')
param typesenseCollection string = 'semantic_routes'

@description('Container image tag. Set during CI/CD.')
param imageTag string = 'latest'

@description('Minimum replica count (1 = always warm, 0 = scale to zero).')
param minReplicas int = 1

@description('Maximum replica count for KEDA scaling.')
param maxReplicas int = 10

@description('Concurrent HTTP requests per replica before KEDA scales out.')
param httpConcurrency string = '50'

@description('Container CPU cores.')
param containerCpu string = '0.5'

@description('Container memory.')
param containerMemory string = '1Gi'

// ── Variables ───────────────────────────────────────────────────────────────

var resourceSuffix = uniqueString(resourceGroup().id, environmentName)
var acrName = 'acr${resourceSuffix}'
var logAnalyticsName = 'log-${environmentName}-${resourceSuffix}'
var acaEnvName = 'acaenv-${environmentName}-${resourceSuffix}'
var appName = 'semantic-router-${environmentName}'

// ── Azure Container Registry ────────────────────────────────────────────────

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
  }
}

// ── Log Analytics ───────────────────────────────────────────────────────────

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: logAnalyticsName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

// ── Container Apps Environment ──────────────────────────────────────────────

resource acaEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: acaEnvName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

// ── Container App ───────────────────────────────────────────────────────────

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: acaEnvironment.id

    configuration: {
      activeRevisionsMode: 'Single'

      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }

      // Pull images from ACR using managed identity
      registries: [
        {
          server: acr.properties.loginServer
          identity: 'system'
        }
      ]

      secrets: [
        {
          name: 'typesense-api-key'
          value: typesenseApiKey
        }
      ]
    }

    template: {
      containers: [
        {
          name: 'semantic-router'
          image: '${acr.properties.loginServer}/semantic-router:${imageTag}'
          resources: {
            cpu: json(containerCpu)
            memory: containerMemory
          }

          env: [
            // ── Azure OpenAI (managed identity, no API key) ──
            {
              name: 'AZURE_OPENAI_ENDPOINT'
              value: azureOpenAiEndpoint
            }
            {
              name: 'AZURE_OPENAI_API_VERSION'
              value: azureOpenAiApiVersion
            }
            {
              name: 'AZURE_EMBEDDING_DEPLOYMENT'
              value: azureEmbeddingDeployment
            }
            {
              name: 'AZURE_USE_MANAGED_IDENTITY'
              value: 'true'
            }
            // ── Typesense ──
            {
              name: 'TYPESENSE_HOST'
              value: typesenseHost
            }
            {
              name: 'TYPESENSE_PORT'
              value: typesensePort
            }
            {
              name: 'TYPESENSE_PROTOCOL'
              value: typesenseProtocol
            }
            {
              name: 'TYPESENSE_API_KEY'
              secretRef: 'typesense-api-key'
            }
            {
              name: 'TYPESENSE_COLLECTION'
              value: typesenseCollection
            }
          ]

          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/healthz'
                port: 8000
              }
              periodSeconds: 10
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/readyz'
                port: 8000
              }
              periodSeconds: 5
              failureThreshold: 3
            }
            {
              type: 'Startup'
              httpGet: {
                path: '/startupz'
                port: 8000
              }
              periodSeconds: 5
              failureThreshold: 10
              initialDelaySeconds: 5
            }
          ]
        }
      ]

      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          {
            name: 'http-scaling'
            http: {
              metadata: {
                concurrentRequests: httpConcurrency
              }
            }
          }
        ]
      }
    }
  }
}

// ── RBAC: Grant the Container App's managed identity AcrPull on ACR ─────────

@description('AcrPull built-in role.')
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

resource acrPullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, containerApp.id, acrPullRoleId)
  scope: acr
  properties: {
    principalId: containerApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
  }
}

// ── Outputs ─────────────────────────────────────────────────────────────────

output acrLoginServer string = acr.properties.loginServer
output acrName string = acr.name
output containerAppFqdn string = containerApp.properties.configuration.ingress.fqdn
output containerAppPrincipalId string = containerApp.identity.principalId
