"""
FlowSync Shared Helpers
Reusable functions for MCP and Query Lambda functions.
"""

import json
import os
import hashlib
import boto3
import math
from decimal import Decimal


# Model configuration
EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v1"  # Using v1 for compatibility with existing embeddings
MODEL_ID = "us.amazon.nova-pro-v1:0"
FALLBACK_MODEL_ID = os.environ.get("FALLBACK_MODEL_ID", "us.amazon.nova-lite-v1:0")


def convert_decimals(obj):
    """Convert Decimal objects to int/float for JSON serialization."""
    if isinstance(obj, list):
        return [convert_decimals(item) for item in obj]
    elif isinstance(obj, dict):
        return {key: convert_decimals(value) for key, value in obj.items()}
    elif isinstance(obj, Decimal):
        # Convert to int if it's a whole number, otherwise float
        if obj % 1 == 0:
            return int(obj)
        else:
            return float(obj)
    else:
        return obj


def respond(status_code, body):
    """Build standard API Gateway response."""
    # Convert Decimals in body before JSON serialization
    body = convert_decimals(body)
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type,Authorization',
        },
        'body': json.dumps(body)
    }


def call_titan_embedding(text, bedrock_client):
    """Generate 1536-dimensional embedding using Titan."""
    response = bedrock_client.invoke_model(
        modelId=EMBEDDING_MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({"inputText": text})
    )
    result = json.loads(response["body"].read())
    embedding = result.get("embedding")
    if not embedding or len(embedding) != 1536:
        raise ValueError("Titan embedding output shape invalid.")
    return embedding


def cosine_similarity(vec_a, vec_b):
    """Calculate cosine similarity between two vectors."""
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def strip_embeddings(records):
    """Remove embedding field from context records (reduces response size by ~12KB per record)."""
    if isinstance(records, dict):
        records = [records]
    for record in records:
        record.pop('embedding', None)
    # Also convert Decimal objects to regular numbers for JSON serialization
    records = convert_decimals(records)
    return records


def convert_floats_to_decimal(obj):
    """Convert all float values to Decimal for DynamoDB compatibility."""
    if isinstance(obj, list):
        return [convert_floats_to_decimal(item) for item in obj]
    elif isinstance(obj, dict):
        return {key: convert_floats_to_decimal(value) for key, value in obj.items()}
    elif isinstance(obj, float):
        return Decimal(str(obj))
    else:
        return obj


def check_cache(dynamodb, cache_table_name, cache_key):
    """Check DynamoDB cache for a RAG response. Returns cached response dict or None."""
    try:
        table = dynamodb.Table(cache_table_name)
        item = table.get_item(Key={'cacheKey': cache_key}).get('Item')
        if item:
            print(f"Cache HIT: {cache_key[:16]}...")
            return convert_decimals(dict(item.get('response', {})))
    except Exception as e:
        print(f"Cache check failed (non-fatal): {str(e)}")
    return None


def write_cache(dynamodb, cache_table_name, cache_key, response, ttl_seconds=3600):
    """Write a RAG response to the DynamoDB cache with 1-hour TTL."""
    import time
    try:
        table = dynamodb.Table(cache_table_name)
        table.put_item(Item={
            'cacheKey': cache_key,
            'response': convert_floats_to_decimal(response),
            'expiresAt': int(time.time()) + ttl_seconds,
        })
        print(f"Cache WRITE: {cache_key[:16]}...")
    except Exception as e:
        print(f"Cache write failed (non-fatal): {str(e)}")


def search_context_rag(project_id, query, branch, bedrock_client, dynamodb, context_table_name, cache_table_name=None):
    """
    RAG pipeline for semantic search + answer generation.

    Steps:
    1. Check DynamoDB cache (if cache_table_name provided)
    2. Embed query using Titan
    3. Fetch all context records for project (paginated)
    4. Compute cosine similarity; apply branch-affinity boost if branch requested
    5. Take top 5 results
    6. Feed top 5 to Nova Pro for answer generation (falls back to FALLBACK_MODEL_ID on throttle)
    7. Write result to cache
    8. Return answer + source citations
    """
    # Step 1: Check cache before running the expensive pipeline
    cache_key = None
    if cache_table_name:
        cache_key = hashlib.sha256(f"{project_id}:{query}:{branch or 'all'}".encode()).hexdigest()
        cached = check_cache(dynamodb, cache_table_name, cache_key)
        if cached:
            cached['cached'] = True
            return cached

    # Step 2: Embed query
    query_embedding = call_titan_embedding(query, bedrock_client)

    # Step 3: Fetch context records (paginate to get ALL records, not just first DDB page)
    table = dynamodb.Table(context_table_name)
    records = []

    if branch:
        # Query BranchContextIndex for specific branch
        kwargs = {
            'IndexName': 'BranchContextIndex',
            'KeyConditionExpression': 'projectId = :pk AND begins_with(branchExtractedAt, :prefix)',
            'ExpressionAttributeValues': {
                ':pk': project_id,
                ':prefix': f'{branch}#'
            }
        }
    else:
        # Query ProjectContextIndex for all branches
        kwargs = {
            'IndexName': 'ProjectContextIndex',
            'KeyConditionExpression': 'projectId = :pk',
            'ExpressionAttributeValues': {':pk': project_id}
        }

    while True:
        response = table.query(**kwargs)
        records.extend(response.get('Items', []))
        last_key = response.get('LastEvaluatedKey')
        if not last_key:
            break
        kwargs['ExclusiveStartKey'] = last_key
    
    if not records:
        return {
            'answer': 'No context records found for this project.',
            'answerGrounded': False,
            'sources': []
        }
    
    # Step 4: Compute similarities (convert Decimal embeddings to float)
    # When no branch is specified, apply a 0.85× score penalty to non-main records
    # to prevent cross-branch pollution from dominating results.
    CROSS_BRANCH_PENALTY = 0.85
    similarities = []
    for record in records:
        raw_embedding = record.get('embedding')
        if not raw_embedding:
            continue
        embedding = [float(x) for x in raw_embedding]
        if len(embedding) != 1536:
            continue
        score = cosine_similarity(query_embedding, embedding)
        # Apply branch affinity: penalise records not on the requested/main branch
        if not branch:
            rec_branch = record.get('branch', 'main')
            if rec_branch != 'main':
                score *= CROSS_BRANCH_PENALTY
        similarities.append((record, score))
    
    # Step 5: Top 5 results by similarity
    top_results = sorted(similarities, key=lambda x: x[1], reverse=True)[:5]
    
    if not top_results:
        return {
            'answer': 'No relevant context found for this query.',
            'answerGrounded': False,
            'sources': []
        }
    
    # Step 6: Build RAG prompt and call Nova Pro (with fallback to FALLBACK_MODEL_ID on throttle)
    context_text = []
    sources = []
    
    for record, score in top_results:
        context_text.append(json.dumps({
            'feature': record.get('feature'),
            'decision': record.get('decision'),
            'tasks': record.get('tasks'),
            'stage': record.get('stage'),
            'risk': record.get('risk'),
            'author': record.get('author'),
            'commitHash': record.get('commitHash'),
            'extractedAt': record.get('extractedAt')
        }, indent=2))
        
        sources.append({
            'eventId': record.get('eventId'),
            'contextId': record.get('contextId'),
            'branch': record.get('branch'),
            'timestamp': record.get('timestamp'),
            'commitHash': record.get('commitHash'),
            'feature': record.get('feature'),
            'stage': record.get('stage'),
            'extractedAt': record.get('extractedAt'),
            'snippet': f"{record.get('feature') or 'N/A'} - {(record.get('decision') or 'N/A')[:100]}...",
            'relevance': round(score, 4)
        })
    
    system_prompt = (
        "You are a helpful assistant that answers questions about software projects. "
        "Answer ONLY using the provided context records. "
        "If the answer is not in the context, say 'I don't have enough context to answer that.' "
        "Be specific and cite relevant details from the context."
    )
    
    user_prompt = f"""Question: {query}

Context records (most relevant first):
{chr(10).join(context_text)}

Return a JSON object with this exact structure:
{{
  "answer": "your answer here",
  "answerGrounded": true or false (false if you couldn't answer from context),
  "citedSources": [array of commitHash values you referenced, empty if none]
}}"""
    
    # Call Nova Pro via Converse API; fall back to FALLBACK_MODEL_ID on throttling
    try:
        try:
            response = bedrock_client.converse(
                modelId=MODEL_ID,
                system=[{"text": system_prompt}],
                messages=[{"role": "user", "content": [{"text": user_prompt}]}],
                inferenceConfig={"maxTokens": 2000, "temperature": 0.3, "topP": 1}
            )
        except Exception as throttle_err:
            err_code = getattr(getattr(throttle_err, 'response', {}).get('Error', {}), 'get', lambda k, d=None: d)('Code', '')
            # botocore ClientError stores error code in response dict
            if hasattr(throttle_err, 'response') and throttle_err.response.get('Error', {}).get('Code', '') in (
                'ThrottlingException', 'ModelTimeoutException', 'ServiceUnavailableException'
            ):
                print(f"Nova Pro throttled, falling back to {FALLBACK_MODEL_ID}")
                response = bedrock_client.converse(
                    modelId=FALLBACK_MODEL_ID,
                    system=[{"text": system_prompt}],
                    messages=[{"role": "user", "content": [{"text": user_prompt}]}],
                    inferenceConfig={"maxTokens": 2000, "temperature": 0.3, "topP": 1}
                )
            else:
                raise
        
        output_text = response['output']['message']['content'][0]['text'].strip()
        
        # Strip markdown code fences if present
        if output_text.startswith('```json'):
            output_text = output_text.split('```json')[1].split('```')[0].strip()
        elif output_text.startswith('```'):
            output_text = output_text.split('```')[1].split('```')[0].strip()
        
        result = json.loads(output_text)
        
        # Step 7: Build final response and write to cache
        final_response = {
            'answer': result.get('answer', 'Unable to generate answer.'),
            'answerGrounded': result.get('answerGrounded', False),
            'sources': sources
        }
        if cache_key and cache_table_name:
            write_cache(dynamodb, cache_table_name, cache_key, final_response)
        return final_response
    except Exception as e:
        print(f"Error calling Nova Pro for RAG: {str(e)}")
        return {
            'answer': f'Error generating answer: {str(e)}',
            'answerGrounded': False,
            'sources': sources
        }


# ─────────────────────────────────────────────────────────────────────────────
# GRAPH-AWARE RAG (Phase 3)
# ─────────────────────────────────────────────────────────────────────────────

GRAPH_ENTITIES_TABLE = "flowsync-graph-entities"
GRAPH_RELATIONSHIPS_TABLE = "flowsync-graph-relationships"
MAX_GRAPH_DEPTH = 2
MAX_GRAPH_ENTITIES = 20
MAX_GRAPH_RELATIONSHIPS = 30


def get_graph_entities_table(dynamodb):
    return dynamodb.Table(GRAPH_ENTITIES_TABLE)


def get_graph_relationships_table(dynamodb):
    return dynamodb.Table(GRAPH_RELATIONSHIPS_TABLE)


def fetch_graph_entity(dynamodb, entity_id):
    """Fetch a single graph entity by ID."""
    try:
        table = get_graph_entities_table(dynamodb)
        response = table.get_item(Key={'entityId': entity_id})
        return response.get('Item')
    except Exception as e:
        print(f"[graph] Error fetching entity {entity_id}: {e}")
        return None


def fetch_graph_entities_batch(dynamodb, entity_ids):
    """Batch fetch multiple graph entities."""
    if not entity_ids:
        return []
    results = []
    for i in range(0, len(entity_ids), 100):
        batch = entity_ids[i:i+100]
        try:
            response = dynamodb.batch_get_item(
                RequestItems={
                    GRAPH_ENTITIES_TABLE: {
                        'Keys': [{'entityId': eid} for eid in batch]
                    }
                }
            )
            items = response.get('Responses', {}).get(GRAPH_ENTITIES_TABLE, [])
            results.extend(items)
        except Exception as e:
            print(f"[graph] Error batch fetching entities: {e}")
    return results


def fetch_outgoing_relationships(dynamodb, source_entity_id, limit=50):
    """Fetch outgoing relationships from an entity."""
    try:
        table = get_graph_relationships_table(dynamodb)
        kwargs = {
            'IndexName': 'SourceEntityIndex',
            'KeyConditionExpression': 'sourceEntityId = :seid',
            'ExpressionAttributeValues': {':seid': source_entity_id},
            'ScanIndexForward': False,
            'Limit': limit,
        }
        response = table.query(**kwargs)
        return response.get('Items', [])
    except Exception as e:
        print(f"[graph] Error fetching outgoing relationships: {e}")
        return []


def fetch_incoming_relationships(dynamodb, target_entity_id, limit=50):
    """Fetch incoming relationships to an entity."""
    try:
        table = get_graph_relationships_table(dynamodb)
        kwargs = {
            'IndexName': 'TargetEntityIndex',
            'KeyConditionExpression': 'targetEntityId = :teid',
            'ExpressionAttributeValues': {':teid': target_entity_id},
            'ScanIndexForward': False,
            'Limit': limit,
        }
        response = table.query(**kwargs)
        return response.get('Items', [])
    except Exception as e:
        print(f"[graph] Error fetching incoming relationships: {e}")
        return []


def expand_context_with_graph(
    dynamodb,
    project_id,
    top_records,
    max_depth=MAX_GRAPH_DEPTH,
    max_entities=MAX_GRAPH_ENTITIES,
    max_relationships=MAX_GRAPH_RELATIONSHIPS
):
    """
    Expand context records with related graph entities and relationships.
    
    For each top semantic result, traverse the graph up to max_depth to find
    related entities (commits, PRs, files, decisions) and include them as
    additional context for the RAG prompt.
    """
    if not top_records:
        return top_records, [], []
    
    # Collect entity IDs from top records (context records have commitHash)
    seed_entity_ids = []
    for record, _ in top_records:
        commit_hash = record.get('commitHash')
        if commit_hash:
            # Try to find the commit entity in the graph
            # We need to derive repository_id from context
            # For now, we'll use a project-scoped repository ID
            repository_id = f"repo:project:{project_id}"
            commit_id = f"commit:{repository_id}:{commit_hash.lower()}"
            seed_entity_ids.append(commit_id)
    
    if not seed_entity_ids:
        return top_records, [], []
    
    # Traverse graph from seed entities
    visited_entities = set()
    visited_relationships = set()
    discovered_entities = {}
    discovered_relationships = []
    paths = []
    
    def traverse(entity_id, depth, path_prefix):
        if depth > max_depth or len(discovered_entities) >= max_entities:
            return
        if entity_id in visited_entities:
            return
        visited_entities.add(entity_id)
        
        entity = fetch_graph_entity(dynamodb, entity_id)
        if entity:
            discovered_entities[entity_id] = entity
            paths.append({
                'entityId': entity_id,
                'entityType': entity.get('entityType'),
                'path': path_prefix,
                'depth': depth,
            })
            
            if depth < max_depth:
                # Fetch outgoing relationships
                outgoing = fetch_outgoing_relationships(dynamodb, entity_id)
                for rel in outgoing[:max_relationships]:
                    rel_id = rel.get('relationshipId')
                    if rel_id not in visited_relationships:
                        visited_relationships.add(rel_id)
                        discovered_relationships.append(rel)
                        
                        # Traverse to target entity
                        target_id = rel.get('targetEntityId')
                        if target_id:
                            traverse(target_id, depth + 1, path_prefix + [rel_id])
                
                # Fetch incoming relationships
                incoming = fetch_incoming_relationships(dynamodb, entity_id)
                for rel in incoming[:max_relationships]:
                    rel_id = rel.get('relationshipId')
                    if rel_id not in visited_relationships:
                        visited_relationships.add(rel_id)
                        discovered_relationships.append(rel)
                        
                        # Traverse to source entity
                        source_id = rel.get('sourceEntityId')
                        if source_id:
                            traverse(source_id, depth + 1, path_prefix + [rel_id])
    
    for seed_id in seed_entity_ids:
        traverse(seed_id, 0, [])
    
    # Convert discovered entities to context-like format for RAG
    graph_context_records = []
    for entity in discovered_entities.values():
        if entity.get('entityType') == 'commit':
            graph_context_records.append({
                'feature': f"Commit: {entity.get('sha', '')[:8]}",
                'decision': entity.get('message', ''),
                'tasks': [],
                'stage': 'Commit',
                'risk': None,
                'author': entity.get('author'),
                'commitHash': entity.get('sha'),
                'extractedAt': entity.get('committedAt'),
                '_graph_entity': True,
                '_graph_entity_type': entity.get('entityType'),
            })
        elif entity.get('entityType') == 'pull_request':
            graph_context_records.append({
                'feature': f"PR #{entity.get('number')}: {entity.get('title', '')}",
                'decision': entity.get('description', ''),
                'tasks': [],
                'stage': 'Pull Request',
                'risk': None,
                'author': entity.get('author'),
                'commitHash': entity.get('merge_commit_sha', ''),
                'extractedAt': entity.get('createdAt'),
                '_graph_entity': True,
                '_graph_entity_type': entity.get('entityType'),
            })
        elif entity.get('entityType') == 'file':
            graph_context_records.append({
                'feature': f"File: {entity.get('path', '')}",
                'decision': '',
                'tasks': [],
                'stage': 'File',
                'risk': None,
                'author': '',
                'commitHash': '',
                'extractedAt': entity.get('lastObservedAt'),
                '_graph_entity': True,
                '_graph_entity_type': entity.get('entityType'),
            })
        elif entity.get('entityType') == 'engineering_decision':
            graph_context_records.append({
                'feature': entity.get('title', ''),
                'decision': entity.get('decision', ''),
                'tasks': [],
                'stage': 'Engineering Decision',
                'risk': entity.get('risk'),
                'author': entity.get('sourceType', ''),
                'commitHash': '',
                'extractedAt': entity.get('createdAt'),
                '_graph_entity': True,
                '_graph_entity_type': entity.get('entityType'),
            })
    
    return top_records, graph_context_records, discovered_relationships


def search_context_rag_with_graph(
    project_id,
    query,
    branch,
    bedrock_client,
    dynamodb,
    context_table_name,
    cache_table_name=None,
    use_graph=True
):
    """
    Enhanced RAG pipeline with graph-aware context expansion.
    
    This extends the base search_context_rag by:
    1. Running semantic search as usual
    2. Expanding results with related graph entities
    3. Combining semantic and graph context for answer generation
    """
    # First, run the base RAG to get semantic results
    base_result = search_context_rag(
        project_id=project_id,
        query=query,
        branch=branch,
        bedrock_client=bedrock_client,
        dynamodb=dynamodb,
        context_table_name=context_table_name,
        cache_table_name=cache_table_name
    )
    
    # If graph is disabled or no sources, return base result
    if not use_graph or not base_result.get('sources'):
        return base_result
    
    # Extract top records from sources to find seed entities
    # We need to reconstruct the top_records format from sources
    table = dynamodb.Table(context_table_name)
    seed_records = []
    for source in base_result['sources'][:5]:
        event_id = source.get('eventId')
        if event_id:
            # Fetch the full context record
            try:
                response = table.get_item(Key={'eventId': event_id})
                record = response.get('Item')
                if record:
                    # We need the similarity score - approximate from relevance
                    score = source.get('relevance', 0.5)
                    seed_records.append((record, score))
            except Exception as e:
                print(f"[graph] Error fetching record for graph expansion: {e}")
    
    if not seed_records:
        return base_result
    
    # Expand with graph
    try:
        _, graph_context, graph_relationships = expand_context_with_graph(
            dynamodb=dynamodb,
            project_id=project_id,
            top_records=seed_records,
        )
        
        if graph_context:
            # Combine graph context with original sources
            # Add graph entities as additional sources
            for gctx in graph_context:
                base_result['sources'].append({
                    'eventId': gctx.get('commitHash', ''),
                    'contextId': f"graph:{gctx.get('_graph_entity_type', 'unknown')}:{gctx.get('commitHash', '')}",
                    'branch': branch,
                    'timestamp': gctx.get('extractedAt', ''),
                    'commitHash': gctx.get('commitHash', ''),
                    'feature': gctx.get('feature', ''),
                    'stage': gctx.get('stage', ''),
                    'extractedAt': gctx.get('extractedAt', ''),
                    'snippet': f"[Graph] {gctx.get('feature', '')} - {gctx.get('decision', '')[:100]}",
                    'relevance': gctx.get('confidence', 0.5),
                    'provenance': 'graph',
                })
            
            # Regenerate answer with expanded context if we have significant graph additions
            if len(graph_context) > 0:
                # Re-run the answer generation with combined context
                return _regenerate_answer_with_graph_context(
                    query=query,
                    base_result=base_result,
                    graph_context=graph_context,
                    graph_relationships=graph_relationships,
                    bedrock_client=bedrock_client,
                    cache_table_name=cache_table_name,
                    project_id=project_id,
                    dynamodb=dynamodb
                )
    except Exception as e:
        print(f"[graph] Graph expansion failed (non-fatal): {e}")
    
    return base_result


def _regenerate_answer_with_graph_context(
    query,
    base_result,
    graph_context,
    graph_relationships,
    bedrock_client,
    cache_table_name,
    project_id,
    dynamodb
):
    """Regenerate answer with combined semantic + graph context."""
    try:
        # Build combined context text
        context_text = []
        all_sources = list(base_result['sources'])
        
        # Add original context records
        for source in base_result['sources'][:5]:
            if not source.get('provenance') == 'graph':
                context_text.append(json.dumps({
                    'feature': source.get('feature'),
                    'decision': source.get('feature'),  # Using feature as decision fallback
                    'tasks': [],
                    'stage': source.get('stage'),
                    'risk': None,
                    'author': '',
                    'commitHash': source.get('commitHash'),
                    'extractedAt': source.get('extractedAt')
                }, indent=2))
        
        # Add graph context
        for gctx in graph_context:
            context_text.append(json.dumps({
                'feature': gctx.get('feature'),
                'decision': gctx.get('decision'),
                'tasks': gctx.get('tasks', []),
                'stage': gctx.get('stage'),
                'risk': gctx.get('risk'),
                'author': gctx.get('author'),
                'commitHash': gctx.get('commitHash'),
                'extractedAt': gctx.get('extractedAt'),
                '_provenance': 'graph',
                '_graph_entity_type': gctx.get('_graph_entity_type'),
            }, indent=2))
        
        # Build enhanced prompt
        system_prompt = (
            "You are a helpful assistant that answers questions about software projects. "
            "Answer ONLY using the provided context records. "
            "Context records marked with '_provenance': 'graph' come from the engineering knowledge graph "
            "and represent relationships between commits, pull requests, files, and decisions. "
            "If the answer is not in the context, say 'I don't have enough context to answer that.' "
            "Be specific and cite relevant details from the context. "
            "Distinguish between facts from semantic search and graph-derived relationships."
        )
        
        user_prompt = f"""Question: {query}

Context records (most relevant first):
{chr(10).join(context_text)}

Return a JSON object with this exact structure:
{{
  "answer": "your answer here",
  "answerGrounded": true or false (false if you couldn't answer from context),
  "citedSources": [array of commitHash values you referenced, empty if none]
}}"""
        
        # Call Nova Pro
        try:
            response = bedrock_client.converse(
                modelId=MODEL_ID,
                system=[{"text": system_prompt}],
                messages=[{"role": "user", "content": [{"text": user_prompt}]}],
                inferenceConfig={"maxTokens": 2000, "temperature": 0.3, "topP": 1}
            )
        except Exception as throttle_err:
            if hasattr(throttle_err, 'response') and throttle_err.response.get('Error', {}).get('Code', '') in (
                'ThrottlingException', 'ModelTimeoutException', 'ServiceUnavailableException'
            ):
                print(f"Nova Pro throttled, falling back to {FALLBACK_MODEL_ID}")
                response = bedrock_client.converse(
                    modelId=FALLBACK_MODEL_ID,
                    system=[{"text": system_prompt}],
                    messages=[{"role": "user", "content": [{"text": user_prompt}]}],
                    inferenceConfig={"maxTokens": 2000, "temperature": 0.3, "topP": 1}
                )
            else:
                raise
        
        output_text = response['output']['message']['content'][0]['text'].strip()
        
        if output_text.startswith('```json'):
            output_text = output_text.split('```json')[1].split('```')[0].strip()
        elif output_text.startswith('```'):
            output_text = output_text.split('```')[1].split('```')[0].strip()
        
        result = json.loads(output_text)
        
        final_response = {
            'answer': result.get('answer', 'Unable to generate answer.'),
            'answerGrounded': result.get('answerGrounded', False),
            'sources': all_sources
        }
        
        # Update cache
        import hashlib
        cache_key = hashlib.sha256(f"{project_id}:{query}:{branch or 'all'}:graph".encode()).hexdigest()
        if cache_table_name:
            write_cache(dynamodb, cache_table_name, cache_key, final_response)
        
        return final_response
    except Exception as e:
        print(f"[graph] Answer regeneration failed: {e}")
        return base_result
