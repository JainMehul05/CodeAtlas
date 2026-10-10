# CodeAtlas Phase 3: Engineering Knowledge Graph Architecture

## Overview

Phase 3 implements an Engineering Knowledge Graph that connects engineering artifacts into a persistent, queryable network of relationships. This enables CodeAtlas to answer complex engineering questions about codebase relationships, decisions, and history.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        CodeAtlas Phase 3                         │
├─────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │   Ingestion  │──▶│  AI Process  │──▶│  Graph Ingestion    │  │
│  │   Pipeline   │   │  (Bedrock)   │   │  (DynamoDB)         │  │
│  └──────────────┘   └──────────────┘   └──────────┬───────────┘  │
│                                                  │               │
│  ┌──────────────┐  ┌──────────────┐             │               │
│  │   MCP Tools  │◀──│  Graph Query │◀────────────┘               │
│  │  (Graph API) │   │   Service    │                               │
│  └──────┬───────┘   └──────┬───────┘                               │
│         │                  │                                       │
│         ▼                  ▼                                       │
│  ┌──────────────────────────────────────┐                          │
│  │      Graph-Aware RAG Pipeline        │                          │
│  │  (Semantic Search + Graph Traversal) │                          │
│  └──────────────────────────────────────┘                          │
└─────────────────────────────────────────────────────────────────┘
```

## Data Model

### Entities

| Entity Type | Description | Key Attributes |
|-------------|-------------|----------------|
| `repository` | Source code repository | provider, owner, name, canonicalUrl, defaultBranch |
| `commit` | Git commit | sha, author, committer, message, committedAt, parentShas |
| `pull_request` | GitHub pull request | number, title, description, state, author, sourceBranch, targetBranch |
| `file` | Repository file | path, language, lastObservedAt |
| `engineering_decision` | Explicit technical decision | title, summary, decision, rationale, status, sourceType, sourceId |

### Relationships

| Relationship | Source → Target | Provenance | Description |
|--------------|-----------------|------------|-------------|
| `contains` | repository → file | explicit | Repository contains file |
| `authored_in` | commit → repository | explicit | Commit authored in repository |
| `modifies` | commit → file | explicit | Commit modifies file |
| `has_parent` | commit → commit | explicit | Parent commit relationship |
| `includes_commit` | pull_request → commit | explicit | PR includes commit |
| `targets_file` | pull_request → file | explicit | PR targets file |
| `has_decision` | repository → engineering_decision | explicit | Repository has decision |
| `relates_to` | engineering_decision → {commit,pr,file,repo} | inferred | Decision relates to artifact |
| `derived_from` | any → engineering_decision | explicit | Entity derived from decision |

### Provenance Types

- `explicit`: Directly stated in source (e.g., commit modifies file)
- `inferred`: Derived from heuristics or rules
- `user_defined`: Manually created by user

## Storage Architecture

### DynamoDB Tables

| Table | Purpose | PK | SK | GSIs |
|-------|---------|-----|-----|------|
| `flowsync-graph-entities` | Entity storage | entityId | - | ProjectEntityIndex (projectId, entityType#createdAt), RepositoryEntityIndex (repositoryId, entityType#createdAt), EntityTypeIndex (entityType, createdAt) |
| `flowsync-graph-relationships` | Relationship storage | relationshipId | - | SourceEntityIndex (sourceEntityId, relationshipType#createdAt), TargetEntityIndex (targetEntityId, relationshipType#createdAt), ProjectRelationshipIndex (projectId, relationshipType#createdAt), RelationshipTypeIndex (relationshipType, createdAt) |

### Key Design Decisions

1. **Adjacency List Model**: Uses DynamoDB adjacency list pattern for graph storage
2. **Stable IDs**: Deterministic ID generation (e.g., `commit:repo:github:owner:repo:sha`)
3. **Conditional Writes**: Optimistic locking for idempotent upserts
4. **Project Isolation**: All queries scoped to projectId
5. **Bounded Traversal**: Max depth 3, max results 50

## Ingestion Pipeline

### Event Processing Flow

```
Event Received
      │
      ▼
┌───────────────────┐
│ Idempotency Check │
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│ Bedrock Extraction│
│ (Nova Pro + Titan)│
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│ Context Record    │
│ Written to DynamoDB│
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│ Graph Ingestion   │
│ (Entities + Rels) │
└─────────┬─────────┘
```

### Supported Event Types

| Event Type | Entities Created | Relationships Created |
|------------|------------------|----------------------|
| `push` | commit, file, repository | modifies (commit→file), authored_in (commit→repo), contains (repo→file) |
| `merge` | pull_request, commit, file, repository | includes_commit, targets_file, modifies, authored_in, contains |
| `context_extraction` | engineering_decision | relates_to (decision→commit) |

### Idempotency

- **Event-level**: deliveryId/eventId used as idempotency key
- **Entity-level**: Stable IDs prevent duplicate entities
- **Relationship-level**: Deterministic relationship IDs with conditional writes

## Graph Query Service

### Query Operations

| Operation | Description | Complexity |
|-----------|-------------|------------|
| `get_entity` | Single entity lookup | O(1) |
| `get_entities` | Batch entity lookup | O(n) |
| `query_entities_by_project` | Project-scoped entity listing | O(paginated) |
| `query_entities_by_repository` | Repository-scoped entity listing | O(paginated) |
| `get_outgoing_relationships` | Outgoing edges from entity | O(paginated) |
| `get_incoming_relationships` | Incoming edges to entity | O(paginated) |
| `get_related_entities` | Bounded graph traversal | O(b^d) where b=branching, d=depth |
| `get_repository_graph_summary` | Aggregated counts by type | O(entity_types + rel_types) |

### Traversal Parameters

| Parameter | Default | Max | Description |
|-----------|---------|-----|-------------|
| `maxDepth` | 2 | 3 | Maximum traversal depth |
| `maxResults` | 20 | 50 | Maximum entities returned |
| `direction` | both | - | outgoing/incoming/both |
| `relationshipTypes` | all | - | Filter by relationship types |

## Graph-Aware RAG Pipeline

### Enhanced RAG Flow

```
User Query
    │
    ▼
┌──────────────────────┐
│ Semantic Search      │
│ (Titan Embeddings +  │
│  Cosine Similarity)  │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ Graph Expansion      │
│ (Traverse from top   │
│  semantic matches)   │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ Combined Context     │
│ (Semantic + Graph)   │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ Nova Pro Generation  │
│ (Grounded Answer)    │
└──────────────────────┘
```

### Graph Expansion Algorithm

1. **Seed Selection**: Top-k semantic matches provide commit hashes
2. **Entity Mapping**: Map commit hashes to graph entities
3. **Bounded Traversal**: BFS/DFS up to maxDepth, maxEntities
4. **Context Conversion**: Convert graph entities to context format
5. **Source Attribution**: Tag graph-derived context with provenance

### Provenance Tracking

All graph-derived context includes:
- `_graph_entity: true`
- `_graph_entity_type`: entity type
- `provenance`: "graph"
- Original semantic sources preserved

## MCP Tools (Graph API)

| Tool | Purpose | Key Parameters |
|------|---------|----------------|
| `query_knowledge_graph` | Graph traversal from entity | entityId, entityType, direction, maxDepth, maxResults |
| `get_related_changes` | Related changes for entity | entityType, entityId, includeGraph |
| `get_engineering_decisions` | Explicit decisions | projectId, repositoryId, entityId, status |
| `get_repository_graph_summary` | Aggregated statistics | projectId, repositoryId |
| `find_related_context` | Graph + semantic search | entityType, entityId, query, maxDepth |

## Frontend Integration

### GraphExplorer Component

| Tab | Functionality |
|-----|---------------|
| Explore | Entity/relationship/path exploration with filters |
| Summary | Repository statistics (counts, density, recent activity) |
| Search | Graph + semantic search combination |

### State Management

- Project-scoped queries
- Real-time loading/error/empty states
- Pagination for large result sets
- Accessible non-canvas representation

## Security & Isolation

| Boundary | Protection |
|----------|------------|
| Project isolation | All queries scoped to projectId |
| Entity validation | Project ownership verified on every query |
| Relationship validation | Type compatibility checked on creation |
| Input validation | Zod schemas for all inputs |
| Output sanitization | Embeddings stripped from responses |
| Rate limiting | MaxDepth=3, MaxResults=50 |

## Observability

### Metrics Published

| Metric | Namespace | Dimensions |
|--------|-----------|------------|
| GraphEntitiesCreated | FlowSync | ProjectId |
| GraphRelationshipsCreated | FlowSync | ProjectId |
| GraphQueryLatency | FlowSync | ProjectId, Tool |
| GraphTraversalDepth | FlowSync | ProjectId |
| GraphIngestionErrors | FlowSync | ProjectId |

### Structured Logging

All operations log with correlationId:
- Graph ingestion started/completed/failed
- Query executed with parameters
- Traversal depth and results
- Errors with full context

## Deployment

### Infrastructure (CDK)

- **Stack**: `FlowSyncStack`
- **Tables**: Graph entities + relationships with GSIs
- **Lambdas**: AI Processing (graph ingestion), MCP (graph tools)
- **IAM**: Least-privilege table access
- **Monitoring**: CloudWatch dashboards + alarms

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GRAPH_ENTITIES_TABLE` | Graph entities table | `flowsync-graph-entities` |
| `GRAPH_RELATIONSHIPS_TABLE` | Graph relationships table | `flowsync-graph-relationships` |
| `GRAPH_SCHEMA_VERSION` | Schema version | `1` |
| `MAX_GRAPH_DEPTH` | Max traversal depth | `3` |
| `MAX_GRAPH_RESULTS` | Max traversal results | `50` |

## Testing

### Test Coverage

| Component | Tests | Coverage |
|-----------|-------|----------|
| Graph Repository | 30 tests | Entity/rel CRUD, traversal, deletion, helpers |
| Graph Ingestion | 6 tests | Push/merge events, decisions, idempotency |
| Graph RAG | 8 tests | Entity/rel fetch, expansion, RAG integration |
| MCP Tools | 11 tests | All 5 graph tools, isolation, edge cases |
| Handler (Phase 2) | 19 tests | Idempotency, batch, webhook, errors |
| Shared Package | 26 tests | Schemas, validation, IDs, paths |

### Test Commands

```bash
# All tests
pytest test/ -v

# Specific modules
pytest test/test_graph_repository.py -v
pytest test/test_graph_ingestion.py -v
pytest test/test_graph_rag.py -v
pytest test/test_handler.py -v

# MCP tests
cd ../mcp && pytest test/test_graph_tools.py -v

# Frontend
cd ../frontend && npm run build && npm run lint
```

## Limitations & Future Work

### Current Limitations

1. **No real-time sync**: Graph updates only via event ingestion
2. **Limited Git metadata**: No branch/commit graph, only explicit relationships
3. **No cross-repo graphs**: Project-scoped only
4. **Inferred relationships**: Limited to explicit + simple heuristics
4. **No schema migration**: Version 1 only

### Planned Enhancements

| Feature | Priority | Effort |
|---------|----------|--------|
| Git commit graph (has_parent) | High | Medium |
| Cross-repository graphs | Medium | High |
| Schema migration framework | Medium | Medium |
| Interactive graph visualization | Low | High |
| Natural language graph queries | Low | High |
| Change impact analysis | High | Medium |

## Conclusion

Phase 3 delivers a production-ready Engineering Knowledge Graph that:
- **Persists** engineering artifacts and relationships in DynamoDB
- **Ingests** from existing event pipeline with idempotency
- **Queries** via bounded graph traversal with project isolation
- **Enhances** RAG with graph-aware context expansion
- **Exposes** via MCP tools for AI assistants
- **Visualizes** via React frontend component

The graph transforms CodeAtlas from a simple context store into an engineering intelligence platform that understands not just *what* changed, but *how* changes relate across the codebase.