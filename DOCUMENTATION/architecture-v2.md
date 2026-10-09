# FlowSync V2 — Canonical Architecture

> This document describes the actual FlowSync architecture after Phase 1 (Foundation & Cleanup).
> For historical/planned documents, see [DOCUMENTATION/README.md](./README.md).

---

## Table of Contents

1. [Overview](#1-overview)
2. [Current Architecture](#2-current-architecture)
3. [Components](#3-components)
4. [Data Flow](#4-data-flow)
5. [Authentication](#5-authentication)
6. [Event Model](#6-event-model)
7. [Error Handling](#7-error-handling)
8. [Correlation IDs](#8-correlation-ids)
9. [Structured Logging](#9-structured-logging)
10. [Known Limitations](#10-known-limitations)
11. [Phase 1 Changes](#11-phase-1-changes)
12. [Future Phases](#12-future-phases)

---

## 1. Overview

**FlowSync** gives AI coding agents persistent project memory via the Model Context Protocol (MCP).

- **Agent-first**: AI agents (GitHub Copilot, Cursor, Claude) call MCP tools to log and search context
- **Dual input**: Agents log reasoning via `log_context`; git pushes auto-capture diffs as fallback
- **RAG-powered**: Titan embeddings + Nova Pro provide grounded answers with source citations
- **Serverless**: AWS Lambda, DynamoDB, API Gateway, S3, Bedrock — zero idle cost

---

## 2. Current Architecture

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│                                      CLIENT LAYER                                        │
│                                                                                          │
│  ┌──────────────────────┐  ┌──────────────────────┐  ┌───────────────────────────────┐  │
│  │  VS Code Extension   │  │   Web Dashboard      │  │  AI Agents (Copilot/        │  │
│  │  (TypeScript/Node)   │  │   (Next.js 14,       │  │  Cursor/Claude via MCP)     │  │
│  │                      │  │   React 18,          │  │                               │  │
│  │  • Git hook capture  │  │   Tailwind,          │  │  • get_project_context      │  │
│  │  • Hook listener     │  │   Radix UI)          │  │  • search_context (RAG)     │  │
│  │  • Webview panel     │  │                      │  │  • get_recent_changes       │  │
│  │  • MCP config        │  │  Pages:              │  │  • log_context              │  │
│  │  • Auto-detect       │  │  • Dashboard         │  │  • get_events               │  │
│  │                      │  │  • Search            │  │                               │  │
│  └──────────┬───────────┘  │  • Chat              │  └──────────────┬──────────────┘  │
│             │              │  • Analytics         │               │                 │
│             │              └──────────┬───────────┘               │                 │
│             │                         │                          │                 │
│             │          HTTPS + Bearer Token Auth                  │                 │
│             └─────────────────────────┼──────────────────────────┘                 │
└──────────────────────────────────────┼──────────────────────────────────────────────┘
                                       │
                                       ▼
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│                              AWS API GATEWAY (REST)                                      │
│                                                                                          │
│  POST   /api/v1/projects          POST   /api/v1/events                                 │
│  GET    /api/v1/projects/{id}     GET    /api/v1/projects/{id}/events                   │
│  POST   /api/v1/query             POST   /api/v1/chat                                   │
│  POST   /mcp                                                                           │
└──────────────┬──────────────┬──────────────┬──────────────┬────────────────────────────┘
               │              │              │              │
      ┌────────▼─────┐ ┌──────▼───────┐ ┌──▼──────────┐ ┌─▼─────────────────────────┐
      │  Ingestion   │ │  Query       │ │  Chat       │ │  MCP Lambda               │
      │  Lambda      │ │  Lambda      │ │  Lambda     │ │  (Python 3.12)            │
      │  (Node 20)   │ │  (Python)    │ │  (Python)   │ │                           │
      │              │ │              │ │             │ │  • 5 tool handlers        │
      │  • Validate  │ │  • Timeline  │ │  • Session  │ │  • Pagination             │
      │  • Store     │ │  • RAG search│ │    mgmt     │ │  • Branch inheritance     │
      │  • Archive   │ │              │ │  • Nova Lite│ │  • RAG search             │
      │  • Async inv.│ │              │ │  • RAG      │ │  • Re-embed on enrich     │
      │  • Merge det.│ │              │ │  • Hybrid   │ │                           │
      └──────┬───────┘ └──────┬───────┘ └──────┬──────┘ └──────────┬────────────────┘
             │                │                │                  │
             │  Async         │                │                  │
             ▼                │                │                  │
      ┌──────────────────┐    │      ┌─────────▼─────────────────▼──────────────────┐
      │  AI Processing   │    │      │           SHARED LAMBDA LAYER                  │
      │  Lambda          │    │      │           (Python 3.12)                        │
      │  (Python 3.12)   │    │      │                                                │
      │                  │    │      │  • auth.py — Token verification (scrypt)       │
      │  • Nova Pro      │    │      │  • helpers.py — RAG pipeline, embeddings,      │
      │    (temp=0)      │    │      │    cosine similarity, branch affinity,         │
      │  • Titan Embed   │    │      │    caching, DynamoDB helpers, pagination       │
      │  • Confidence    │    │      └────────────────────────────────────────────────┘
      │  • Orphan merge  │    │
      │  • Branch prop.  │    │
      └────────┬─────────┘    │
               │              │
      ┌────────▼──────────────▼────────────────────────────────────────────────────────┐
      │                              AWS BEDROCK (AI LAYER)                            │
      │                                                                                  │
      │  ┌─────────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐  │
      │  │  Amazon Nova Pro    │  │  Amazon Nova Lite │  │  Amazon Titan            │  │
      │  │  (us.amazon.nova-   │  │  (us.amazon.nova- │  │  Embeddings v1           │  │
      │  │   pro-v1:0)         │  │   lite-v1:0)      │  │  (amazon.titan-embed-    │  │
      │  │                     │  │                   │  │   text-v1)               │  │
      │  │  Temperature: 0     │  │  Temperature: 0.7 │  │  Dimension: 1536        │  │
      │  │  Max tokens: 2000   │  │  Max tokens: 2000 │  │                          │  │
      │  │                     │  │                   │  │  Used for:               │  │
      │  │  Used for:          │  │  Used for:        │  │  • Query embedding       │  │
      │  │  • Intent extraction│  │  • Chat responses │  │  • Context embedding     │  │
      │  │  • RAG answer gen   │  │  • Conversational │  │  • Cosine similarity     │  │
      │  │    (temp=0.3)       │  │    dialogue       │  │  • Branch affinity       │  │
      │  └─────────────────────┘  └──────────────────┘  └──────────────────────────┘  │
      └────────────────────────────────────────────────────────────────────────────────┘
               │
               ▼
      ┌────────────────────────────────────────────────────────────────────────────────┐
      │                              STORAGE LAYER                                     │
      │                                                                                │
      │  ┌────────────────────────────────────────────────────────────────────────┐   │
      │  │                        Amazon DynamoDB (On-Demand)                      │   │
      │  │                                                                        │   │
      │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐     │   │
      │  │  │ flowsync-    │  │ flowsync-    │  │ flowsync-context         │     │   │
      │  │  │ projects     │  │ events       │  │                          │     │   │
      │  │  │              │  │              │  │ PK: eventId              │     │   │
      │  │  │ PK: projectId│  │ PK: projectId│  │ GSI: ProjectContextIndex │     │   │
      │  │  │              │  │ SK: ts#evtId │  │ GSI: BranchContextIndex  │     │   │
      │  │  │ Stores:      │  │              │  │                          │     │   │
      │  │  │ • name       │  │ GSI:         │  │ Stores:                  │     │   │
      │  │  │ • languages  │  │ EventIdIndex │  │ • feature, decision      │     │   │
      │  │  │ • frameworks │  │ BranchIndex  │  │ • tasks, stage, risk     │     │   │
      │  │  │ • tokenHash  │  │              │  │ • entities, confidence   │     │   │
      │  │  │ • teamMembers│  │              │  │ • embedding (1536-dim)   │     │   │
      │  │  └──────────────┘  └──────────────┘  │ • agentReasoning         │     │   │
      │  │                                        └──────────────────────────┘     │   │
      │  │  ┌──────────────┐  ┌────────────────────────┐  ┌──────────────┐       │   │
      │  │  │ flowsync-    │  │ flowsync-chat-sessions │  │ flowsync-    │       │   │
      │  │  │ audit        │  │                        │  │ cache        │       │   │
      │  │  │              │  │ PK: sessionId          │  │              │       │   │
      │  │  │ PK: entityId │  │ TTL: 30 min            │  │ PK: cacheKey │       │   │
      │  │  │ SK: timestamp│  │ Max 10 messages/session │  │ TTL: 1 hour  │       │   │
      │  │  │              │  │                        │  │              │       │   │
      │  │  │ Immutable log│  │                        │  │ RAG cache    │       │   │
      │  │  └──────────────┘  └────────────────────────┘  └──────────────┘       │   │
      │  └────────────────────────────────────────────────────────────────────────┘   │
      │                                                                                │
      │  ┌────────────────────────────────────────┐                                   │
      │  │  Amazon S3                             │                                   │
      │  │  flowsync-raw-events-{account}         │                                   │
      │  │  Raw event JSON archive                │                                   │
      │  └────────────────────────────────────────┘                                   │
      └────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Components

### 3.1 VS Code Extension (`extension/`)

**Runtime**: Node.js / TypeScript (bundled with webpack)

**Key Files**:
- `src/extension.ts` — Main activation, status bar, hook listener lifecycle
- `src/commands/initProject.ts` — Project initialization, auto-detection, token generation
- `src/commands/joinProject.ts` — Join existing project with token
- `src/commands/recordReasoning.ts` — Human path to `log_context`
- `src/commands/catchMeUp.ts` — Summarize changes since last seen
- `src/eventTransmitter.ts` — HTTP transport with retry logic
- `src/api.ts` — Shared API client for extension/webview
- `src/gitUtils.ts` — Git diff, commit info, merge detection
- `src/hookListener.ts` — Local HTTP server for git hook signals
- `src/panels/FlowSyncPanel.ts` — Main webview panel
- `src/panels/FlowSyncSidebar.ts` — Activity bar sidebar
- `webview-ui/src/components/*.tsx` — React components (Dashboard, Chat, Init, Join, CatchMeUp)

**User-Facing Branding**: FlowSync (formerly BuildBerry)

### 3.2 MCP Server (`mcp-server/`)

**Runtime**: Node.js / TypeScript (stdio transport)

**Tools**:
| Tool | Purpose |
|------|---------|
| `get_project_context` | Branch-aware context retrieval with pagination |
| `search_context` | Natural language RAG search with citations |
| `get_recent_changes` | Latest activity across branches |
| `log_context` | Record reasoning, merges into recent push |
| `get_events` | Raw event listing (authenticated) |

**Branch Auto-Scoping**: Detects current git branch at startup; `search_context` defaults to current branch.

### 3.3 Frontend Dashboard (`frontend/`)

**Runtime**: Next.js 14, React 18, TypeScript

**Pages**:
- `/` — Landing page with login modal
- `/dashboard` — Project timeline, stats, quick actions
- `/search` — RAG-powered natural language search
- `/chat` — Conversational AI with hybrid RAG
- `/analytics` — Contributor stats, activity charts
- `/settings` — Connection config, demo access, sign out

**API Communication**: Axios with `NEXT_PUBLIC_API_BASE_URL`, Bearer token auth

### 3.4 Ingestion Lambda (`infra/lambda/ingestion/`)

**Runtime**: Node.js 20.x

**Routes**:
- `POST /api/v1/projects` — Create project, return token once
- `POST /api/v1/events` — Ingest push/developer_note events
- `GET /api/v1/projects/{projectId}` — Validate token, return project info

**Flow**: Validate → DynamoDB write → S3 archive → Async invoke AI Processing → 200 OK

**SLA**: < 500ms (warm ~175ms)

**Correlation ID**: Extracted from `x-correlation-id` / `x-request-id` headers; passed to async invocations.

### 3.5 AI Processing Lambda (`infra/lambda/ai_processing/`)

**Runtime**: Python 3.12, 512MB, 60s timeout

**Responsibilities**:
1. Call Nova Pro (temp=0) for intent extraction
2. Compute deterministic confidence (0.55–1.0)
3. Generate Titan embedding (1536-dim)
4. Write to `flowsync-context` with GSI indexes
5. Handle orphaned record merging (Direction B)
6. Handle branch merge propagation

**Fallback**: Nova Lite on throttle/timeout.

### 3.6 Query Lambda (`infra/lambda/query/`)

**Runtime**: Python 3.12, 256MB, 30s timeout

**Routes**:
- `GET /api/v1/projects/{projectId}/events` — Timeline with branch filter
- `POST /api/v1/query` — RAG search

**Auth**: Bearer token verified against project hash

### 3.7 MCP Lambda (`infra/lambda/mcp/`)

**Runtime**: Python 3.12, 256MB, 30s timeout

**Tools**: Same 5 tools as MCP server, exposed via HTTP `/mcp`

**No Auth**: MCP tools are unauthenticated (projectId + token via env vars)

### 3.8 Chat Lambda (`infra/lambda/chat/`)

**Runtime**: Python 3.12, 512MB, 30s timeout

**Hybrid Approach**:
1. Classify question: factual → RAG pipeline → Nova Pro → Nova Lite presentation
2. Conversational → Nova Lite directly with context

**Session Management**: DynamoDB `flowsync-chat-sessions`, 30-min TTL, max 10 messages.

### 3.9 Shared Lambda Layer (`infra/lambda/shared/python/flowsync_common/`)

- `auth.py` — scrypt token verification (matches Node.js)
- `helpers.py` — RAG pipeline, embeddings, cosine similarity, caching, pagination

---

## 4. Data Flow

### 4.1 Push Event Flow

```
Developer pushes code
       │
       ▼
Git pre-push hook fires (local HTTP listener)
       │
       ▼
Extension captures: diff, commit, author, branch, merge info
       │
       ▼
POST /api/v1/events (Bearer token)
       │
       ▼
API Gateway → Ingestion Lambda
       │
       ├─▶ Validate schema (UUID, commit hash, ISO8601, diff ≤ 50KB)
       ├─▶ Write to flowsync-events (PK: projectId, SK: timestamp#eventId)
       ├─▶ Archive raw JSON to S3 (flowsyc-raw-events-{account})
       ├─▶ Async invoke AI Processing Lambda (fire-and-forget)
       └─▶ Return 200 OK (within 500ms)
               │
               ▼
AI Processing Lambda (async)
       │
       ├─▶ Nova Pro: Extract feature, decision, tasks, stage, risk, entities
       ├─▶ Compute confidence
       ├─▶ Titan Embedding (1536-dim)
       ├─▶ Write to flowsync-context (PK: eventId, GSI: projectId+branch)
       ├─▶ Check for orphaned record (same project/branch/author, 30 min)
       ├─▶ If merge: propagate source branch context to target
       └─▶ Write audit record
```

### 4.2 RAG Search Flow

```
User/Agent asks question
       │
       ▼
POST /api/v1/query or /mcp (search_context)
       │
       ▼
Query Lambda / MCP Lambda
       │
       ├─▶ Check cache (SHA256(project:query:branch), 1hr TTL)
       ├─▶ Titan embed query (~112ms)
       ├─▶ Fetch ALL context records (paginated, branch-scoped or all)
       ├─▶ Cosine similarity vs all embeddings
       ├─▶ Branch affinity: non-main × 0.85 penalty (if no branch filter)
       ├─▶ Top-5 by similarity
       ├─▶ Nova Pro (temp=0.3): Grounded answer + citations
       ├─▶ Fallback to Nova Lite on throttle
       ├─▶ Write to cache
       └─▶ Return { answer, answerGrounded, sources[] }
```

### 4.3 Agent Logging Flow (log_context)

```
Agent calls log_context
       │
       ▼
MCP Lambda / Extension API
       │
       ├─▶ Find recent push record (same project/branch/author, 30 min)
       ├─▶ If found: UPDATE with reasoning, decision, risk, tasks
       ├─▶ Re-embed enriched content
       ├─▶ If not found: CREATE orphaned record (commitHash: null)
       └─▶ Write audit record
```

---

## 5. Authentication

### Token Generation (Project Creation)
- 256-bit token: `crypto.randomBytes(32).toString('hex')`
- Scrypt hash: `crypto.scryptSync(token, salt, 64)` with N=16384, r=8, p=1
- Stored as `salt:hash` in `flowsync-projects.apiTokenHash`
- Token shown **once** at creation (auto-copied to clipboard)

### Token Verification
- **Node.js**: `crypto.scryptSync(token, salt, 64)` + `crypto.timingSafeEqual`
- **Python**: `hashlib.scrypt(token.encode(), salt=salt.encode(), n=16384, r=8, p=1, dklen=64)` + `hmac.compare_digest`
- Salt encoded as UTF-8 hex string (matching Node.js behavior)

### Protected Endpoints
| Endpoint | Auth |
|----------|------|
| `POST /api/v1/events` | ✅ Bearer token |
| `GET /api/v1/projects/{id}` | ✅ Bearer token |
| `GET /api/v1/projects/{id}/events` | ✅ Bearer token |
| `POST /api/v1/query` | ✅ Bearer token |
| `POST /api/v1/chat` | ✅ Bearer token |
| `POST /mcp` | ❌ No auth (uses env vars) |

### Storage
- **Extension**: VS Code SecretStorage (`flowsync.token.{projectId}`)
- **MCP Server**: Environment variables (`FLOWSYNC_TOKEN`)
- **Frontend**: localStorage (projectId + token), never in code

---

## 6. Event Model

### Canonical FlowSyncEvent (V2 Foundation)

```typescript
interface FlowSyncEvent {
  eventId: string;           // UUID v4
  eventType: EventType;      // push | developer_note | agent_reasoning | merge | deployment | build
  schemaVersion: string;     // "1"
  source: EventSource;       // vscode | github | ci_cd | deployment | manual
  projectId: string;
  organizationId?: string;   // Future
  repositoryId?: string;     // Future
  actor: Actor;              // { id, name, email?, avatarUrl? }
  timestamp: string;         // ISO 8601
  deliveryId?: string;       // Source delivery ID (e.g., GitHub webhook)
  correlationId?: string;    // Request tracing
  payload: BaseEventPayload; // Event-specific
  metadata?: Record<string, unknown>;
}
```

### Payload Types

| Event Type | Payload Fields |
|------------|----------------|
| `push` | commitHash, message, diff, author, parentBranch?, isMerge?, sourceBranch?, changedFiles? |
| `developer_note` | text, filePath, lineNumber |
| `agent_reasoning` | reasoning, branch, author, decision?, tasks?, risk? |
| `merge` | sourceBranch, targetBranch, commitHash, author |

### Validation
- Zod schemas in `@flowsync/shared/src/events.ts`
- Validates: UUID v4, 40-char hex commit hash, ISO 8601, diff ≤ 50KB, no directory traversal

---

## 7. Error Handling

### Error Codes (Standardized)

| Category | Codes |
|----------|-------|
| Validation | `VALIDATION_ERROR`, `INVALID_SCHEMA`, `MISSING_REQUIRED_FIELD`, `INVALID_FIELD_FORMAT` |
| Authentication | `AUTHENTICATION_ERROR`, `INVALID_TOKEN`, `TOKEN_EXPIRED`, `MISSING_AUTH_HEADER`, `MALFORMED_AUTH_HEADER` |
| Authorization | `AUTHORIZATION_ERROR`, `INSUFFICIENT_PERMISSIONS`, `PROJECT_ACCESS_DENIED` |
| Not Found | `NOT_FOUND`, `PROJECT_NOT_FOUND`, `EVENT_NOT_FOUND`, `CONTEXT_NOT_FOUND` |
| Conflict | `CONFLICT`, `DUPLICATE_EVENT`, `DUPLICATE_PROJECT` |
| Rate Limit | `RATE_LIMITED`, `TOO_MANY_REQUESTS` |
| External Service | `EXTERNAL_SERVICE_ERROR`, `BEDROCK_ERROR`, `BEDROCK_THROTTLED`, `DYNAMODB_ERROR`, `S3_ERROR` |
| Internal | `INTERNAL_ERROR`, `CONFIGURATION_ERROR`, `SERIALIZATION_ERROR` |

### HTTP Status Mapping
- 400: Validation errors
- 401: Authentication errors
- 403: Authorization errors
- 404: Not found
- 409: Conflicts
- 429: Rate limited
- 500: Internal errors
- 502: External service errors
- 503: Bedrock throttled (retryable)

### Response Format
```json
{
  "error": "ERROR_CODE",
  "message": "Human-readable message",
  "correlationId": "uuid",
  "details": {}  // Only in development
}
```

### Implementation
- `FlowSyncError` class in `@flowsync/shared/src/errors.ts`
- Factory methods: `.validation()`, `.authentication()`, `.notFound()`, `.internal()`
- `toFlowSyncError()` converts any error to structured format
- Never exposes stack traces, secrets, or AWS internals to clients

---

## 8. Correlation IDs

### Headers
- `x-correlation-id` — Primary correlation ID
- `x-request-id` — Fallback (API Gateway request ID)

### Propagation
1. **API Gateway** → Extracts/generates correlation ID
2. **Ingestion Lambda** → Includes in async invocations to AI Processing
3. **AI Processing** → Passes to merge propagation invocations
4. **Query/MCP/Chat Lambdas** → Extract from headers, include in logs
5. **Extension** → Generates for outgoing requests (future)

### Usage
```typescript
// Extract or generate
const correlationId = extractCorrelationId(headers);

// Run with context
runWithCorrelation(correlationId, () => {
  // All logs include correlationId automatically
  logger.info("Processing event");
});

// Get in async context
const corrId = getCorrelationId();
```

---

## 9. Structured Logging

### Log Entry Format (JSON)
```json
{
  "timestamp": "2026-10-09T12:34:56.789Z",
  "level": 1,
  "levelName": "INFO",
  "service": "ingestion",
  "function": "ingestEvent",
  "correlationId": "abc-123",
  "eventId": "evt-456",
  "projectId": "proj-789",
  "eventType": "push",
  "message": "Event ingested successfully",
  "metadata": { "ingestion_ms": 175 }
}
```

### Sanitization
Automatically redacts: `token`, `password`, `secret`, `apikey`, `authorization`, `bearer`, `credential`, `privatekey`

### Logger Factory
```typescript
const logger = createLogger("ingestion", "ingestEvent");
logger.info("Event received", { eventId });
logger.error("Failed to process", { eventId }, error);
```

---

## 10. Known Limitations

### Deferred to Later Phases

| Limitation | Phase |
|------------|-------|
| No SQS queue between ingestion and AI processing | Phase 2 |
| No GitHub App / webhook integration | Phase 2 |
| No CI/CD event ingestion | Phase 2 |
| No engineering entities (files, functions, classes) | Phase 3 |
| No relationship graph between entities | Phase 3 |
| No RBAC / multi-tenancy | Phase 5 |
| No distributed tracing (X-Ray) | Observability Phase |
| No advanced RAG (hybrid search, reranking) | Phase 4 |
| No MCP V2 toolset (write_context, get_entities, etc.) | Phase 5 |
| No automated deployment pipeline | Phase 2 |

### Current Constraints
- MCP `/mcp` endpoint has no authentication (relies on network isolation)
- Single AWS account/region (us-east-1)
- Titan Embeddings v1 only (v2 migration needed)
- No cross-region replication
- Frontend demo credentials require env vars (not in repo)

---

## 11. Phase 1 Changes

### Branding
- ✅ Extension: `buildberry` → `flowsync` (package name)
- ✅ Display name: "BuildBerry" → "FlowSync"
- ✅ All user-facing strings: commands, notifications, webview UI, status bar
- ✅ Internal identifiers preserved: `flowsync-projects`, `flowsync-events`, etc.

### Security
- ✅ Removed hardcoded `DEMO_PROJECT_ID` and `DEMO_TOKEN` from `frontend/src/lib/constants.ts`
- ✅ Added `.env.example` files for frontend, extension, MCP server
- ✅ Updated `.gitignore` files to exclude `.env`, `.env.local`, secrets
- ✅ Demo credentials now via `NEXT_PUBLIC_DEMO_PROJECT_ID` / `NEXT_PUBLIC_DEMO_TOKEN`

### Repository Hygiene
- ✅ Root `.gitignore`: env files, node_modules, build output, coverage, IDE, OS files
- ✅ Frontend `.gitignore`: env, `.next`, `out`, coverage, turbo
- ✅ Infra `.gitignore`: compiled JS (except lambdas), cdk.out, test logs, Python cache
- ✅ Extension `.vscodeignore`: env files added

### Event Foundation
- ✅ `@flowsync/shared` package with canonical event types
- ✅ Zod validation schemas for all event types
- ✅ Type guards for payload discrimination
- ✅ Schema version support (`schemaVersion: "1"`)

### Correlation IDs
- ✅ `x-correlation-id` / `x-request-id` header support
- ✅ Ingestion Lambda: extracts, propagates to async invocations
- ✅ Response headers include correlation ID
- ✅ Shared utilities: `generateCorrelationId`, `runWithCorrelation`, `extractCorrelationFromApiGateway`

### Error Model
- ✅ `FlowSyncError` class with standardized codes
- ✅ HTTP status mapping
- ✅ Safe serialization (no stack traces in production)
- ✅ Factory methods for common errors

### Structured Logging
- ✅ JSON log format with timestamp, level, service, correlationId
- ✅ Sanitization of sensitive fields
- ✅ Service/function context

### Testing Foundation
- ✅ `@flowsync/shared` unit tests (events, errors, correlation)
- ✅ Ingestion Lambda tests (routes, validation, auth)
- ✅ Extension tests (config, API, error handling)
- ✅ Jest + ts-jest configuration for all test suites

---

## 12. Future Phases

| Phase | Focus | Key Deliverables |
|-------|-------|------------------|
| **Phase 2** | GitHub + Event-Driven | SQS queue, GitHub App, webhook ingestion, CI/CD events, deployment tracking |
| **Phase 3** | Engineering Entities | File/function/class extraction, relationship graph, impact analysis |
| **Phase 4** | Advanced Retrieval | Hybrid search, reranking, query expansion, multi-hop reasoning |
| **Phase 5** | MCP V2 + Multi-tenant | Full MCP toolset, RBAC, organizations, SSO, audit compliance |
| **Observability** | Distributed Tracing | X-Ray integration, custom dashboards, alerting, SLOs |

---

## Appendix: DynamoDB Schema Reference

| Table | PK | SK | GSIs |
|-------|----|----|------|
| `flowsync-projects` | `projectId` | — | — |
| `flowsync-events` | `projectId` | `timestamp#eventId` | `EventIdIndex` (eventId), `BranchIndex` (projectId, branch#timestamp) |
| `flowsync-context` | `eventId` | — | `ProjectContextIndex` (projectId, extractedAt), `BranchContextIndex` (projectId, branch#extractedAt) |
| `flowsync-audit` | `entityId` | `timestamp` | — |
| `flowsync-chat-sessions` | `sessionId` | — | TTL: `ttl` |
| `flowsync-cache` | `cacheKey` | — | TTL: `expiresAt` |

---

*Generated after Phase 1 completion. Update as architecture evolves.*