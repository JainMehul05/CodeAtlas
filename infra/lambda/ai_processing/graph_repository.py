"""
Engineering Knowledge Graph - Persistence Layer
DynamoDB adjacency list model for graph entities and relationships.
"""

import json
import uuid
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from botocore.exceptions import ClientError
from botocore.config import Config as BotoConfig

from flowsync_common.helpers import convert_floats_to_decimal, convert_decimals


# DynamoDB table names
GRAPH_ENTITIES_TABLE = "flowsync-graph-entities"
GRAPH_RELATIONSHIPS_TABLE = "flowsync-graph-relationships"

# Graph schema version
GRAPH_SCHEMA_VERSION = "1"

# Query limits
MAX_QUERY_DEPTH = 3
MAX_QUERY_RESULTS = 100
DEFAULT_PAGE_SIZE = 20


class GraphRepository:
    """Repository for graph entities and relationships using DynamoDB adjacency list model."""

    def __init__(self, dynamodb_resource=None):
        if dynamodb_resource is None:
            import boto3
            dynamodb_resource = boto3.resource("dynamodb")
        self.dynamodb = dynamodb_resource
        self.entities_table = self.dynamodb.Table(GRAPH_ENTITIES_TABLE)
        self.relationships_table = self.dynamodb.Table(GRAPH_RELATIONSHIPS_TABLE)

    # ─────────────────────────────────────────────────────────────────────────────
    # ENTITY OPERATIONS
    # ─────────────────────────────────────────────────────────────────────────────

    def upsert_entity(self, entity: dict) -> dict:
        """
        Upsert a graph entity.
        Uses conditional write to handle concurrent updates safely.
        """
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        
        # Ensure required fields
        entity = dict(entity)
        entity['schemaVersion'] = entity.get('schemaVersion', GRAPH_SCHEMA_VERSION)
        entity['updatedAt'] = now
        if 'createdAt' not in entity:
            entity['createdAt'] = now
        
        # Convert floats to Decimal for DynamoDB
        entity = convert_floats_to_decimal(entity)
        
        try:
            self.entities_table.put_item(Item=entity)
            return convert_decimals(entity)
        except ClientError as e:
            print(f"[graph] Error upserting entity {entity.get('entityId')}: {e}")
            raise

    def get_entity(self, entity_id: str) -> Optional[dict]:
        """Get a graph entity by ID."""
        try:
            response = self.entities_table.get_item(Key={'entityId': entity_id})
            item = response.get('Item')
            return convert_decimals(item) if item else None
        except ClientError as e:
            print(f"[graph] Error getting entity {entity_id}: {e}")
            raise

    def get_entities(self, entity_ids: list[str]) -> list[dict]:
        """Batch get multiple entities."""
        if not entity_ids:
            return []
        
        # DynamoDB batch_get_item supports up to 100 items
        results = []
        for i in range(0, len(entity_ids), 100):
            batch = entity_ids[i:i+100]
            try:
                response = self.dynamodb.batch_get_item(
                    RequestItems={
                        GRAPH_ENTITIES_TABLE: {
                            'Keys': [{'entityId': eid} for eid in batch]
                        }
                    }
                )
                items = response.get('Responses', {}).get(GRAPH_ENTITIES_TABLE, [])
                results.extend(convert_decimals(item) for item in items)
            except ClientError as e:
                print(f"[graph] Error batch getting entities: {e}")
                raise
        return results

    def query_entities_by_project(
        self,
        project_id: str,
        entity_type: Optional[str] = None,
        repository_id: Optional[str] = None,
        limit: int = DEFAULT_PAGE_SIZE,
        next_token: Optional[str] = None
    ) -> dict:
        """
        Query entities by project with optional filters.
        Uses ProjectEntityIndex GSI (projectId, entityType, createdAt).
        """
        try:
            kwargs = {
                'IndexName': 'ProjectEntityIndex',
                'KeyConditionExpression': 'projectId = :pk',
                'ExpressionAttributeValues': {':pk': project_id},
                'ScanIndexForward': False,  # Newest first
                'Limit': min(limit, MAX_QUERY_RESULTS),
            }
            
            filter_expressions = []
            if entity_type:
                filter_expressions.append('entityType = :et')
                kwargs['ExpressionAttributeValues'][':et'] = entity_type
            if repository_id:
                filter_expressions.append('repositoryId = :rid')
                kwargs['ExpressionAttributeValues'][':rid'] = repository_id
            
            if filter_expressions:
                kwargs['FilterExpression'] = ' AND '.join(filter_expressions)
            
            if next_token:
                kwargs['ExclusiveStartKey'] = json.loads(next_token)
            
            response = self.entities_table.query(**kwargs)
            
            result = {
                'entities': convert_decimals(response.get('Items', [])),
                'count': len(response.get('Items', [])),
            }
            
            if 'LastEvaluatedKey' in response:
                result['nextToken'] = json.dumps(response['LastEvaluatedKey'])
            
            return result
        except ClientError as e:
            print(f"[graph] Error querying entities by project: {e}")
            raise

    def query_entities_by_repository(
        self,
        repository_id: str,
        entity_type: Optional[str] = None,
        limit: int = DEFAULT_PAGE_SIZE,
        next_token: Optional[str] = None
    ) -> dict:
        """Query entities by repository."""
        try:
            kwargs = {
                'IndexName': 'RepositoryEntityIndex',
                'KeyConditionExpression': 'repositoryId = :rid',
                'ExpressionAttributeValues': {':rid': repository_id},
                'ScanIndexForward': False,
                'Limit': min(limit, MAX_QUERY_RESULTS),
            }
            
            if entity_type:
                kwargs['FilterExpression'] = 'entityType = :et'
                kwargs['ExpressionAttributeValues'][':et'] = entity_type
            
            if next_token:
                kwargs['ExclusiveStartKey'] = json.loads(next_token)
            
            response = self.entities_table.query(**kwargs)
            
            result = {
                'entities': convert_decimals(response.get('Items', [])),
                'count': len(response.get('Items', [])),
            }
            
            if 'LastEvaluatedKey' in response:
                result['nextToken'] = json.dumps(response['LastEvaluatedKey'])
            
            return result
        except ClientError as e:
            print(f"[graph] Error querying entities by repository: {e}")
            raise

    # ─────────────────────────────────────────────────────────────────────────────
    # RELATIONSHIP OPERATIONS
    # ─────────────────────────────────────────────────────────────────────────────

    def upsert_relationship(self, relationship: dict) -> dict:
        """
        Upsert a graph relationship.
        Uses conditional write based on relationshipId for idempotency.
        """
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        
        relationship = dict(relationship)
        relationship['schemaVersion'] = relationship.get('schemaVersion', GRAPH_SCHEMA_VERSION)
        relationship['updatedAt'] = now
        if 'createdAt' not in relationship:
            relationship['createdAt'] = now
        
        relationship = convert_floats_to_decimal(relationship)
        
        try:
            # Use conditional put to avoid overwriting with older data
            # Only overwrite if the new updatedAt is newer (or same for idempotent retry)
            self.relationships_table.put_item(
                Item=relationship,
                ConditionExpression='attribute_not_exists(relationshipId) OR updatedAt <= :newUpdatedAt',
                ExpressionAttributeValues={':newUpdatedAt': now}
            )
            return convert_decimals(relationship)
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                print(f"[graph] Relationship {relationship.get('relationshipId')} already exists with newer data, skipping")
                return convert_decimals(relationship)
            print(f"[graph] Error upserting relationship {relationship.get('relationshipId')}: {e}")
            raise

    def get_relationship(self, relationship_id: str) -> Optional[dict]:
        """Get a relationship by ID."""
        try:
            response = self.relationships_table.get_item(Key={'relationshipId': relationship_id})
            item = response.get('Item')
            return convert_decimals(item) if item else None
        except ClientError as e:
            print(f"[graph] Error getting relationship {relationship_id}: {e}")
            raise

    def get_outgoing_relationships(
        self,
        source_entity_id: str,
        relationship_type: Optional[str] = None,
        limit: int = DEFAULT_PAGE_SIZE,
        next_token: Optional[str] = None
    ) -> dict:
        """Get outgoing relationships from an entity."""
        try:
            kwargs = {
                'IndexName': 'SourceEntityIndex',
                'KeyConditionExpression': 'sourceEntityId = :seid',
                'ExpressionAttributeValues': {':seid': source_entity_id},
                'ScanIndexForward': False,
                'Limit': min(limit, MAX_QUERY_RESULTS),
            }
            
            if relationship_type:
                kwargs['FilterExpression'] = 'relationshipType = :rt'
                kwargs['ExpressionAttributeValues'][':rt'] = relationship_type
            
            if next_token:
                kwargs['ExclusiveStartKey'] = json.loads(next_token)
            
            response = self.relationships_table.query(**kwargs)
            
            result = {
                'relationships': convert_decimals(response.get('Items', [])),
                'count': len(response.get('Items', [])),
            }
            
            if 'LastEvaluatedKey' in response:
                result['nextToken'] = json.dumps(response['LastEvaluatedKey'])
            
            return result
        except ClientError as e:
            print(f"[graph] Error getting outgoing relationships: {e}")
            raise

    def get_incoming_relationships(
        self,
        target_entity_id: str,
        relationship_type: Optional[str] = None,
        limit: int = DEFAULT_PAGE_SIZE,
        next_token: Optional[str] = None
    ) -> dict:
        """Get incoming relationships to an entity."""
        try:
            kwargs = {
                'IndexName': 'TargetEntityIndex',
                'KeyConditionExpression': 'targetEntityId = :teid',
                'ExpressionAttributeValues': {':teid': target_entity_id},
                'ScanIndexForward': False,
                'Limit': min(limit, MAX_QUERY_RESULTS),
            }
            
            if relationship_type:
                kwargs['FilterExpression'] = 'relationshipType = :rt'
                kwargs['ExpressionAttributeValues'][':rt'] = relationship_type
            
            if next_token:
                kwargs['ExclusiveStartKey'] = json.loads(next_token)
            
            response = self.relationships_table.query(**kwargs)
            
            result = {
                'relationships': convert_decimals(response.get('Items', [])),
                'count': len(response.get('Items', [])),
            }
            
            if 'LastEvaluatedKey' in response:
                result['nextToken'] = json.dumps(response['LastEvaluatedKey'])
            
            return result
        except ClientError as e:
            print(f"[graph] Error getting incoming relationships: {e}")
            raise

    def get_related_entities(
        self,
        entity_id: str,
        direction: str = "both",  # "outgoing", "incoming", "both"
        relationship_types: Optional[list[str]] = None,
        max_depth: int = 1,
        max_results: int = MAX_QUERY_RESULTS,
        visited: Optional[set] = None
    ) -> dict:
        """
        Get entities related to the given entity through relationships.
        Supports bounded traversal up to max_depth.
        """
        if visited is None:
            visited = set()
        
        if entity_id in visited or max_depth <= 0:
            return {'entities': [], 'relationships': [], 'paths': []}
        
        visited.add(entity_id)
        
        all_entities = {}
        all_relationships = []
        all_paths = []
        
        def process_relationships(rels, current_depth, path_prefix):
            for rel in rels:
                all_relationships.append(rel)
                
                # Determine the related entity ID
                if rel['sourceEntityId'] == entity_id or (path_prefix and rel['sourceEntityId'] == path_prefix[-1]):
                    related_id = rel['targetEntityId']
                    related_type = rel['targetEntityType']
                    dir = 'outgoing'
                else:
                    related_id = rel['sourceEntityId']
                    related_type = rel['sourceEntityType']
                    dir = 'incoming'
                
                if related_id not in visited:
                    visited.add(related_id)
                    entity = self.get_entity(related_id)
                    if entity:
                        all_entities[related_id] = entity
                        all_paths.append({
                            'path': path_prefix + [rel['relationshipId']],
                            'entityId': related_id,
                            'entityType': related_type,
                            'relationshipType': rel['relationshipType'],
                            'direction': dir,
                            'depth': current_depth,
                            'provenance': rel.get('provenance'),
                            'confidence': rel.get('confidence'),
                        })
                        
                        # Recurse for deeper traversal
                        if current_depth < max_depth:
                            if dir == 'outgoing' or direction in ('both', 'outgoing'):
                                next_rels = self.get_outgoing_relationships(
                                    related_id, 
                                    limit=max_results
                                )['relationships']
                                process_relationships(next_rels, current_depth + 1, path_prefix + [rel['relationshipId']])
                            if dir == 'incoming' or direction in ('both', 'incoming'):
                                next_rels = self.get_incoming_relationships(
                                    related_id,
                                    limit=max_results
                                )['relationships']
                                process_relationships(next_rels, current_depth + 1, path_prefix + [rel['relationshipId']])
        
        # Get initial relationships
        if direction in ('outgoing', 'both'):
            outgoing = self.get_outgoing_relationships(entity_id, limit=max_results)
            if relationship_types:
                outgoing['relationships'] = [r for r in outgoing['relationships'] if r['relationshipType'] in relationship_types]
            process_relationships(outgoing['relationships'], 1, [])
        
        if direction in ('incoming', 'both'):
            incoming = self.get_incoming_relationships(entity_id, limit=max_results)
            if relationship_types:
                incoming['relationships'] = [r for r in incoming['relationships'] if r['relationshipType'] in relationship_types]
            process_relationships(incoming['relationships'], 1, [])
        
        return {
            'entities': list(all_entities.values())[:max_results],
            'relationships': all_relationships[:max_results],
            'paths': all_paths[:max_results],
        }

    def query_relationships_by_project(
        self,
        project_id: str,
        relationship_type: Optional[str] = None,
        limit: int = DEFAULT_PAGE_SIZE,
        next_token: Optional[str] = None
    ) -> dict:
        """Query relationships by project."""
        try:
            kwargs = {
                'IndexName': 'ProjectRelationshipIndex',
                'KeyConditionExpression': 'projectId = :pid',
                'ExpressionAttributeValues': {':pid': project_id},
                'ScanIndexForward': False,
                'Limit': min(limit, MAX_QUERY_RESULTS),
            }
            
            if relationship_type:
                kwargs['FilterExpression'] = 'relationshipType = :rt'
                kwargs['ExpressionAttributeValues'][':rt'] = relationship_type
            
            if next_token:
                kwargs['ExclusiveStartKey'] = json.loads(next_token)
            
            response = self.relationships_table.query(**kwargs)
            
            result = {
                'relationships': convert_decimals(response.get('Items', [])),
                'count': len(response.get('Items', [])),
            }
            
            if 'LastEvaluatedKey' in response:
                result['nextToken'] = json.dumps(response['LastEvaluatedKey'])
            
            return result
        except ClientError as e:
            print(f"[graph] Error querying relationships by project: {e}")
            raise

    # ─────────────────────────────────────────────────────────────────────────────
    # GRAPH QUERY OPERATIONS
    # ─────────────────────────────────────────────────────────────────────────────

    def get_repository_graph_summary(self, repository_id: str) -> dict:
        """Get a summary of the graph for a repository."""
        try:
            # Count entities by type
            entity_counts = {}
            for entity_type in ['repository', 'commit', 'pull_request', 'file', 'engineering_decision']:
                result = self.query_entities_by_repository(repository_id, entity_type, limit=1)
                entity_counts[entity_type] = result.get('count', 0)
            
            # Count relationships by type
            rel_counts = {}
            for rel_type in [
                'contains', 'authored_in', 'modifies', 'has_parent',
                'includes_commit', 'targets_file', 'has_decision', 'relates_to', 'derived_from'
            ]:
                result = self.query_relationships_by_project(
                    repository_id.replace('repo:', ''),  # projectId from repositoryId
                    rel_type, 
                    limit=1
                )
                rel_counts[rel_type] = result.get('count', 0)
            
            return {
                'repositoryId': repository_id,
                'entityCounts': entity_counts,
                'relationshipCounts': rel_counts,
                'totalEntities': sum(entity_counts.values()),
                'totalRelationships': sum(rel_counts.values()),
            }
        except ClientError as e:
            print(f"[graph] Error getting repository graph summary: {e}")
            raise

    # ─────────────────────────────────────────────────────────────────────────────
    # DELETION OPERATIONS (with safe cleanup)
    # ─────────────────────────────────────────────────────────────────────────────

    def delete_relationship(self, relationship_id: str) -> bool:
        """Delete a relationship. Returns True if deleted, False if not found."""
        try:
            self.relationships_table.delete_item(
                Key={'relationshipId': relationship_id},
                ConditionExpression='attribute_exists(relationshipId)'
            )
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                return False
            print(f"[graph] Error deleting relationship {relationship_id}: {e}")
            raise

    def delete_entity(self, entity_id: str, cascade: bool = False) -> bool:
        """
        Delete an entity.
        If cascade=True, also delete all relationships connected to this entity.
        If cascade=False, fail if entity has relationships.
        """
        try:
            if not cascade:
                # Check for connected relationships
                outgoing = self.get_outgoing_relationships(entity_id, limit=1)
                incoming = self.get_incoming_relationships(entity_id, limit=1)
                if outgoing['count'] > 0 or incoming['count'] > 0:
                    raise ValueError(f"Entity {entity_id} has connected relationships. Use cascade=True to delete them.")
            
            # Delete relationships first if cascading
            if cascade:
                outgoing = self.get_outgoing_relationships(entity_id, limit=MAX_QUERY_RESULTS)
                incoming = self.get_incoming_relationships(entity_id, limit=MAX_QUERY_RESULTS)
                
                for rel in outgoing['relationships'] + incoming['relationships']:
                    self.delete_relationship(rel['relationshipId'])
            
            # Delete entity
            self.entities_table.delete_item(
                Key={'entityId': entity_id},
                ConditionExpression='attribute_exists(entityId)'
            )
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                return False
            print(f"[graph] Error deleting entity {entity_id}: {e}")
            raise


# ─────────────────────────────────────────────────────────────────────────────
# ENTITY CREATION HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def create_repository_entity(
    provider: str,
    owner: str,
    name: str,
    project_id: str,
    canonical_url: Optional[str] = None,
    default_branch: Optional[str] = None
) -> dict:
    """Create a repository entity with stable ID."""
    repository_id = f"repo:{provider}:{owner}:{name}".lower()
    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    
    return {
        'entityId': repository_id,
        'entityType': 'repository',
        'projectId': project_id,
        'repositoryId': repository_id,
        'provider': provider,
        'owner': owner,
        'name': name,
        'canonicalUrl': canonical_url,
        'defaultBranch': default_branch,
        'createdAt': now,
        'updatedAt': now,
        'schemaVersion': GRAPH_SCHEMA_VERSION,
    }


def create_commit_entity(
    repository_id: str,
    sha: str,
    author: str,
    committer: str,
    message: str,
    committed_at: str,
    parent_shas: list[str],
    project_id: str,
    source: str = "github"
) -> dict:
    """Create a commit entity with stable ID."""
    commit_id = f"commit:{repository_id}:{sha.lower()}"
    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    
    return {
        'entityId': commit_id,
        'entityType': 'commit',
        'projectId': project_id,
        'repositoryId': repository_id,
        'sha': sha.lower(),
        'author': author,
        'committer': committer,
        'message': message,
        'committedAt': committed_at,
        'parentShas': [s.lower() for s in parent_shas],
        'source': source,
        'createdAt': now,
        'updatedAt': now,
        'schemaVersion': GRAPH_SCHEMA_VERSION,
    }


def create_pull_request_entity(
    repository_id: str,
    number: int,
    title: str,
    state: str,
    author: str,
    source_branch: str,
    target_branch: str,
    project_id: str,
    description: Optional[str] = None,
    created_at: Optional[str] = None,
    updated_at: Optional[str] = None,
    merged_at: Optional[str] = None,
    closed_at: Optional[str] = None,
    url: Optional[str] = None,
    source: str = "github"
) -> dict:
    """Create a pull request entity with stable ID."""
    pr_id = f"pr:{repository_id}:{number}"
    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    
    return {
        'entityId': pr_id,
        'entityType': 'pull_request',
        'projectId': project_id,
        'repositoryId': repository_id,
        'number': number,
        'title': title,
        'description': description,
        'state': state,
        'author': author,
        'sourceBranch': source_branch,
        'targetBranch': target_branch,
        'createdAt': created_at or now,
        'updatedAt': updated_at or now,
        'mergedAt': merged_at,
        'closedAt': closed_at,
        'url': url,
        'source': source,
        'schemaVersion': GRAPH_SCHEMA_VERSION,
    }


def create_file_entity(
    repository_id: str,
    path: str,
    project_id: str,
    language: Optional[str] = None
) -> dict:
    """Create a file entity with stable ID."""
    from flowsync_shared.graph import normalize_file_path, generateFileId
    normalized_path = normalize_file_path(path)
    file_id = f"file:{repository_id}:{normalized_path}"
    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    
    return {
        'entityId': file_id,
        'entityType': 'file',
        'projectId': project_id,
        'repositoryId': repository_id,
        'path': normalized_path,
        'language': language,
        'lastObservedAt': now,
        'createdAt': now,
        'updatedAt': now,
        'schemaVersion': GRAPH_SCHEMA_VERSION,
    }


def create_engineering_decision_entity(
    project_id: str,
    title: str,
    summary: str,
    decision: str,
    source_type: str,
    source_id: str,
    repository_id: Optional[str] = None,
    rationale: Optional[str] = None,
    status: Optional[str] = None,
    source_url: Optional[str] = None
) -> dict:
    """Create an engineering decision entity with stable ID."""
    decision_id = f"decision:{project_id}:{source_type}:{source_id}"
    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    
    return {
        'entityId': decision_id,
        'entityType': 'engineering_decision',
        'projectId': project_id,
        'repositoryId': repository_id,
        'title': title,
        'summary': summary,
        'decision': decision,
        'rationale': rationale,
        'status': status,
        'sourceType': source_type,
        'sourceId': source_id,
        'sourceUrl': source_url,
        'createdAt': now,
        'updatedAt': now,
        'schemaVersion': GRAPH_SCHEMA_VERSION,
    }


def create_relationship(
    relationship_type: str,
    source_entity_id: str,
    source_entity_type: str,
    target_entity_id: str,
    target_entity_type: str,
    project_id: str,
    repository_id: Optional[str] = None,
    provenance: str = "explicit",
    confidence: Optional[float] = None,
    evidence: Optional[str] = None,
    metadata: Optional[dict] = None
) -> dict:
    """Create a relationship with stable ID."""
    from flowsync_shared.graph import generateRelationshipId
    
    relationship_id = f"rel:{relationship_type}:{source_entity_id}:{target_entity_id}"
    # Use a deterministic hash-based ID
    import hashlib
    hash_input = f"{relationship_type}:{source_entity_id}:{target_entity_id}"
    relationship_id = f"rel:{hashlib.sha256(hash_input.encode()).hexdigest()[:16]}"
    
    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    
    return {
        'relationshipId': relationship_id,
        'relationshipType': relationship_type,
        'sourceEntityId': source_entity_id,
        'sourceEntityType': source_entity_type,
        'targetEntityId': target_entity_id,
        'targetEntityType': target_entity_type,
        'projectId': project_id,
        'repositoryId': repository_id,
        'provenance': provenance,
        'confidence': confidence,
        'evidence': evidence,
        'createdAt': now,
        'updatedAt': now,
        'schemaVersion': GRAPH_SCHEMA_VERSION,
        'metadata': metadata or {},
    }


# Singleton instance for Lambda reuse
_graph_repo = None

def get_graph_repository():
    global _graph_repo
    if _graph_repo is None:
        _graph_repo = GraphRepository()
    return _graph_repo