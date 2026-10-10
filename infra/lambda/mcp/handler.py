"""
FlowSync MCP Lambda
Model Context Protocol (MCP) server for AI agents (Copilot, Claude Desktop).
Provides 4 tools: get_project_context, get_recent_changes, search_context, log_context.
Phase 3 adds graph tools: query_knowledge_graph, get_related_changes, get_engineering_decisions, get_repository_graph_summary, find_related_context.
"""

import json
import boto3
import os
import base64
from datetime import datetime, timedelta
from decimal import Decimal
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError
from flowsync_common.helpers import respond, strip_embeddings, search_context_rag, search_context_rag_with_graph, convert_floats_to_decimal, call_titan_embedding
from flowsync_common.auth import authenticate

# Environment variables
CONTEXT_TABLE = os.environ.get("CONTEXT_TABLE", "flowsync-context")
PROJECTS_TABLE = os.environ.get("PROJECTS_TABLE", "flowsync-projects")
AUDIT_TABLE = os.environ.get("AUDIT_TABLE", "flowsync-audit")
CACHE_TABLE = os.environ.get("CACHE_TABLE", "")
GRAPH_ENTITIES_TABLE = os.environ.get("GRAPH_ENTITIES_TABLE", "flowsync-graph-entities")
GRAPH_RELATIONSHIPS_TABLE = os.environ.get("GRAPH_RELATIONSHIPS_TABLE", "flowsync-graph-relationships")

# AWS clients — Bedrock with adaptive retry (handles ThrottlingException automatically)
_bedrock_retry_config = BotoConfig(retries={'max_attempts': 3, 'mode': 'adaptive'})
bedrock_client = boto3.client("bedrock-runtime", config=_bedrock_retry_config)
dynamodb = boto3.resource("dynamodb")
cloudwatch = boto3.client("cloudwatch")


def publish_metric(metric_name, value, project_id):
    """Publish CloudWatch metric for monitoring."""
    try:
        cloudwatch.put_metric_data(
            Namespace='FlowSync',
            MetricData=[{
                'MetricName': metric_name,
                'Value': value,
                'Unit': 'Count',
                'Dimensions': [{'Name': 'ProjectId', 'Value': project_id}]
            }]
        )
    except Exception as e:
        print(f"Failed to publish metric: {str(e)}")


def get_project_context(params):
    """
    Tool 1: Get context records for a branch with parent branch inheritance.
    
    Params:
      - projectId (required)
      - branch (required)
      - limit (optional, default 10, max 50)
      - nextToken (optional, pagination cursor from previous response)
    
    Returns: {recentContext: [...], nextToken?: string}
    """
    project_id = params.get('projectId')
    branch = params.get('branch', 'main')
    limit = min(int(params.get('limit', 10)), 50)
    next_token = params.get('nextToken')
    
    if not project_id:
        return respond(400, {'error': 'bad_request', 'message': 'Missing projectId'})
    
    table = dynamodb.Table(CONTEXT_TABLE)
    
    # Build query kwargs
    query_kwargs = {
        'IndexName': 'BranchContextIndex',
        'KeyConditionExpression': 'projectId = :pk AND begins_with(branchExtractedAt, :prefix)',
        'ExpressionAttributeValues': {
            ':pk': project_id,
            ':prefix': f'{branch}#'
        },
        'ScanIndexForward': False,
        'Limit': limit
    }
    
    if next_token:
        query_kwargs['ExclusiveStartKey'] = json.loads(base64.b64decode(next_token).decode())
    
    response = table.query(**query_kwargs)
    branch_records = response.get('Items', [])
    
    # Encode next page token if more results exist
    next_token_out = None
    if 'LastEvaluatedKey' in response:
        next_token_out = base64.b64encode(
            json.dumps(response['LastEvaluatedKey']).encode()
        ).decode()
    
    # If not main branch, also fetch main branch context for inheritance
    all_records = branch_records
    if branch != 'main':
        main_response = table.query(
            IndexName='BranchContextIndex',
            KeyConditionExpression='projectId = :pk AND begins_with(branchExtractedAt, :prefix)',
            ExpressionAttributeValues={
                ':pk': project_id,
                ':prefix': 'main#'
            },
            ScanIndexForward=False,
            Limit=limit
        )
        
        main_records = main_response.get('Items', [])
        
        # Merge: branch records override main records by feature name
        branch_features = {r['feature']: r for r in branch_records}
        for main_record in main_records:
            feature = main_record['feature']
            if feature not in branch_features:
                all_records.append(main_record)
        
        # Sort and limit to requested page size
        all_records = sorted(all_records, key=lambda x: x['extractedAt'], reverse=True)[:limit]
    
    # Strip embeddings
    all_records = strip_embeddings(all_records)
    
    result = {'recentContext': all_records}
    if next_token_out:
        result['nextToken'] = next_token_out
    
    return respond(200, result)


def get_recent_changes(params):
    """
    Tool 2: Get last N context records chronologically.
    
    Params:
      - projectId (required)
      - branch (optional)
      - limit (optional, default 10, max 50)
      - since (optional, ISO 8601 timestamp — return only records after this time)
    
    Returns: {changes: [...]}
    """
    project_id = params.get('projectId')
    branch = params.get('branch')
    limit = min(int(params.get('limit', 10)), 50)
    since = params.get('since')  # ISO 8601 timestamp filter
    
    if not project_id:
        return respond(400, {'error': 'bad_request', 'message': 'Missing projectId'})
    
    table = dynamodb.Table(CONTEXT_TABLE)
    
    # Build expression values and optional filter
    expr_values = {':pk': project_id}
    if since:
        expr_values[':since'] = since
    
    if branch:
        expr_values[':prefix'] = f'{branch}#'
        query_kwargs = {
            'IndexName': 'BranchContextIndex',
            'KeyConditionExpression': 'projectId = :pk AND begins_with(branchExtractedAt, :prefix)',
            'ExpressionAttributeValues': expr_values,
            'ScanIndexForward': False,
            'Limit': limit
        }
    else:
        query_kwargs = {
            'IndexName': 'ProjectContextIndex',
            'KeyConditionExpression': 'projectId = :pk',
            'ExpressionAttributeValues': expr_values,
            'ScanIndexForward': False,
            'Limit': limit
        }
    
    if since:
        query_kwargs['FilterExpression'] = 'extractedAt >= :since'
    
    response = table.query(**query_kwargs)
    records = response.get('Items', [])
    records = strip_embeddings(records)
    
    return respond(200, {'changes': records})


def search_context(params):
    """
    Tool 3: Semantic search with RAG answer generation.
    
    Params:
      - projectId (required)
      - query (required)
      - branch (optional)
    
    Returns: {answer, answerGrounded, sources: [...]}
    """
    project_id = params.get('projectId')
    query = params.get('query')
    branch = params.get('branch')
    
    if not project_id:
        return respond(400, {'error': 'bad_request', 'message': 'Missing projectId'})
    if not query:
        return respond(400, {'error': 'bad_request', 'message': 'Missing query'})
    
    # Call shared RAG pipeline
    try:
        result = search_context_rag(
            project_id=project_id,
            query=query,
            branch=branch,
            bedrock_client=bedrock_client,
            dynamodb=dynamodb,
            context_table_name=CONTEXT_TABLE,
            cache_table_name=CACHE_TABLE or None
        )
        return respond(200, result)
    except Exception as e:
        print(f"Error in search_context: {str(e)}")
        publish_metric('MCPToolFailure', 1, project_id)
        return respond(500, {'error': 'search_failed', 'message': str(e)})


def log_context(params):
    """
    Tool 4: Agent writes reasoning to context (MCP write operation).
    
    Params:
      - projectId (required)
      - branch (required)
      - author (required)
      - reasoning (required)
      - decision (optional)
      - tasks (optional)
      - risk (optional)
    
    Returns: {success: true, eventId: "..."}
    """
    project_id = params.get('projectId')
    branch = params.get('branch', 'main')
    author = params.get('author')
    reasoning = params.get('reasoning')
    decision = params.get('decision')
    tasks = params.get('tasks', [])
    risk = params.get('risk')
    
    if not project_id or not author or not reasoning:
        return respond(400, {
            'error': 'bad_request',
            'message': 'Missing required params: projectId, author, reasoning'
        })
    
    table = dynamodb.Table(CONTEXT_TABLE)
    timestamp = datetime.utcnow().isoformat() + 'Z'
    
    # Find existing complete record (same projectId, branch, author, within 30 min)
    time_obj = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    window_start = (time_obj - timedelta(minutes=30)).isoformat().replace('+00:00', 'Z')
    
    try:
        response = table.query(
            IndexName='BranchContextIndex',
            KeyConditionExpression='projectId = :pk AND branchExtractedAt BETWEEN :start AND :end',
            FilterExpression='author = :author AND #status = :complete',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':pk': project_id,
                ':start': f"{branch}#{window_start}",
                ':end': f"{branch}#{timestamp}",
                ':author': author,
                ':complete': 'complete'
            },
            Limit=1,
            ScanIndexForward=False
        )
        
        existing = response.get('Items')
        
        if existing:
            # Update existing record with agent reasoning
            event_id = existing[0]['eventId']
            
            update_expr = "SET agentReasoning = :reasoning"
            expr_values = {':reasoning': reasoning}
            
            if decision:
                update_expr += ", decision = :decision"
                expr_values[':decision'] = decision
            
            if tasks:
                update_expr += ", tasks = :tasks"
                expr_values[':tasks'] = tasks
            
            if risk:
                update_expr += ", risk = :risk"
                expr_values[':risk'] = risk
            
            table.update_item(
                Key={'eventId': event_id},
                UpdateExpression=update_expr,
                ExpressionAttributeValues=expr_values
            )

            # Re-embed after enrichment so RAG search sees the updated content
            try:
                updated = table.get_item(Key={'eventId': event_id}).get('Item', {})
                embed_text = json.dumps({
                    'feature':        updated.get('feature', ''),
                    'decision':       updated.get('decision', ''),
                    'risk':           updated.get('risk', ''),
                    'tasks':          updated.get('tasks', []),
                    'agentReasoning': reasoning,
                })
                new_embedding = call_titan_embedding(embed_text, bedrock_client)
                table.update_item(
                    Key={'eventId': event_id},
                    UpdateExpression='SET embedding = :emb',
                    ExpressionAttributeValues={':emb': convert_floats_to_decimal(new_embedding)}
                )
                print(f'[log_context] Re-embedded record {event_id} after enrichment')
            except Exception as emb_err:
                # Non-fatal: enrichment was written successfully; only search ranking is affected
                print(f'[log_context] WARNING: re-embed failed for {event_id}: {emb_err}')

            # Write audit record
            audit_table = dynamodb.Table(AUDIT_TABLE)
            audit_table.put_item(Item={
                'entityId': event_id,
                'timestamp': timestamp,
                'action': 'agent_reasoning_added',
                'projectId': project_id,
                'branch': branch,
                'author': author
            })
            
            return respond(200, {'success': True, 'eventId': event_id, 'action': 'updated'})
        
        else:
            # Create orphaned record (no commit yet)
            import uuid
            event_id = str(uuid.uuid4())
            
            orphaned_record = {
                'eventId': event_id,
                'projectId': project_id,
                'branch': branch,
                'branchExtractedAt': f"{branch}#{timestamp}",
                'commitHash': None,
                'status': 'uncommitted',
                'feature': 'Agent reasoning',
                'decision': decision,
                'tasks': tasks,
                'stage': 'Feature Development',
                'risk': risk,
                'confidence': Decimal('0.5'),
                'entities': [],
                'author': author,
                'agentReasoning': reasoning,
                'modelVersion': 'mcp-agent',
                'embedding': None,
                'extractedAt': timestamp
            }
            
            orphaned_record = convert_floats_to_decimal(orphaned_record)
            table.put_item(Item=orphaned_record)
            
            # Write audit record
            audit_table = dynamodb.Table(AUDIT_TABLE)
            audit_table.put_item(Item={
                'entityId': event_id,
                'timestamp': timestamp,
                'action': 'agent_reasoning_logged',
                'projectId': project_id,
                'branch': branch,
                'author': author
            })
            
            return respond(200, {'success': True, 'eventId': event_id, 'action': 'created'})
    
    except Exception as e:
        print(f"Error in log_context: {str(e)}")
        publish_metric('MCPToolFailure', 1, project_id)
        return respond(500, {'error': 'log_failed', 'message': str(e)})


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 3: GRAPH QUERY TOOLS
# ─────────────────────────────────────────────────────────────────────────────

def get_graph_entities_table():
    return dynamodb.Table(GRAPH_ENTITIES_TABLE)


def get_graph_relationships_table():
    return dynamodb.Table(GRAPH_RELATIONSHIPS_TABLE)


def query_knowledge_graph(params):
    """
    Tool 5: Query the engineering knowledge graph for related entities and relationships.
    
    Params:
      - projectId (required)
      - entityType (optional): repository, commit, pull_request, file, engineering_decision
      - entityId (optional): specific entity to start from
      - relationshipTypes (optional): list of relationship types to filter
      - direction (optional): "outgoing", "incoming", "both" (default: "both")
      - maxDepth (optional): maximum traversal depth (default: 2, max: 3)
      - maxResults (optional): maximum results (default: 20, max: 50)
      - branch (optional): filter by branch context
    
    Returns: {entities: [...], relationships: [...], paths: [...]}
    """
    project_id = params.get('projectId')
    entity_type = params.get('entityType')
    entity_id = params.get('entityId')
    relationship_types = params.get('relationshipTypes')
    direction = params.get('direction', 'both')
    max_depth = min(int(params.get('maxDepth', 2)), 3)
    max_results = min(int(params.get('maxResults', 20)), 50)
    branch = params.get('branch')
    
    if not project_id:
        return respond(400, {'error': 'bad_request', 'message': 'Missing projectId'})
    
    if not entity_id and not entity_type:
        return respond(400, {'error': 'bad_request', 'message': 'Either entityId or entityType must be provided'})
    
    entities_table = get_graph_entities_table()
    relationships_table = get_graph_relationships_table()
    
    try:
        entities = []
        relationships = []
        paths = []
        visited = set()
        
        # If entity_id provided, start traversal from that entity
        if entity_id:
            # Fetch the seed entity
            entity_response = entities_table.get_item(Key={'entityId': entity_id})
            seed_entity = entity_response.get('Item')
            
            if not seed_entity:
                return respond(404, {'error': 'not_found', 'message': f'Entity not found: {entity_id}'})
            
            # Verify project isolation
            if seed_entity.get('projectId') != project_id:
                return respond(403, {'error': 'forbidden', 'message': 'Entity belongs to different project'})
            
            entities.append(seed_entity)
            visited.add(entity_id)
            
            # Traverse graph
            def traverse(current_id, depth, path_prefix):
                if depth > max_depth or len(entities) >= max_results:
                    return
                
                # Get outgoing relationships
                if direction in ('outgoing', 'both'):
                    kwargs = {
                        'IndexName': 'SourceEntityIndex',
                        'KeyConditionExpression': 'sourceEntityId = :seid',
                        'ExpressionAttributeValues': {':seid': current_id},
                        'ScanIndexForward': False,
                        'Limit': max_results,
                    }
                    if relationship_types:
                        kwargs['FilterExpression'] = 'relationshipType IN (:rts)'
                        kwargs['ExpressionAttributeValues'] = {':rts': relationship_types}
                    
                    response = relationships_table.query(**kwargs)
                    for rel in response.get('Items', []):
                        if len(relationships) >= max_results:
                            break
                        relationships.append(rel)
                        
                        target_id = rel.get('targetEntityId')
                        if target_id and target_id not in visited:
                            visited.add(target_id)
                            target_entity = entities_table.get_item(Key={'entityId': target_id}).get('Item')
                            if target_entity and target_entity.get('projectId') == project_id:
                                entities.append(target_entity)
                                paths.append({
                                    'path': path_prefix + [rel.get('relationshipId')],
                                    'entityId': target_id,
                                    'entityType': target_entity.get('entityType'),
                                    'relationshipType': rel.get('relationshipType'),
                                    'direction': 'outgoing',
                                    'depth': depth + 1,
                                    'provenance': rel.get('provenance'),
                                    'confidence': rel.get('confidence'),
                                })
                                traverse(target_id, depth + 1, path_prefix + [rel.get('relationshipId')])
                
                # Get incoming relationships
                if direction in ('incoming', 'both'):
                    kwargs = {
                        'IndexName': 'TargetEntityIndex',
                        'KeyConditionExpression': 'targetEntityId = :teid',
                        'ExpressionAttributeValues': {':teid': current_id},
                        'ScanIndexForward': False,
                        'Limit': max_results,
                    }
                    if relationship_types:
                        kwargs['FilterExpression'] = 'relationshipType IN (:rts)'
                        kwargs['ExpressionAttributeValues'] = {':rts': relationship_types}
                    
                    response = relationships_table.query(**kwargs)
                    for rel in response.get('Items', []):
                        if len(relationships) >= max_results:
                            break
                        relationships.append(rel)
                        
                        source_id = rel.get('sourceEntityId')
                        if source_id and source_id not in visited:
                            visited.add(source_id)
                            source_entity = entities_table.get_item(Key={'entityId': source_id}).get('Item')
                            if source_entity and source_entity.get('projectId') == project_id:
                                entities.append(source_entity)
                                paths.append({
                                    'path': path_prefix + [rel.get('relationshipId')],
                                    'entityId': source_id,
                                    'entityType': source_entity.get('entityType'),
                                    'relationshipType': rel.get('relationshipType'),
                                    'direction': 'incoming',
                                    'depth': depth + 1,
                                    'provenance': rel.get('provenance'),
                                    'confidence': rel.get('confidence'),
                                })
                                traverse(source_id, depth + 1, path_prefix + [rel.get('relationshipId')])
            
            traverse(entity_id, 0, [])
        
        else:
            # Query entities by type within project
            kwargs = {
                'IndexName': 'ProjectEntityIndex',
                'KeyConditionExpression': 'projectId = :pk',
                'ExpressionAttributeValues': {':pk': project_id},
                'ScanIndexForward': False,
                'Limit': max_results,
            }
            
            if entity_type:
                kwargs['FilterExpression'] = 'entityType = :et'
                kwargs['ExpressionAttributeValues'][':et'] = entity_type
            
            response = entities_table.query(**kwargs)
            entities = response.get('Items', [])
        
        # Strip embeddings if present
        entities = strip_embeddings(entities)
        
        return respond(200, {
            'entities': entities,
            'relationships': relationships,
            'paths': paths,
            'count': len(entities),
        })
    
    except Exception as e:
        print(f"Error in query_knowledge_graph: {str(e)}")
        publish_metric('MCPToolFailure', 1, project_id)
        return respond(500, {'error': 'query_failed', 'message': str(e)})


def get_related_changes(params):
    """
    Tool 6: Get changes related to a file, commit, pull request, or context item.
    
    Params:
      - projectId (required)
      - entityType (required): commit, pull_request, file, engineering_decision
      - entityId (required)
      - includeGraph (optional): whether to include graph relationships (default: true)
    
    Returns: {relatedCommits: [...], relatedPRs: [...], relatedFiles: [...], relatedDecisions: [...], relationships: [...]}
    """
    project_id = params.get('projectId')
    entity_type = params.get('entityType')
    entity_id = params.get('entityId')
    include_graph = params.get('includeGraph', True)
    
    if not project_id or not entity_type or not entity_id:
        return respond(400, {'error': 'bad_request', 'message': 'Missing required params: projectId, entityType, entityId'})
    
    entities_table = get_graph_entities_table()
    relationships_table = get_graph_relationships_table()
    
    try:
        # Verify entity exists and belongs to project
        entity_response = entities_table.get_item(Key={'entityId': entity_id})
        entity = entity_response.get('Item')
        
        if not entity:
            return respond(404, {'error': 'not_found', 'message': f'Entity not found: {entity_id}'})
        
        if entity.get('projectId') != project_id:
            return respond(403, {'error': 'forbidden', 'message': 'Entity belongs to different project'})
        
        if entity.get('entityType') != entity_type:
            return respond(400, {'error': 'bad_request', 'message': f'Entity type mismatch: expected {entity_type}, got {entity.get("entityType")}'})
        
        related_commits = []
        related_prs = []
        related_files = []
        related_decisions = []
        relationships = []
        
        if include_graph:
            # Get all relationships for this entity
            # Outgoing
            kwargs = {
                'IndexName': 'SourceEntityIndex',
                'KeyConditionExpression': 'sourceEntityId = :seid',
                'ExpressionAttributeValues': {':seid': entity_id},
                'ScanIndexForward': False,
                'Limit': 100,
            }
            response = relationships_table.query(**kwargs)
            outgoing = response.get('Items', [])
            
            # Incoming
            kwargs = {
                'IndexName': 'TargetEntityIndex',
                'KeyConditionExpression': 'targetEntityId = :teid',
                'ExpressionAttributeValues': {':teid': entity_id},
                'ScanIndexForward': False,
                'Limit': 100,
            }
            response = relationships_table.query(**kwargs)
            incoming = response.get('Items', [])
            
            all_relationships = outgoing + incoming
            relationships = strip_embeddings(all_relationships)
            
            # Collect related entity IDs
            related_entity_ids = set()
            for rel in all_relationships:
                if rel.get('sourceEntityId') == entity_id:
                    related_entity_ids.add(rel.get('targetEntityId'))
                else:
                    related_entity_ids.add(rel.get('sourceEntityId'))
            
            # Fetch related entities
            if related_entity_ids:
                batch_ids = list(related_entity_ids)[:100]
                batch_response = dynamodb.batch_get_item(
                    RequestItems={
                        GRAPH_ENTITIES_TABLE: {
                            'Keys': [{'entityId': eid} for eid in batch_ids]
                        }
                    }
                )
                related_entities = batch_response.get('Responses', {}).get(GRAPH_ENTITIES_TABLE, [])
                
                for rel_entity in related_entities:
                    if rel_entity.get('projectId') != project_id:
                        continue
                    
                    e_type = rel_entity.get('entityType')
                    if e_type == 'commit':
                        related_commits.append(strip_embeddings(rel_entity))
                    elif e_type == 'pull_request':
                        related_prs.append(strip_embeddings(rel_entity))
                    elif e_type == 'file':
                        related_files.append(strip_embeddings(rel_entity))
                    elif e_type == 'engineering_decision':
                        related_decisions.append(strip_embeddings(rel_entity))
        
        return respond(200, {
            'relatedCommits': related_commits,
            'relatedPRs': related_prs,
            'relatedFiles': related_files,
            'relatedDecisions': related_decisions,
            'relationships': relationships,
            'counts': {
                'commits': len(related_commits),
                'pullRequests': len(related_prs),
                'files': len(related_files),
                'decisions': len(related_decisions),
                'relationships': len(relationships),
            }
        })
    
    except Exception as e:
        print(f"Error in get_related_changes: {str(e)}")
        publish_metric('MCPToolFailure', 1, project_id)
        return respond(500, {'error': 'query_failed', 'message': str(e)})


def get_engineering_decisions(params):
    """
    Tool 7: Get explicit engineering decisions and related rationale.
    
    Params:
      - projectId (required)
      - repositoryId (optional)
      - entityId (optional): filter decisions related to this entity
      - status (optional): filter by status (e.g., "accepted", "proposed", "superseded")
      - limit (optional, default: 20, max: 50)
    
    Returns: {decisions: [...], count: N}
    """
    project_id = params.get('projectId')
    repository_id = params.get('repositoryId')
    entity_id = params.get('entityId')
    status = params.get('status')
    limit = min(int(params.get('limit', 20)), 50)
    
    if not project_id:
        return respond(400, {'error': 'bad_request', 'message': 'Missing projectId'})
    
    entities_table = get_graph_entities_table()
    relationships_table = get_graph_relationships_table()
    
    try:
        decisions = []
        
        if entity_id:
            # Find decisions related to this entity
            kwargs = {
                'IndexName': 'TargetEntityIndex',
                'KeyConditionExpression': 'targetEntityId = :teid',
                'ExpressionAttributeValues': {':teid': entity_id},
                'ScanIndexForward': False,
                'Limit': limit,
            }
            response = relationships_table.query(**kwargs)
            relationships = response.get('Items', [])
            
            decision_ids = []
            for rel in relationships:
                if rel.get('relationshipType') == 'relates_to':
                    decision_ids.append(rel.get('sourceEntityId'))
            
            if decision_ids:
                batch_response = dynamodb.batch_get_item(
                    RequestItems={
                        GRAPH_ENTITIES_TABLE: {
                            'Keys': [{'entityId': did} for did in decision_ids[:100]]
                        }
                    }
                )
                decisions = batch_response.get('Responses', {}).get(GRAPH_ENTITIES_TABLE, [])
                decisions = [d for d in decisions if d.get('projectId') == project_id and d.get('entityType') == 'engineering_decision']
        else:
            # Query all engineering decisions for project
            kwargs = {
                'IndexName': 'ProjectEntityIndex',
                'KeyConditionExpression': 'projectId = :pk',
                'ExpressionAttributeValues': {':pk': project_id},
                'FilterExpression': 'entityType = :et',
                'ExpressionAttributeValues': {
                    ':pk': project_id,
                    ':et': 'engineering_decision'
                },
                'ScanIndexForward': False,
                'Limit': limit,
            }
            
            if status:
                kwargs['FilterExpression'] = 'entityType = :et AND #st = :status'
                kwargs['ExpressionAttributeNames'] = {'#st': 'status'}
                kwargs['ExpressionAttributeValues'][':status'] = status
            
            if repository_id:
                kwargs['FilterExpression'] = 'entityType = :et AND repositoryId = :rid'
                kwargs['ExpressionAttributeValues'][':rid'] = repository_id
            
            response = entities_table.query(**kwargs)
            decisions = response.get('Items', [])
        
        # Filter by status if provided
        if status:
            decisions = [d for d in decisions if d.get('status') == status]
        
        decisions = strip_embeddings(decisions)
        
        return respond(200, {
            'decisions': decisions,
            'count': len(decisions),
        })
    
    except Exception as e:
        print(f"Error in get_engineering_decisions: {str(e)}")
        publish_metric('MCPToolFailure', 1, project_id)
        return respond(500, {'error': 'query_failed', 'message': str(e)})


def get_repository_graph_summary(params):
    """
    Tool 8: Get a summary of a repository's knowledge graph.
    
    Params:
      - projectId (required)
      - repositoryId (optional): if not provided, summarizes all repositories in project
    
    Returns: {summary: {...}, entityCounts: {...}, relationshipCounts: {...}, recentActivity: [...]}
    """
    project_id = params.get('projectId')
    repository_id = params.get('repositoryId')
    
    if not project_id:
        return respond(400, {'error': 'bad_request', 'message': 'Missing projectId'})
    
    entities_table = get_graph_entities_table()
    relationships_table = get_graph_relationships_table()
    
    try:
        entity_counts = {}
        relationship_counts = {}
        
        entity_types = ['repository', 'commit', 'pull_request', 'file', 'engineering_decision']
        relationship_types = [
            'contains', 'authored_in', 'modifies', 'has_parent',
            'includes_commit', 'targets_file', 'has_decision', 'relates_to', 'derived_from'
        ]
        
        for etype in entity_types:
            kwargs = {
                'IndexName': 'ProjectEntityIndex',
                'KeyConditionExpression': 'projectId = :pk',
                'ExpressionAttributeValues': {':pk': project_id},
                'FilterExpression': 'entityType = :et',
                'ExpressionAttributeValues': {':pk': project_id, ':et': etype},
                'Select': 'COUNT',
            }
            if repository_id:
                kwargs['FilterExpression'] = 'entityType = :et AND repositoryId = :rid'
                kwargs['ExpressionAttributeValues'][':rid'] = repository_id
            
            response = entities_table.query(**kwargs)
            entity_counts[etype] = response.get('Count', 0)
        
        for rtype in relationship_types:
            kwargs = {
                'IndexName': 'ProjectRelationshipIndex',
                'KeyConditionExpression': 'projectId = :pk',
                'ExpressionAttributeValues': {':pk': project_id},
                'FilterExpression': 'relationshipType = :rt',
                'ExpressionAttributeValues': {':pk': project_id, ':rt': rtype},
                'Select': 'COUNT',
            }
            if repository_id:
                kwargs['FilterExpression'] = 'relationshipType = :rt AND repositoryId = :rid'
                kwargs['ExpressionAttributeValues'][':rid'] = repository_id
            
            response = relationships_table.query(**kwargs)
            relationship_counts[rtype] = response.get('Count', 0)
        
        # Get recent activity (last 10 entities)
        kwargs = {
            'IndexName': 'ProjectEntityIndex',
            'KeyConditionExpression': 'projectId = :pk',
            'ExpressionAttributeValues': {':pk': project_id},
            'ScanIndexForward': False,
            'Limit': 10,
        }
        if repository_id:
            kwargs['FilterExpression'] = 'repositoryId = :rid'
            kwargs['ExpressionAttributeValues'][':rid'] = repository_id
        
        response = entities_table.query(**kwargs)
        recent_activity = strip_embeddings(response.get('Items', []))
        
        total_entities = sum(entity_counts.values())
        total_relationships = sum(relationship_counts.values())
        
        return respond(200, {
            'summary': {
                'projectId': project_id,
                'repositoryId': repository_id,
                'totalEntities': total_entities,
                'totalRelationships': total_relationships,
                'graphDensity': round(total_relationships / max(total_entities, 1), 2) if total_entities > 0 else 0,
            },
            'entityCounts': entity_counts,
            'relationshipCounts': relationship_counts,
            'recentActivity': recent_activity,
        })
    
    except Exception as e:
        print(f"Error in get_repository_graph_summary: {str(e)}")
        publish_metric('MCPToolFailure', 1, project_id)
        return respond(500, {'error': 'query_failed', 'message': str(e)})


def find_related_context(params):
    """
    Tool 9: Find context connected to an entity and optionally combine with semantic search.
    
    Params:
      - projectId (required)
      - entityType (required): commit, pull_request, file, engineering_decision
      - entityId (required)
      - query (optional): natural language query to combine with graph context
      - branch (optional)
      - maxDepth (optional, default: 2)
      - maxResults (optional, default: 20)
    
    Returns: {graphContext: [...], semanticAnswer: {...}, combinedSources: [...]}
    """
    project_id = params.get('projectId')
    entity_type = params.get('entityType')
    entity_id = params.get('entityId')
    query = params.get('query')
    branch = params.get('branch')
    max_depth = min(int(params.get('maxDepth', 2)), 3)
    max_results = min(int(params.get('maxResults', 20)), 50)
    
    if not project_id or not entity_type or not entity_id:
        return respond(400, {'error': 'bad_request', 'message': 'Missing required params: projectId, entityType, entityId'})
    
    entities_table = get_graph_entities_table()
    relationships_table = get_graph_relationships_table()
    
    try:
        # Verify entity
        entity_response = entities_table.get_item(Key={'entityId': entity_id})
        entity = entity_response.get('Item')
        
        if not entity or entity.get('projectId') != project_id:
            return respond(404, {'error': 'not_found', 'message': f'Entity not found: {entity_id}'})
        
        # Get graph context using the same traversal logic
        visited = set()
        discovered_entities = {}
        discovered_relationships = []
        paths = []
        
        def traverse(current_id, depth, path_prefix):
            if depth > max_depth or len(discovered_entities) >= max_results:
                return
            if current_id in visited:
                return
            visited.add(current_id)
            
            ent = entities_table.get_item(Key={'entityId': current_id}).get('Item')
            if ent and ent.get('projectId') == project_id:
                discovered_entities[current_id] = ent
                paths.append({
                    'entityId': current_id,
                    'entityType': ent.get('entityType'),
                    'path': path_prefix,
                    'depth': depth,
                })
                
                if depth < max_depth:
                    # Outgoing
                    kwargs = {
                        'IndexName': 'SourceEntityIndex',
                        'KeyConditionExpression': 'sourceEntityId = :seid',
                        'ExpressionAttributeValues': {':seid': current_id},
                        'ScanIndexForward': False,
                        'Limit': max_results,
                    }
                    response = relationships_table.query(**kwargs)
                    for rel in response.get('Items', []):
                        discovered_relationships.append(rel)
                        target_id = rel.get('targetEntityId')
                        if target_id:
                            traverse(target_id, depth + 1, path_prefix + [rel.get('relationshipId')])
                    
                    # Incoming
                    kwargs = {
                        'IndexName': 'TargetEntityIndex',
                        'KeyConditionExpression': 'targetEntityId = :teid',
                        'ExpressionAttributeValues': {':teid': current_id},
                        'ScanIndexForward': False,
                        'Limit': max_results,
                    }
                    response = relationships_table.query(**kwargs)
                    for rel in response.get('Items', []):
                        discovered_relationships.append(rel)
                        source_id = rel.get('sourceEntityId')
                        if source_id:
                            traverse(source_id, depth + 1, path_prefix + [rel.get('relationshipId')])
        
        traverse(entity_id, 0, [])
        
        # Convert to context format
        graph_context = []
        for ent in discovered_entities.values():
            graph_context.append({
                'entityId': ent.get('entityId'),
                'entityType': ent.get('entityType'),
                'title': ent.get('title') or ent.get('feature') or f"{ent.get('entityType')}: {ent.get('entityId', '')[:12]}",
                'summary': ent.get('summary') or ent.get('decision') or ent.get('message') or '',
                'provenance': 'graph',
                'sourceType': ent.get('entityType'),
                'sourceUrl': ent.get('url') or ent.get('sourceUrl'),
                'commitHash': ent.get('sha') or ent.get('commitHash'),
                'extractedAt': ent.get('createdAt') or ent.get('committedAt') or ent.get('extractedAt'),
                'metadata': {k: v for k, v in ent.items() if k not in ['entityId', 'entityType', 'projectId', 'repositoryId', 'schemaVersion', 'createdAt', 'updatedAt']},
            })
        
        graph_context = strip_embeddings(graph_context)
        discovered_relationships = strip_embeddings(discovered_relationships)
        
        result = {
            'graphContext': graph_context,
            'relationships': discovered_relationships,
            'paths': paths,
            'counts': {
                'entities': len(graph_context),
                'relationships': len(discovered_relationships),
            }
        }
        
        # If query provided, also run semantic search and combine
        if query:
            try:
                rag_result = search_context_rag_with_graph(
                    project_id=project_id,
                    query=query,
                    branch=branch,
                    bedrock_client=bedrock_client,
                    dynamodb=dynamodb,
                    context_table_name=CONTEXT_TABLE,
                    cache_table_name=CACHE_TABLE or None,
                    use_graph=True
                )
                result['semanticAnswer'] = {
                    'answer': rag_result.get('answer'),
                    'answerGrounded': rag_result.get('answerGrounded'),
                }
                result['combinedSources'] = rag_result.get('sources', [])
            except Exception as e:
                print(f"[find_related_context] Semantic search failed: {e}")
                result['semanticAnswer'] = {'error': str(e)}
        
        return respond(200, result)
    
    except Exception as e:
        print(f"Error in find_related_context: {str(e)}")
        publish_metric('MCPToolFailure', 1, project_id)
        return respond(500, {'error': 'query_failed', 'message': str(e)})


def handler(event, context):
    """Main handler - routes MCP tool calls."""
    print('MCP Lambda invoked', json.dumps(event))
    
    try:
        # Parse request body (API Gateway sends as string)
        body = json.loads(event.get('body', '{}'))
        tool_name = body.get('tool')
        params = body.get('params', {})
        
        if not tool_name:
            return respond(400, {'error': 'bad_request', 'message': 'Missing tool name'})
        
        # Authenticate request (except for tools that don't require project access)
        project_id = params.get('projectId')
        if project_id:
            auth_result = authenticate(event, project_id, dynamodb, PROJECTS_TABLE)
            if not auth_result['success']:
                return respond(auth_result['statusCode'], auth_result['error'])
        
        # Route to appropriate tool
        if tool_name == 'get_project_context':
            return get_project_context(params)
        elif tool_name == 'get_recent_changes':
            return get_recent_changes(params)
        elif tool_name == 'search_context':
            return search_context(params)
        elif tool_name == 'log_context':
            return log_context(params)
        # Phase 3 graph tools
        elif tool_name == 'query_knowledge_graph':
            return query_knowledge_graph(params)
        elif tool_name == 'get_related_changes':
            return get_related_changes(params)
        elif tool_name == 'get_engineering_decisions':
            return get_engineering_decisions(params)
        elif tool_name == 'get_repository_graph_summary':
            return get_repository_graph_summary(params)
        elif tool_name == 'find_related_context':
            return find_related_context(params)
        else:
            return respond(400, {'error': 'invalid_tool', 'message': f'Unknown tool: {tool_name}'})
    
    except json.JSONDecodeError as e:
        print(f"Invalid JSON in request body: {str(e)}")
        return respond(400, {'error': 'bad_request', 'message': 'Invalid JSON'})
    except Exception as e:
        print(f"Unhandled error in MCP handler: {str(e)}")
        return respond(500, {'error': 'internal_error', 'message': str(e)})
