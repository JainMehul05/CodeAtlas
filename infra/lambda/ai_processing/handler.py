import json
import boto3
import os
import uuid
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from botocore.exceptions import ClientError
from botocore.config import Config as BotoConfig

# Model and embedding configuration
MODEL_ID = "us.amazon.nova-pro-v1:0"
EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v1"
FALLBACK_MODEL_ID = os.environ.get("FALLBACK_MODEL_ID", "us.amazon.nova-lite-v1:0")

# DynamoDB table names
CONTEXT_TABLE = os.environ.get("CONTEXT_TABLE", "flowsync-context")
AUDIT_TABLE = os.environ.get("AUDIT_TABLE", "flowsync-audit")
PROJECTS_TABLE = os.environ.get("PROJECTS_TABLE", "flowsync-projects")
IDEMPOTENCY_TABLE = os.environ.get("IDEMPOTENCY_TABLE", "flowsync-idempotency")
GRAPH_ENTITIES_TABLE = os.environ.get("GRAPH_ENTITIES_TABLE", "flowsync-graph-entities")
GRAPH_RELATIONSHIPS_TABLE = os.environ.get("GRAPH_RELATIONSHIPS_TABLE", "flowsync-graph-relationships")

# Bedrock client with adaptive retry
_bedrock_retry_config = BotoConfig(retries={'max_attempts': 3, 'mode': 'adaptive'})
bedrock_client = boto3.client("bedrock-runtime", config=_bedrock_retry_config)
dynamodb = boto3.resource("dynamodb")
cloudwatch = boto3.client("cloudwatch")

# Idempotency TTL (7 days in seconds)
IDEMPOTENCY_TTL_SECONDS = 7 * 24 * 60 * 60

# Graph schema version
GRAPH_SCHEMA_VERSION = "1"

def call_bedrock(event_data):
    """Call Nova Pro via Bedrock Converse API with commit metadata and return extracted context as JSON."""
    diff         = event_data.get('diff', '')
    commit_hash  = event_data.get('commitHash', '')
    message      = event_data.get('message', '')
    author       = event_data.get('author', '')
    branch       = event_data.get('branch', 'main')
    changed_files = event_data.get('changedFiles', [])

    system_prompt = (
        "You are a deterministic software project intelligence extractor. "
        "Return STRICT JSON only. No explanation, no markdown, no free text outside the JSON object."
    )

    user_prompt = f"""Analyze this Git push and extract structured project intelligence.

Commit Hash: {commit_hash}
Commit Message: {message}
Author: {author}
Branch: {branch}
Changed Files: {', '.join(changed_files) if changed_files else 'not provided'}

Diff:
{diff}

Return ONLY a valid JSON object with this exact structure:
{{
  "feature": "name of the feature or module being modified (e.g. 'Auth pipeline', 'Event ingestion', 'Dashboard UI')",
  "decision": "<see rules below>",
  "tasks": ["specific remaining task inferred from TODOs, partial implementations, or stub functions — empty array if none visible"],
  "stage": "one of: Setup | Feature Development | Refactoring | Bug Fix | Testing | Documentation",
  "risk": "a concrete risk visible in the diff (e.g. 'No error handling on DB write', 'Token logged in plaintext', 'No input validation') — null if none",
  "entities": ["every function name, class name, or filename directly modified in this diff"]
}}

Rules for 'decision' field:
- SET to a concise string when the diff shows ANY of these patterns:
    * One technology/library/approach REPLACED by another (e.g. removed bcrypt import, added scrypt call)
    * An explicit design choice in data structures (e.g. storing token as salt:hash, using GSI for query)
    * A new architectural pattern introduced (e.g. fire-and-forget async invoke, singleton SDK client)
    * A change in API style or protocol (e.g. switched from invoke_model to converse API)
    * A security or performance tradeoff made explicit in the code or commit message
    * Added/removed a dependency that implies a deliberate choice
- SET to null ONLY when the diff is purely additive content with no technology or approach choice visible
- Be specific: write WHAT was chosen and WHY if evident (e.g. 'Used Bedrock Converse API over invoke_model for model-agnostic interface')

Extract only factual information present in the diff and message. Do not invent or assume."""

    t0 = time.time()
    model_used = MODEL_ID
    try:
        response = bedrock_client.converse(
            modelId=MODEL_ID,
            system=[{"text": system_prompt}],
            messages=[{"role": "user", "content": [{"text": user_prompt}]}],
            inferenceConfig={"maxTokens": 2000, "temperature": 0, "topP": 1}
        )
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code in ('ThrottlingException', 'ModelTimeoutException', 'ServiceUnavailableException'):
            print(f"Nova Pro throttled ({error_code}), falling back to {FALLBACK_MODEL_ID}")
            model_used = FALLBACK_MODEL_ID
            response = bedrock_client.converse(
                modelId=FALLBACK_MODEL_ID,
                system=[{"text": system_prompt}],
                messages=[{"role": "user", "content": [{"text": user_prompt}]}],
                inferenceConfig={"maxTokens": 2000, "temperature": 0, "topP": 1}
            )
        else:
            raise
    bedrock_duration_ms = int((time.time() - t0) * 1000)

    usage = response.get('usage', {})
    print(f"BEDROCK_TIMING input_tokens={usage.get('inputTokens', 0)} output_tokens={usage.get('outputTokens', 0)} duration_ms={bedrock_duration_ms}")
    print(f"Bedrock response metadata: {json.dumps(usage, default=str)}")

    try:
        output_text = response['output']['message']['content'][0]['text'].strip()
    except (KeyError, IndexError, TypeError) as e:
        raise ValueError(f"Unexpected Bedrock Converse response structure: {e}. Response: {response}")

    if output_text.startswith('```json'):
        output_text = output_text.split('```json')[1].split('```')[0].strip()
    elif output_text.startswith('```'):
        output_text = output_text.split('```')[1].split('```')[0].strip()

    result = json.loads(output_text)
    result['_bedrock_duration_ms'] = bedrock_duration_ms
    return result

def validate_extraction_schema(data):
    required_fields = [
        "feature", "decision", "tasks", "stage", "risk", "entities"
    ]
    for field in required_fields:
        if field not in data:
            raise ValueError(f"Missing required field: {field}")
    return True

def compute_confidence(extraction):
    score = 0.55
    if extraction.get('decision'):
        score += 0.15
    if extraction.get('risk'):
        score += 0.15
    if extraction.get('tasks'):
        score += 0.10
    if len(extraction.get('entities', [])) >= 2:
        score += 0.05
    return round(min(score, 1.0), 2)

def convert_floats_to_decimal(obj):
    if isinstance(obj, list):
        return [convert_floats_to_decimal(item) for item in obj]
    elif isinstance(obj, dict):
        return {key: convert_floats_to_decimal(value) for key, value in obj.items()}
    elif isinstance(obj, float):
        return Decimal(str(obj))
    else:
        return obj

def call_titan_embedding(text):
    t0 = time.time()
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
    embedding_duration_ms = int((time.time() - t0) * 1000)
    print(f"EMBEDDING_TIMING duration_ms={embedding_duration_ms} dims={len(embedding)}")
    return embedding, embedding_duration_ms

def write_context_record(context_record):
    table = dynamodb.Table(CONTEXT_TABLE)
    context_record = convert_floats_to_decimal(context_record)
    table.put_item(Item=context_record)

def write_audit_record(audit_record):
    table = dynamodb.Table(AUDIT_TABLE)
    table.put_item(Item=audit_record)

def update_project_activity(project_id, timestamp):
    table = dynamodb.Table(PROJECTS_TABLE)
    table.update_item(
        Key={"projectId": project_id},
        UpdateExpression="SET lastActivityAt = :ts ADD eventCount :inc",
        ExpressionAttributeValues={":ts": timestamp, ":inc": 1}
    )

def find_orphaned_record(project_id, branch, author, timestamp):
    table = dynamodb.Table(CONTEXT_TABLE)
    
    time_obj = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    window_start = (time_obj - timedelta(minutes=30)).isoformat().replace('+00:00', 'Z')
    
    try:
        response = table.query(
            IndexName='BranchContextIndex',
            KeyConditionExpression='projectId = :pk AND branchExtractedAt BETWEEN :start AND :end',
            FilterExpression='commitHash = :null AND author = :author',
            ExpressionAttributeValues={
                ':pk': project_id,
                ':start': f"{branch}#{window_start}",
                ':end': f"{branch}#{timestamp}",
                ':null': None,
                ':author': author
            },
            Limit=1,
            ScanIndexForward=False
        )
        
        if response.get('Items'):
            return response['Items'][0]
        return None
    except ClientError as e:
        print(f"Error finding orphaned record: {str(e)}")
        return None

def update_orphaned_record(event_id, commit_hash, timestamp):
    table = dynamodb.Table(CONTEXT_TABLE)
    table.update_item(
        Key={"eventId": event_id},
        UpdateExpression="SET commitHash = :hash, #status = :status, committedAt = :ts",
        ExpressionAttributeNames={'#status': 'status'},
        ExpressionAttributeValues={
            ':hash': commit_hash,
            ':status': 'complete',
            ':ts': timestamp
        }
    )
    print(f"Updated orphaned record {event_id} with commitHash {commit_hash}")

def publish_cloudwatch_metric(metric_name, value, project_id):
    try:
        cloudwatch.put_metric_data(
            Namespace='FlowSync',
            MetricData=[
                {
                    'MetricName': metric_name,
                    'Value': value,
                    'Unit': 'Count',
                    'Timestamp': datetime.utcnow(),
                    'Dimensions': [
                        {
                            'Name': 'ProjectId',
                            'Value': project_id
                        }
                    ]
                }
            ]
        )
        print(f"Published CloudWatch metric: {metric_name} = {value}")
    except Exception as e:
        print(f"Failed to publish CloudWatch metric: {str(e)}")

def propagate_branch_context(project_id, source_branch, target_branch, timestamp):
    table = dynamodb.Table(CONTEXT_TABLE)

    all_records = []
    kwargs = {
        'IndexName': 'BranchContextIndex',
        'KeyConditionExpression': 'projectId = :pk AND begins_with(branchExtractedAt, :prefix)',
        'ExpressionAttributeValues': {
            ':pk': project_id,
            ':prefix': f'{source_branch}#'
        }
    }
    while True:
        response = table.query(**kwargs)
        all_records.extend(r for r in response.get('Items', []) if r.get('status') != 'failed')
        last_key = response.get('LastEvaluatedKey')
        if not last_key:
            break
        kwargs['ExclusiveStartKey'] = last_key

    if not all_records:
        print(f"[propagate] No records for branch '{source_branch}' — nothing to propagate")
        return 0

    for record in all_records:
        new_record = dict(record)
        new_record['eventId']           = str(uuid.uuid4())
        new_record['branch']            = target_branch
        new_record['branchExtractedAt'] = f"{target_branch}#{timestamp}"
        new_record['mergedFrom']        = source_branch
        new_record['extractedAt']       = timestamp
        table.put_item(Item=new_record)

    print(f"[propagate] Copied {len(all_records)} records: '{source_branch}' → '{target_branch}'")
    return len(all_records)


# ─────────────────────────────────────────────────────────────────────────────
# IDEMPOTENCY HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def check_idempotency(idempotency_key: str) -> tuple[bool, dict | None]:
    """
    Check if an event has already been processed.
    Returns (is_duplicate, existing_record).
    Handles stale PROCESSING records by allowing reprocessing if older than lease threshold.
    """
    table = dynamodb.Table(IDEMPOTENCY_TABLE)
    try:
        response = table.get_item(Key={'idempotencyKey': idempotency_key})
        item = response.get('Item')
        if item:
            status = item.get('status')
            # Handle stale PROCESSING records: if PROCESSING for > 10 minutes, allow reprocessing
            if status == 'PROCESSING':
                started_at_str = item.get('startedAt')
                if started_at_str:
                    try:
                        started_at = datetime.fromisoformat(started_at_str.replace('Z', '+00:00'))
                        now = datetime.now(timezone.utc)
                        if (now - started_at).total_seconds() > 600:  # 10 minute lease
                            print(f"[idempotency] Stale PROCESSING record detected for key: {idempotency_key}, allowing reprocessing")
                            return False, None
                    except Exception:
                        pass  # If parsing fails, treat as normal duplicate
                print(f"[idempotency] Duplicate detected for key: {idempotency_key}")
                return True, item
            # For COMPLETED, FAILED, or any other final status, treat as duplicate
            print(f"[idempotency] Duplicate detected for key: {idempotency_key} (status: {status})")
            return True, item
        return False, None
    except ClientError as e:
        print(f"[idempotency] Error checking idempotency: {str(e)}")
        return False, None

def claim_idempotency(idempotency_key: str, event_data: dict) -> bool:
    """
    Atomically claim an idempotency key for processing.
    Returns True if claim succeeded, False if already claimed.
    """
    table = dynamodb.Table(IDEMPOTENCY_TABLE)
    timestamp = datetime.utcnow().isoformat() + 'Z'
    expires_at = int(time.time()) + IDEMPOTENCY_TTL_SECONDS
    
    try:
        table.put_item(
            Item={
                'idempotencyKey': idempotency_key,
                'status': 'PROCESSING',
                'eventData': event_data,
                'startedAt': timestamp,
                'expiresAt': expires_at,
            },
            ConditionExpression='attribute_not_exists(idempotencyKey)'
        )
        return True
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            print(f"[idempotency] Key already claimed: {idempotency_key}")
            return False
        print(f"[idempotency] Error claiming idempotency: {str(e)}")
        return False

def complete_idempotency(idempotency_key: str, result: dict):
    """Mark idempotency record as completed with result."""
    table = dynamodb.Table(IDEMPOTENCY_TABLE)
    try:
        table.update_item(
            Key={'idempotencyKey': idempotency_key},
            UpdateExpression='SET #status = :status, completedAt = :ts, result = :result',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': 'COMPLETED',
                ':ts': datetime.utcnow().isoformat() + 'Z',
                ':result': result,
            }
        )
    except ClientError as e:
        print(f"[idempotency] Error completing idempotency: {str(e)}")

def fail_idempotency(idempotency_key: str, error: str):
    """Mark idempotency record as failed."""
    table = dynamodb.Table(IDEMPOTENCY_TABLE)
    try:
        table.update_item(
            Key={'idempotencyKey': idempotency_key},
            UpdateExpression='SET #status = :status, failedAt = :ts, error = :error',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': 'FAILED',
                ':ts': datetime.utcnow().isoformat() + 'Z',
                ':error': error,
            }
        )
    except ClientError as e:
        print(f"[idempotency] Error failing idempotency: {str(e)}")


# ─────────────────────────────────────────────────────────────────────────────
# GRAPH INGESTION HELPERS (Phase 3)
# ─────────────────────────────────────────────────────────────────────────────

def generate_repository_id(provider: str, owner: str, name: str) -> str:
    return f"repo:{provider}:{owner}:{name}".lower()

def generate_commit_id(repository_id: str, sha: str) -> str:
    return f"commit:{repository_id}:{sha.lower()}"

def generate_pr_id(repository_id: str, number: int) -> str:
    return f"pr:{repository_id}:{number}"

def generate_file_id(repository_id: str, path: str) -> str:
    # Normalize path
    normalized = path.replace('\\', '/').strip().lstrip('./').lstrip('/')
    # Resolve . and .. segments
    segments = normalized.split('/')
    resolved = []
    for seg in segments:
        if seg == '' or seg == '.':
            continue
        if seg == '..':
            if resolved:
                resolved.pop()
        else:
            resolved.append(seg)
    normalized = '/'.join(resolved)
    return f"file:{repository_id}:{normalized}"

def generate_decision_id(project_id: str, source_type: str, source_id: str) -> str:
    return f"decision:{project_id}:{source_type}:{source_id}"

def generate_relationship_id(rel_type: str, source_id: str, target_id: str) -> str:
    import hashlib
    hash_input = f"{rel_type}:{source_id}:{target_id}"
    return f"rel:{hashlib.sha256(hash_input.encode()).hexdigest()[:16]}"

def get_graph_entities_table():
    return dynamodb.Table(GRAPH_ENTITIES_TABLE)

def get_graph_relationships_table():
    return dynamodb.Table(GRAPH_RELATIONSHIPS_TABLE)

def upsert_graph_entity(entity: dict):
    """Upsert a graph entity with idempotent write."""
    table = get_graph_entities_table()
    now = datetime.utcnow().isoformat() + 'Z'
    entity = dict(entity)
    entity['schemaVersion'] = entity.get('schemaVersion', GRAPH_SCHEMA_VERSION)
    entity['updatedAt'] = now
    if 'createdAt' not in entity:
        entity['createdAt'] = now
    
    # Convert floats to Decimal
    def convert_floats(obj):
        if isinstance(obj, list):
            return [convert_floats(item) for item in obj]
        elif isinstance(obj, dict):
            return {k: convert_floats(v) for k, v in obj.items()}
        elif isinstance(obj, float):
            return Decimal(str(obj))
        return obj
    
    entity = convert_floats(entity)
    
    try:
        table.put_item(Item=entity)
    except ClientError as e:
        print(f"[graph] Error upserting entity {entity.get('entityId')}: {e}")
        raise

def upsert_graph_relationship(relationship: dict):
    """Upsert a graph relationship with conditional write for idempotency."""
    table = get_graph_relationships_table()
    now = datetime.utcnow().isoformat() + 'Z'
    relationship = dict(relationship)
    relationship['schemaVersion'] = relationship.get('schemaVersion', GRAPH_SCHEMA_VERSION)
    relationship['updatedAt'] = now
    if 'createdAt' not in relationship:
        relationship['createdAt'] = now
    
    def convert_floats(obj):
        if isinstance(obj, list):
            return [convert_floats(item) for item in obj]
        elif isinstance(obj, dict):
            return {k: convert_floats(v) for k, v in obj.items()}
        elif isinstance(obj, float):
            return Decimal(str(obj))
        return obj
    
    relationship = convert_floats(relationship)
    
    try:
        table.put_item(
            Item=relationship,
            ConditionExpression='attribute_not_exists(relationshipId) OR updatedAt <= :newUpdatedAt',
            ExpressionAttributeValues={':newUpdatedAt': now}
        )
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            print(f"[graph] Relationship {relationship.get('relationshipId')} already exists with newer data, skipping")
            return
        print(f"[graph] Error upserting relationship {relationship.get('relationshipId')}: {e}")
        raise

def ingest_graph_from_event(body: dict, correlation_id: str):
    """
    Ingest graph entities and relationships from a processed event.
    This is called after successful event processing to build the knowledge graph.
    """
    event_id = body.get('eventId')
    project_id = body.get('projectId')
    event_type = body.get('eventType')
    branch = body.get('branch')
    payload = body.get('payload', {})
    timestamp = body.get('timestamp', datetime.utcnow().isoformat() + 'Z')
    delivery_id = body.get('deliveryId')
    
    if not project_id or not event_id:
        log_warn(correlation_id, "Skipping graph ingestion: missing projectId or eventId")
        return
    
    log_info(correlation_id, "Starting graph ingestion", eventId=event_id, eventType=event_type)
    
    # Extract repository info from project or payload
    # For GitHub events, we can derive repository from delivery context
    repository_id = None
    provider = "github"
    owner = "unknown"
    repo_name = "unknown"
    
    # Try to get repository from payload or context
    if event_type in ('push', 'merge', 'workflow_run', 'check_run', 'check_suite') and payload.get('repository'):
        repo_info = payload.get('repository', {})
        owner = repo_info.get('owner', 'unknown')
        repo_name = repo_info.get('name', 'unknown')
        repository_id = generate_repository_id(provider, owner, repo_name)
    elif delivery_id and delivery_id.startswith('github-'):
        # For GitHub webhooks, we may not have full repo info in the event
        # Use a placeholder that can be enriched later
        repository_id = generate_repository_id(provider, "unknown", "unknown")
    else:
        # For VS Code events, we may need to derive from project
        # For now, create a project-scoped repository ID
        repository_id = f"repo:project:{project_id}"
    
    entities_created = []
    relationships_created = []
    
    try:
        if event_type == 'push':
            # Create/update commit entity
            commit_hash = payload.get('commitHash')
            if commit_hash:
                commit_id = generate_commit_id(repository_id, commit_hash)
                commit_entity = {
                    'entityId': commit_id,
                    'entityType': 'commit',
                    'projectId': project_id,
                    'repositoryId': repository_id,
                    'sha': commit_hash.lower(),
                    'author': payload.get('author', 'unknown'),
                    'committer': payload.get('author', 'unknown'),
                    'message': payload.get('message', ''),
                    'committedAt': timestamp,
                    'parentShas': [],  # Would need to fetch from GitHub API
                    'source': payload.get('source', 'vscode'),
                    'createdAt': timestamp,
                    'updatedAt': timestamp,
                    'schemaVersion': GRAPH_SCHEMA_VERSION,
                }
                upsert_graph_entity(commit_entity)
                entities_created.append(commit_id)
                
                # Relationship: commit MODIFIES files
                changed_files = payload.get('changedFiles', [])
                for file_path in changed_files:
                    file_id = generate_file_id(repository_id, file_path)
                    file_entity = {
                        'entityId': file_id,
                        'entityType': 'file',
                        'projectId': project_id,
                        'repositoryId': repository_id,
                        'path': file_path,
                        'lastObservedAt': timestamp,
                        'createdAt': timestamp,
                        'updatedAt': timestamp,
                        'schemaVersion': GRAPH_SCHEMA_VERSION,
                    }
                    upsert_graph_entity(file_entity)
                    entities_created.append(file_id)
                    
                    # MODIFIES relationship
                    rel = {
                        'relationshipId': generate_relationship_id('modifies', commit_id, file_id),
                        'relationshipType': 'modifies',
                        'sourceEntityId': commit_id,
                        'sourceEntityType': 'commit',
                        'targetEntityId': file_id,
                        'targetEntityType': 'file',
                        'projectId': project_id,
                        'repositoryId': repository_id,
                        'provenance': 'explicit',
                        'evidence': event_id,
                        'createdAt': timestamp,
                        'updatedAt': timestamp,
                        'schemaVersion': GRAPH_SCHEMA_VERSION,
                    }
                    upsert_graph_relationship(rel)
                    relationships_created.append(rel['relationshipId'])
                
                # Relationship: commit AUTHORED_IN repository
                rel = {
                    'relationshipId': generate_relationship_id('authored_in', commit_id, repository_id),
                    'relationshipType': 'authored_in',
                    'sourceEntityId': commit_id,
                    'sourceEntityType': 'commit',
                    'targetEntityId': repository_id,
                    'targetEntityType': 'repository',
                    'projectId': project_id,
                    'repositoryId': repository_id,
                    'provenance': 'explicit',
                    'evidence': event_id,
                    'createdAt': timestamp,
                    'updatedAt': timestamp,
                    'schemaVersion': GRAPH_SCHEMA_VERSION,
                }
                upsert_graph_relationship(rel)
                relationships_created.append(rel['relationshipId'])
                
                # Create repository entity if not exists
                repo_entity = {
                    'entityId': repository_id,
                    'entityType': 'repository',
                    'projectId': project_id,
                    'repositoryId': repository_id,
                    'provider': provider,
                    'owner': owner,
                    'name': repo_name,
                    'defaultBranch': branch,
                    'createdAt': timestamp,
                    'updatedAt': timestamp,
                    'schemaVersion': GRAPH_SCHEMA_VERSION,
                }
                upsert_graph_entity(repo_entity)
                entities_created.append(repository_id)
                
                # Relationship: repository CONTAINS file
                for file_path in changed_files:
                    file_id = generate_file_id(repository_id, file_path)
                    rel = {
                        'relationshipId': generate_relationship_id('contains', repository_id, file_id),
                        'relationshipType': 'contains',
                        'sourceEntityId': repository_id,
                        'sourceEntityType': 'repository',
                        'targetEntityId': file_id,
                        'targetEntityType': 'file',
                        'projectId': project_id,
                        'repositoryId': repository_id,
                        'provenance': 'explicit',
                        'evidence': event_id,
                        'createdAt': timestamp,
                        'updatedAt': timestamp,
                        'schemaVersion': GRAPH_SCHEMA_VERSION,
                    }
                    upsert_graph_relationship(rel)
                    relationships_created.append(rel['relationshipId'])
        
        elif event_type == 'merge':
            # Create pull request entity
            pr_number = payload.get('pullRequest', {}).get('number')
            if pr_number:
                pr_id = generate_pr_id(repository_id, pr_number)
                pr_entity = {
                    'entityId': pr_id,
                    'entityType': 'pull_request',
                    'projectId': project_id,
                    'repositoryId': repository_id,
                    'number': pr_number,
                    'title': payload.get('pullRequest', {}).get('title', ''),
                    'description': payload.get('pullRequest', {}).get('body', ''),
                    'state': 'merged' if payload.get('pullRequest', {}).get('merged') else 'closed',
                    'author': payload.get('author', 'unknown'),
                    'sourceBranch': payload.get('sourceBranch', ''),
                    'targetBranch': payload.get('branch', 'main'),
                    'createdAt': timestamp,
                    'updatedAt': timestamp,
                    'mergedAt': timestamp if payload.get('pullRequest', {}).get('merged') else None,
                    'url': payload.get('pullRequest', {}).get('url'),
                    'source': 'github',
                    'schemaVersion': GRAPH_SCHEMA_VERSION,
                }
                # Remove None values
                pr_entity = {k: v for k, v in pr_entity.items() if v is not None}
                upsert_graph_entity(pr_entity)
                entities_created.append(pr_id)
                
                # Also create commit entity for merge commit
                commit_hash = payload.get('commitHash')
                if commit_hash:
                    commit_id = generate_commit_id(repository_id, commit_hash)
                    commit_entity = {
                        'entityId': commit_id,
                        'entityType': 'commit',
                        'projectId': project_id,
                        'repositoryId': repository_id,
                        'sha': commit_hash.lower(),
                        'author': payload.get('author', 'unknown'),
                        'committer': payload.get('author', 'unknown'),
                        'message': payload.get('message', ''),
                        'committedAt': timestamp,
                        'parentShas': [],  # Would need to fetch from GitHub API
                        'source': 'github',
                        'createdAt': timestamp,
                        'updatedAt': timestamp,
                        'schemaVersion': GRAPH_SCHEMA_VERSION,
                    }
                    upsert_graph_entity(commit_entity)
                    entities_created.append(commit_id)
                    
                    # Relationship: commit MODIFIES files
                    changed_files = payload.get('changedFiles', [])
                    for file_path in changed_files:
                        file_id = generate_file_id(repository_id, file_path)
                        file_entity = {
                            'entityId': file_id,
                            'entityType': 'file',
                            'projectId': project_id,
                            'repositoryId': repository_id,
                            'path': file_path,
                            'lastObservedAt': timestamp,
                            'createdAt': timestamp,
                            'updatedAt': timestamp,
                            'schemaVersion': GRAPH_SCHEMA_VERSION,
                        }
                        upsert_graph_entity(file_entity)
                        entities_created.append(file_id)
                        
                        # MODIFIES relationship
                        rel = {
                            'relationshipId': generate_relationship_id('modifies', commit_id, file_id),
                            'relationshipType': 'modifies',
                            'sourceEntityId': commit_id,
                            'sourceEntityType': 'commit',
                            'targetEntityId': file_id,
                            'targetEntityType': 'file',
                            'projectId': project_id,
                            'repositoryId': repository_id,
                            'provenance': 'explicit',
                            'evidence': event_id,
                            'createdAt': timestamp,
                            'updatedAt': timestamp,
                            'schemaVersion': GRAPH_SCHEMA_VERSION,
                        }
                        upsert_graph_relationship(rel)
                        relationships_created.append(rel['relationshipId'])
                    
                    # Relationship: commit AUTHORED_IN repository
                    rel = {
                        'relationshipId': generate_relationship_id('authored_in', commit_id, repository_id),
                        'relationshipType': 'authored_in',
                        'sourceEntityId': commit_id,
                        'sourceEntityType': 'commit',
                        'targetEntityId': repository_id,
                        'targetEntityType': 'repository',
                        'projectId': project_id,
                        'repositoryId': repository_id,
                        'provenance': 'explicit',
                        'evidence': event_id,
                        'createdAt': timestamp,
                        'updatedAt': timestamp,
                        'schemaVersion': GRAPH_SCHEMA_VERSION,
                    }
                    upsert_graph_relationship(rel)
                    relationships_created.append(rel['relationshipId'])
                    
                    # Relationship: PR INCLUDES_COMMIT (for merge commit)
                    rel = {
                        'relationshipId': generate_relationship_id('includes_commit', pr_id, commit_id),
                        'relationshipType': 'includes_commit',
                        'sourceEntityId': pr_id,
                        'sourceEntityType': 'pull_request',
                        'targetEntityId': commit_id,
                        'targetEntityType': 'commit',
                        'projectId': project_id,
                        'repositoryId': repository_id,
                        'provenance': 'explicit',
                        'evidence': event_id,
                        'createdAt': timestamp,
                        'updatedAt': timestamp,
                        'schemaVersion': GRAPH_SCHEMA_VERSION,
                    }
                    upsert_graph_relationship(rel)
                    relationships_created.append(rel['relationshipId'])
                
                # Relationship: PR TARGETS_FILE
                changed_files = payload.get('changedFiles', [])
                for file_path in changed_files:
                    file_id = generate_file_id(repository_id, file_path)
                    rel = {
                        'relationshipId': generate_relationship_id('targets_file', pr_id, file_id),
                        'relationshipType': 'targets_file',
                        'sourceEntityId': pr_id,
                        'sourceEntityType': 'pull_request',
                        'targetEntityId': file_id,
                        'targetEntityType': 'file',
                        'projectId': project_id,
                        'repositoryId': repository_id,
                        'provenance': 'explicit',
                        'evidence': event_id,
                        'createdAt': timestamp,
                        'updatedAt': timestamp,
                        'schemaVersion': GRAPH_SCHEMA_VERSION,
                    }
                    upsert_graph_relationship(rel)
                    relationships_created.append(rel['relationshipId'])
                
                # Create repository entity and CONTAINS relationships for files
                repo_entity = {
                    'entityId': repository_id,
                    'entityType': 'repository',
                    'projectId': project_id,
                    'repositoryId': repository_id,
                    'provider': provider,
                    'owner': owner,
                    'name': repo_name,
                    'defaultBranch': payload.get('branch', 'main'),
                    'createdAt': timestamp,
                    'updatedAt': timestamp,
                    'schemaVersion': GRAPH_SCHEMA_VERSION,
                }
                upsert_graph_entity(repo_entity)
                entities_created.append(repository_id)
                
                for file_path in changed_files:
                    file_id = generate_file_id(repository_id, file_path)
                    rel = {
                        'relationshipId': generate_relationship_id('contains', repository_id, file_id),
                        'relationshipType': 'contains',
                        'sourceEntityId': repository_id,
                        'sourceEntityType': 'repository',
                        'targetEntityId': file_id,
                        'targetEntityType': 'file',
                        'projectId': project_id,
                        'repositoryId': repository_id,
                        'provenance': 'explicit',
                        'evidence': event_id,
                        'createdAt': timestamp,
                        'updatedAt': timestamp,
                        'schemaVersion': GRAPH_SCHEMA_VERSION,
                    }
                    upsert_graph_relationship(rel)
                    relationships_created.append(rel['relationshipId'])
        
        # Ingest engineering decision from context extraction
        # The context extraction creates a context record with feature, decision, etc.
        # We can create an engineering_decision entity from this
        if event_type == 'push' and payload.get('commitHash'):
            # This will be linked after context extraction
            pass
        
        elif event_type == 'workflow_run':
            # Handle GitHub Actions workflow run
            workflow_run_id = payload.get('runId')
            if workflow_run_id:
                repo_info = payload.get('repository', {})
                if repo_info:
                    owner = repo_info.get('owner', 'unknown')
                    repo_name = repo_info.get('name', 'unknown')
                    repository_id = generate_repository_id(provider, owner, repo_name)
                
                workflow_run_entity_id = f"workflow:{repository_id}:{workflow_run_id}"
                workflow_run_entity = {
                    'entityId': workflow_run_entity_id,
                    'entityType': 'workflow_run',
                    'projectId': project_id,
                    'repositoryId': repository_id,
                    'workflowId': payload.get('workflowId'),
                    'workflowName': payload.get('workflowName'),
                    'runId': workflow_run_id,
                    'runNumber': payload.get('runNumber'),
                    'runAttempt': payload.get('runAttempt'),
                    'event': payload.get('event'),
                    'status': payload.get('status'),
                    'conclusion': payload.get('conclusion'),
                    'headBranch': payload.get('headBranch'),
                    'headSha': payload.get('headSha'),
                    'startedAt': payload.get('startedAt'),
                    'completedAt': payload.get('completedAt'),
                    'htmlUrl': payload.get('htmlUrl'),
                    'checkSuiteId': payload.get('checkSuiteId'),
                    'pullRequestNumbers': payload.get('pullRequests', []),
                    'createdAt': timestamp,
                    'updatedAt': timestamp,
                    'schemaVersion': GRAPH_SCHEMA_VERSION,
                }
                upsert_graph_entity(workflow_run_entity)
                entities_created.append(workflow_run_entity_id)
                
                # Relationship: workflow run TRIGGERS from commit
                head_sha = payload.get('headSha')
                if head_sha:
                    commit_id = generate_commit_id(repository_id, head_sha)
                    rel = {
                        'relationshipId': generate_relationship_id('triggers', commit_id, workflow_run_entity_id),
                        'relationshipType': 'triggers',
                        'sourceEntityId': commit_id,
                        'sourceEntityType': 'commit',
                        'targetEntityId': workflow_run_entity_id,
                        'targetEntityType': 'workflow_run',
                        'projectId': project_id,
                        'repositoryId': repository_id,
                        'provenance': 'explicit',
                        'evidence': event_id,
                        'createdAt': timestamp,
                        'updatedAt': timestamp,
                        'schemaVersion': GRAPH_SCHEMA_VERSION,
                    }
                    upsert_graph_relationship(rel)
                    relationships_created.append(rel['relationshipId'])
                
                # Relationship: workflow run HAS_CHECK for check runs
                # This will be populated when check_run events are processed
                
                # Create repository entity if not exists
                repo_entity = {
                    'entityId': repository_id,
                    'entityType': 'repository',
                    'projectId': project_id,
                    'repositoryId': repository_id,
                    'provider': provider,
                    'owner': owner,
                    'name': repo_name,
                    'defaultBranch': payload.get('headBranch', 'main'),
                    'createdAt': timestamp,
                    'updatedAt': timestamp,
                    'schemaVersion': GRAPH_SCHEMA_VERSION,
                }
                upsert_graph_entity(repo_entity)
                entities_created.append(repository_id)
        
        elif event_type == 'check_run':
            # Handle GitHub Actions check run
            check_run_id = payload.get('checkRunId')
            if check_run_id:
                repo_info = payload.get('repository', {})
                if repo_info:
                    owner = repo_info.get('owner', 'unknown')
                    repo_name = repo_info.get('name', 'unknown')
                    repository_id = generate_repository_id(provider, owner, repo_name)
                
                check_run_entity_id = f"check:{repository_id}:{check_run_id}"
                check_run_entity = {
                    'entityId': check_run_entity_id,
                    'entityType': 'check_run',
                    'projectId': project_id,
                    'repositoryId': repository_id,
                    'checkRunId': check_run_id,
                    'name': payload.get('name'),
                    'headSha': payload.get('headSha'),
                    'status': payload.get('status'),
                    'conclusion': payload.get('conclusion'),
                    'startedAt': payload.get('startedAt'),
                    'completedAt': payload.get('completedAt'),
                    'htmlUrl': payload.get('htmlUrl'),
                    'checkSuiteId': payload.get('checkSuiteId'),
                    'pullRequestNumbers': payload.get('pullRequests', []),
                    'outputTitle': payload.get('outputTitle'),
                    'outputSummary': payload.get('outputSummary'),
                    'outputText': payload.get('outputText'),
                    'annotationsCount': payload.get('annotationsCount'),
                    'annotationsUrl': payload.get('annotationsUrl'),
                    'createdAt': timestamp,
                    'updatedAt': timestamp,
                    'schemaVersion': GRAPH_SCHEMA_VERSION,
                }
                # Remove None values
                check_run_entity = {k: v for k, v in check_run_entity.items() if v is not None}
                upsert_graph_entity(check_run_entity)
                entities_created.append(check_run_entity_id)
                
                # Relationship: check run HAS_CHECK from workflow run
                check_suite_id = payload.get('checkSuiteId')
                if check_suite_id:
                    workflow_run_entity_id = f"workflow:{repository_id}:{check_suite_id}"
                    rel = {
                        'relationshipId': generate_relationship_id('has_check', workflow_run_entity_id, check_run_entity_id),
                        'relationshipType': 'has_check',
                        'sourceEntityId': workflow_run_entity_id,
                        'sourceEntityType': 'workflow_run',
                        'targetEntityId': check_run_entity_id,
                        'targetEntityType': 'check_run',
                        'projectId': project_id,
                        'repositoryId': repository_id,
                        'provenance': 'explicit',
                        'evidence': event_id,
                        'createdAt': timestamp,
                        'updatedAt': timestamp,
                        'schemaVersion': GRAPH_SCHEMA_VERSION,
                    }
                    upsert_graph_relationship(rel)
                    relationships_created.append(rel['relationshipId'])
                
                # Relationship: check run FAILED_IN file/commit if failed
                if payload.get('conclusion') == 'failure':
                    head_sha = payload.get('headSha')
                    if head_sha:
                        commit_id = generate_commit_id(repository_id, head_sha)
                        rel = {
                            'relationshipId': generate_relationship_id('failed_in', commit_id, check_run_entity_id),
                            'relationshipType': 'failed_in',
                            'sourceEntityId': commit_id,
                            'sourceEntityType': 'commit',
                            'targetEntityId': check_run_entity_id,
                            'targetEntityType': 'check_run',
                            'projectId': project_id,
                            'repositoryId': repository_id,
                            'provenance': 'inferred',
                            'evidence': event_id,
                            'createdAt': timestamp,
                            'updatedAt': timestamp,
                            'schemaVersion': GRAPH_SCHEMA_VERSION,
                        }
                        upsert_graph_relationship(rel)
                        relationships_created.append(rel['relationshipId'])
                
                # Create repository entity if not exists
                repo_entity = {
                    'entityId': repository_id,
                    'entityType': 'repository',
                    'projectId': project_id,
                    'repositoryId': repository_id,
                    'provider': provider,
                    'owner': owner,
                    'name': repo_name,
                    'defaultBranch': 'main',
                    'createdAt': timestamp,
                    'updatedAt': timestamp,
                    'schemaVersion': GRAPH_SCHEMA_VERSION,
                }
                upsert_graph_entity(repo_entity)
                entities_created.append(repository_id)
        
        elif event_type == 'check_suite':
            # Handle GitHub Actions check suite
            check_suite_id = payload.get('checkSuiteId')
            if check_suite_id:
                repo_info = payload.get('repository', {})
                if repo_info:
                    owner = repo_info.get('owner', 'unknown')
                    repo_name = repo_info.get('name', 'unknown')
                    repository_id = generate_repository_id(provider, owner, repo_name)
                
                # Check suite is essentially a workflow run, so we can create a workflow_run entity
                workflow_run_entity_id = f"workflow:{repository_id}:{check_suite_id}"
                workflow_run_entity = {
                    'entityId': workflow_run_entity_id,
                    'entityType': 'workflow_run',
                    'projectId': project_id,
                    'repositoryId': repository_id,
                    'workflowId': 0,  # Not directly available in check_suite
                    'workflowName': 'Check Suite',
                    'runId': check_suite_id,
                    'runNumber': 0,
                    'runAttempt': 1,
                    'event': 'check_suite',
                    'status': payload.get('status'),
                    'conclusion': payload.get('conclusion'),
                    'headBranch': payload.get('headBranch'),
                    'headSha': payload.get('headSha'),
                    'startedAt': payload.get('createdAt'),
                    'completedAt': payload.get('updatedAt'),
                    'htmlUrl': '',  # Not directly available
                    'checkSuiteId': check_suite_id,
                    'pullRequestNumbers': [pr.get('number') for pr in payload.get('pullRequests', [])],
                    'createdAt': timestamp,
                    'updatedAt': timestamp,
                    'schemaVersion': GRAPH_SCHEMA_VERSION,
                }
                upsert_graph_entity(workflow_run_entity)
                entities_created.append(workflow_run_entity_id)
                
                # Relationship: workflow run TRIGGERS from commit
                head_sha = payload.get('headSha')
                if head_sha:
                    commit_id = generate_commit_id(repository_id, head_sha)
                    rel = {
                        'relationshipId': generate_relationship_id('triggers', commit_id, workflow_run_entity_id),
                        'relationshipType': 'triggers',
                        'sourceEntityId': commit_id,
                        'sourceEntityType': 'commit',
                        'targetEntityId': workflow_run_entity_id,
                        'targetEntityType': 'workflow_run',
                        'projectId': project_id,
                        'repositoryId': repository_id,
                        'provenance': 'explicit',
                        'evidence': event_id,
                        'createdAt': timestamp,
                        'updatedAt': timestamp,
                        'schemaVersion': GRAPH_SCHEMA_VERSION,
                    }
                    upsert_graph_relationship(rel)
                    relationships_created.append(rel['relationshipId'])
                
                # Create repository entity if not exists
                repo_entity = {
                    'entityId': repository_id,
                    'entityType': 'repository',
                    'projectId': project_id,
                    'repositoryId': repository_id,
                    'provider': provider,
                    'owner': owner,
                    'name': repo_name,
                    'defaultBranch': payload.get('headBranch', 'main'),
                    'createdAt': timestamp,
                    'updatedAt': timestamp,
                    'schemaVersion': GRAPH_SCHEMA_VERSION,
                }
                upsert_graph_entity(repo_entity)
                entities_created.append(repository_id)
        
        # Ingest engineering decision from context extraction
        # The context extraction creates a context record with feature, decision, etc.
        # We can create an engineering_decision entity from this
        if event_type == 'push' and payload.get('commitHash'):
            # This will be linked after context extraction
            pass
        
        log_info(correlation_id, "Graph ingestion completed", 
                 entitiesCreated=len(entities_created),
                 relationshipsCreated=len(relationships_created))
        
    except Exception as e:
        log_error(correlation_id, "Graph ingestion failed", e, eventId=event_id)
        # Don't raise - graph ingestion failure shouldn't fail the main processing
        print(f"[graph] Non-fatal graph ingestion error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# STRUCTURED LOGGING WITH CORRELATION ID
# ─────────────────────────────────────────────────────────────────────────────

def log_structured(level: str, message: str, correlation_id: str = None, **kwargs):
    """Log a structured JSON entry with correlation ID."""
    entry = {
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'level': level,
        'message': message,
        'correlationId': correlation_id,
        **kwargs,
    }
    print(json.dumps(entry))

def log_error(correlation_id: str, message: str, error: Exception, **kwargs):
    log_structured('ERROR', message, correlation_id, error={
        'name': type(error).__name__,
        'message': str(error),
    }, **kwargs)

def log_info(correlation_id: str, message: str, **kwargs):
    log_structured('INFO', message, correlation_id, **kwargs)

def log_warn(correlation_id: str, message: str, **kwargs):
    log_structured('WARN', message, correlation_id, **kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# CORE PROCESSING FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def process_event_record(record: dict, correlation_id: str) -> dict:
    """
    Process a single event record from SQS.
    Returns the processing result.
    """
    # Extract message body
    try:
        body = json.loads(record.get('body', '{}'))
    except json.JSONDecodeError:
        raise ValueError("Invalid JSON in SQS message body")
    
    event_id = body.get('eventId')
    project_id = body.get('projectId')
    event_type = body.get('eventType')
    branch = body.get('branch')
    parent_branch = body.get('parentBranch')
    payload = body.get('payload', {})
    timestamp = body.get('timestamp', datetime.utcnow().isoformat() + 'Z')
    delivery_id = body.get('deliveryId')
    
    # Build idempotency key - use deliveryId for GitHub events, eventId otherwise
    idempotency_key = delivery_id or event_id
    if not idempotency_key:
        raise ValueError("Event missing both eventId and deliveryId")
    
    # Check idempotency
    is_duplicate, existing = check_idempotency(idempotency_key)
    if is_duplicate:
        log_info(correlation_id, "Duplicate event detected, skipping", 
                 idempotencyKey=idempotency_key, existingStatus=existing.get('status'))
        return {
            'status': 'duplicate',
            'eventId': event_id,
            'idempotencyKey': idempotency_key,
        }
    
    # Claim idempotency
    event_data = {
        'eventId': event_id,
        'projectId': project_id,
        'eventType': event_type,
        'branch': branch,
        'timestamp': timestamp,
    }
    if not claim_idempotency(idempotency_key, event_data):
        # Another process claimed it - treat as duplicate
        log_info(correlation_id, "Event claimed by another processor, skipping",
                 idempotencyKey=idempotency_key)
        return {
            'status': 'duplicate',
            'eventId': event_id,
            'idempotencyKey': idempotency_key,
        }
    
    try:
        # Process the event
        result = process_event(body, correlation_id)
        
        # Mark as completed
        complete_idempotency(idempotency_key, {'status': 'success', 'result': result})
        return result
    except Exception as e:
        fail_idempotency(idempotency_key, str(e))
        raise

def process_event(event_data: dict, correlation_id: str) -> dict:
    """Process a single event (core logic extracted from original handler)."""
    project_id = event_data.get("projectId", "test-project")
    event_id = event_data.get("eventId", "test-event")
    event_type = event_data.get("eventType")
    branch = event_data.get("branch", "main")
    timestamp = event_data.get("timestamp", datetime.utcnow().isoformat() + "Z")
    parent_branch = event_data.get("parentBranch")
    payload = event_data.get("payload", event_data)
    delivery_id = event_data.get("deliveryId")
    
    diff         = payload.get("diff", "")
    commit_hash  = payload.get("commitHash")
    message      = payload.get("message", "")
    author       = payload.get("author", "unknown")
    changed_files = payload.get("changedFiles", [])

    # Handle merge propagation
    if event_data.get('propagate'):
        source_branch = event_data.get('sourceBranch')
        target_branch = event_data.get('targetBranch')
        if not source_branch or not target_branch:
            raise ValueError('sourceBranch and targetBranch required for propagation')
        count = propagate_branch_context(project_id, source_branch, target_branch, timestamp)
        update_project_activity(project_id, timestamp)
        return {'status': 'propagated', 'count': count, 'from': source_branch, 'to': target_branch}

    log_info(correlation_id, "Processing event", eventId=event_id, eventType=event_type, branch=branch)

    # Direction B: Check for orphaned record if this is a commit event
    if commit_hash:
        orphaned = find_orphaned_record(project_id, branch, author, timestamp)
        if orphaned:
            update_orphaned_record(orphaned['eventId'], commit_hash, timestamp)
            
            audit_record = {
                "entityId": orphaned['eventId'],
                "action": "commit_linked",
                "timestamp": timestamp,
                "projectId": project_id,
                "branch": branch,
                "author": author
            }
            write_audit_record(audit_record)
            update_project_activity(project_id, timestamp)
            
            return {
                "status": "orphaned_updated",
                "eventId": orphaned['eventId'],
                "message": "Orphaned record updated with commitHash"
            }

    # Call Bedrock for extraction
    t_handler_start = time.time()
    event_data_for_bedrock = {
        "diff": diff, "commitHash": commit_hash, "message": message,
        "author": author, "branch": branch, "changedFiles": changed_files
    }
    extraction = call_bedrock(event_data_for_bedrock)
    bedrock_ms = extraction.pop('_bedrock_duration_ms', 0)
    validate_extraction_schema(extraction)
    extraction['confidence'] = compute_confidence(extraction)

    # Generate Titan embedding
    embedding_input = json.dumps(extraction)
    embedding, embedding_ms = call_titan_embedding(embedding_input)
    total_ms = int((time.time() - t_handler_start) * 1000)

    # Build context record
    context_record = {
        "eventId":            event_id,
        "projectId":          project_id,
        "branch":             branch,
        "branchExtractedAt":  f"{branch}#{timestamp}",
        "parentBranch":       parent_branch,
        "commitHash":         commit_hash,
        "status":             "complete" if commit_hash else "uncommitted",
        "feature":            extraction["feature"],
        "decision":           extraction["decision"],
        "tasks":              extraction["tasks"],
        "stage":              extraction["stage"],
        "risk":               extraction["risk"],
        "confidence":         extraction["confidence"],
        "entities":           extraction["entities"],
        "author":             author,
        "agentReasoning":     None,
        "modelVersion":       MODEL_ID,
        "embedding":          embedding,
        "extractedAt":        timestamp,
        "processingDuration": total_ms
    }
    write_context_record(context_record)

    # Benchmark log
    print(json.dumps({
        "BENCHMARK_LOG": True,
        "eventId":        context_record["eventId"],
        "projectId":      project_id,
        "branch":         branch,
        "author":         author,
        "bedrock_ms":     bedrock_ms,
        "embedding_ms":   embedding_ms,
        "total_ms":       total_ms,
        "diff_chars":     len(diff),
        "confidence":     float(extraction["confidence"]),
        "has_decision":   extraction["decision"] is not None,
        "has_risk":       extraction["risk"] is not None,
        "tasks_count":    len(extraction["tasks"]),
        "entities_count": len(extraction["entities"]),
        "timestamp":      timestamp
    }))

    # Audit record
    audit_record = {
        "entityId": context_record["eventId"],
        "action": "context_extracted",
        "timestamp": timestamp,
        "projectId": project_id,
        "branch": branch,
        "author": author
    }
    write_audit_record(audit_record)

    update_project_activity(project_id, timestamp)

    # Graph ingestion: create entities and relationships from this event
    try:
        ingest_graph_from_event(event_data, correlation_id)
        
        # Create engineering decision entity from extraction
        if extraction.get('decision') or extraction.get('risk') or extraction.get('tasks'):
            # Determine repository_id for graph relationships
            repository_id = None
            provider = "github"
            owner = "unknown"
            repo_name = "unknown"
            if event_data.get('source') == 'github' or payload.get('repository'):
                repo_info = payload.get('repository', {})
                owner = repo_info.get('owner', 'unknown')
                repo_name = repo_info.get('name', 'unknown')
                repository_id = generate_repository_id(provider, owner, repo_name)
            elif delivery_id and delivery_id.startswith('github-'):
                repository_id = generate_repository_id(provider, "unknown", "unknown")
            else:
                repository_id = f"repo:project:{project_id}"
            
            decision_id = generate_decision_id(project_id, 'context_extraction', event_id)
            decision_entity = {
                'entityId': decision_id,
                'entityType': 'engineering_decision',
                'projectId': project_id,
                'repositoryId': repository_id,
                'title': extraction.get('feature', 'Engineering Decision'),
                'summary': f"Extracted from commit {commit_hash[:8] if commit_hash else 'unknown'}",
                'decision': extraction.get('decision', ''),
                'rationale': extraction.get('risk'),
                'status': 'accepted',
                'sourceType': 'context_extraction',
                'sourceId': event_id,
                'createdAt': timestamp,
                'updatedAt': timestamp,
                'schemaVersion': GRAPH_SCHEMA_VERSION,
            }
            # Remove None values
            decision_entity = {k: v for k, v in decision_entity.items() if v is not None}
            upsert_graph_entity(decision_entity)
            
            # Relationship: decision RELATES_TO commit
            if commit_hash:
                commit_id = generate_commit_id(repository_id, commit_hash)
                rel = {
                    'relationshipId': generate_relationship_id('relates_to', decision_id, commit_id),
                    'relationshipType': 'relates_to',
                    'sourceEntityId': decision_id,
                    'sourceEntityType': 'engineering_decision',
                    'targetEntityId': commit_id,
                    'targetEntityType': 'commit',
                    'projectId': project_id,
                    'repositoryId': repository_id,
                    'provenance': 'inferred',
                    'confidence': float(extraction.get('confidence', 0.5)),
                    'evidence': event_id,
                    'createdAt': timestamp,
                    'updatedAt': timestamp,
                    'schemaVersion': GRAPH_SCHEMA_VERSION,
                }
                upsert_graph_relationship(rel)
    except Exception as e:
        log_error(correlation_id, "Graph ingestion failed", e, eventId=event_id)
        # Non-fatal - don't fail the main processing

    return {
        "status": "success",
        "eventId": context_record["eventId"],
        "message": "Context record written"
    }


# ─────────────────────────────────────────────────────────────────────────────
# SQS BATCH HANDLER
# ─────────────────────────────────────────────────────────────────────────────

def handler(event, context):
    """
    Main handler for SQS event source mapping.
    Processes batch of records with partial failure reporting.
    """
    print("AI Processing Lambda invoked", json.dumps(event, default=str))
    
    records = event.get('Records', [])
    if not records:
        log_warn(None, "No records in SQS event")
        return {'batchItemFailures': []}
    
    batch_item_failures = []
    
    for record in records:
        # Extract correlation ID from message attributes
        message_attrs = record.get('messageAttributes', {})
        correlation_id = message_attrs.get('correlationId', {}).get('stringValue')
        if not correlation_id:
            correlation_id = record.get('messageId', 'unknown')
        
        log_info(correlation_id, "Processing SQS record", messageId=record.get('messageId'))
        
        try:
            # Check if this is a merge propagation message
            body = json.loads(record.get('body', '{}'))
            if body.get('propagate'):
                # Handle merge propagation
                project_id = body.get('projectId', 'test-project')
                source_branch = body.get('sourceBranch')
                target_branch = body.get('targetBranch')
                timestamp = body.get('timestamp', datetime.utcnow().isoformat() + 'Z')
                if not source_branch or not target_branch:
                    raise ValueError('sourceBranch and targetBranch required for propagation')
                count = propagate_branch_context(project_id, source_branch, target_branch, timestamp)
                update_project_activity(project_id, timestamp)
                log_info(correlation_id, "Merge propagation completed", 
                         count=count, fromBranch=source_branch, toBranch=target_branch)
                continue
            
            # Process regular event with idempotency
            result = process_event_record(record, correlation_id)
            
            if result.get('status') == 'duplicate':
                log_info(correlation_id, "Skipped duplicate event", 
                         eventId=result.get('eventId'))
            else:
                log_info(correlation_id, "Event processed successfully", 
                         eventId=result.get('eventId'), status=result.get('status'))
            
        except Exception as e:
            log_error(correlation_id, "Failed to process record", e, messageId=record.get('messageId'))
            batch_item_failures.append({'itemIdentifier': record.get('messageId')})
    
    return {'batchItemFailures': batch_item_failures}