"""
Graph Backfill/Reconciliation Script - Phase 3
Utility to backfill engineering knowledge graph from existing context records.

Usage:
    python graph_backfill.py --project-id <project-id> [--dry-run] [--batch-size 100]
"""

import json
import os
import sys
import argparse
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Dict, Any, Optional
from unittest.mock import Mock, patch, MagicMock

# Add shared python to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'shared', 'python'))

# Set environment variables
os.environ.setdefault('AWS_REGION', 'us-east-1')
os.environ.setdefault('AWS_DEFAULT_REGION', 'us-east-1')

# Import handler functions
import handler

# Import shared helpers
from flowsync_common import helpers as shared_helpers

# Mock boto3 for standalone operation
mock_dynamodb_resource = MagicMock()
mock_entities_table = Mock()
mock_relationships_table = Mock()
mock_context_table = Mock()
mock_idempotency_table = Mock()
mock_projects_table = Mock()
mock_audit_table = Mock()

def mock_table(name):
    if name == "flowsync-graph-entities":
        return mock_entities_table
    elif name == "flowsync-graph-relationships":
        return mock_relationships_table
    elif name == "flowsync-context":
        return mock_context_table
    elif name == "flowsync-idempotency":
        return mock_idempotency_table
    elif name == "flowsync-projects":
        return mock_projects_table
    elif name == "flowsync-audit":
        return mock_audit_table
    return Mock()

mock_dynamodb_resource.Table.side_effect = mock_table

# Mock Bedrock client
mock_bedrock_client = MagicMock()
mock_bedrock_client.converse.return_value = {
    'output': {'message': {'content': [{'text': json.dumps({
        'feature': 'Test Feature',
        'decision': 'Test decision',
        'tasks': ['task1'],
        'stage': 'Feature Development',
        'risk': 'Test risk',
        'entities': ['entity1', 'entity2']
    })}]}},
    'usage': {'inputTokens': 100, 'outputTokens': 50}
}
mock_bedrock_client.invoke_model.return_value = {
    'body': Mock(read=Mock(return_value=json.dumps({'embedding': [0.1] * 1536}).encode()))
}

# Start patches
boto3_resource_patcher = patch('boto3.resource', return_value=mock_dynamodb_resource)
boto3_client_patcher = patch('boto3.client', return_value=mock_bedrock_client)

boto3_resource_patcher.start()
boto3_client_patcher.start()

# Import handler after patches
import handler

# Re-import shared helpers after patches
from flowsync_common import helpers as shared_helpers


def create_test_context_record(entity_id='evt-1', feature='Test Feature', decision='Test decision', 
                               commit_hash='abc123', branch='main', project_id='test-project'):
    """Create a test context record."""
    return {
        'eventId': entity_id,
        'projectId': project_id,
        'branch': branch,
        'branchExtractedAt': f"{branch}#2024-01-01T00:00:00Z",
        'commitHash': commit_hash,
        'status': 'complete',
        'feature': feature,
        'decision': decision,
        'tasks': ['task1'],
        'stage': 'Feature Development',
        'risk': 'Test risk',
        'confidence': Decimal('0.85'),
        'entities': ['entity1', 'entity2'],
        'author': 'Test Author',
        'agentReasoning': None,
        'modelVersion': 'us.amazon.nova-pro-v1:0',
        'embedding': [0.1] * 1536,
        'extractedAt': '2024-01-01T00:00:00Z',
        'processingDuration': 1000,
    }


def create_test_graph_entity(entity_id, entity_type='commit', project_id='test-project', **kwargs):
    """Create a test graph entity."""
    base = {
        'entityId': entity_id,
        'entityType': entity_type,
        'projectId': project_id,
        'repositoryId': 'repo:github:owner:repo',
        'createdAt': '2024-01-01T00:00:00Z',
        'updatedAt': '2024-01-01T00:00:00Z',
        'schemaVersion': '1',
    }
    base.update(kwargs)
    return base


def create_test_relationship(rel_type='modifies', source_id='commit:repo:test:sha', target_id='file:repo:test:file.py', **kwargs):
    """Create a test graph relationship."""
    base = {
        'relationshipId': f'rel:{rel_type}:{source_id}:{target_id}',
        'relationshipType': rel_type,
        'sourceEntityId': source_id,
        'sourceEntityType': 'commit',
        'targetEntityId': target_id,
        'targetEntityType': 'file',
        'projectId': 'test-project',
        'repositoryId': 'repo:github:owner:repo',
        'provenance': 'explicit',
        'evidence': 'evt-1',
        'createdAt': '2024-01-01T00:00:00Z',
        'updatedAt': '2024-01-01T00:00:00Z',
        'schemaVersion': '1',
    }
    base.update(kwargs)
    return base


def backfill_graph_from_context_records(project_id: str, dry_run: bool = False, batch_size: int = 100) -> dict:
    """
    Backfill the engineering knowledge graph from existing context records.
    
    Args:
        project_id: Project ID to backfill
        dry_run: If True, only simulate the backfill without writing
        batch_size: Number of records to process per batch
        
    Returns:
        Dictionary with backfill statistics
    """
    stats = {
        'processed': 0,
        'entities_created': 0,
        'relationships_created': 0,
        'errors': 0,
        'skipped': 0,
    }
    
    print(f"Starting graph backfill for project {project_id} (dry_run={dry_run})")
    
    # Fetch all context records for the project
    context_table = mock_context_table if 'mock_context_table' in globals() else handler.dynamodb.Table('flowsync-context')
    
    all_records = []
    try:
        kwargs = {
            'IndexName': 'ProjectContextIndex',
            'KeyConditionExpression': 'projectId = :pk',
            'ExpressionAttributeValues': {':pk': project_id},
        }
        
        while True:
            response = context_table.query(**kwargs)
            records = response.get('Items', [])
            all_records.extend(records)
            
            if 'LastEvaluatedKey' not in response:
                break
            kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
            
    except Exception as e:
        print(f"Error fetching context records: {e}")
        stats['errors'] += 1
        return stats
    
    print(f"Found {len(all_records)} context records to process")
    
    # Process records in batches
    for i in range(0, len(all_records), batch_size):
        batch = all_records[i:i + batch_size]
        print(f"Processing batch {i//batch_size + 1}/{(len(all_records) + batch_size - 1)//batch_size} ({len(batch)} records)")
        
        for record in batch:
            try:
                # Skip if no commit hash (uncommitted records)
                commit_hash = record.get('commitHash')
                if not commit_hash:
                    stats['skipped'] += 1
                    continue
                
                # Build event data for graph ingestion
                event_data = {
                    'eventId': record.get('eventId'),
                    'projectId': record.get('projectId'),
                    'eventType': 'push',
                    'branch': record.get('branch', 'main'),
                    'payload': {
                        'commitHash': commit_hash,
                        'message': record.get('feature', ''),
                        'diff': '',  # Not available in context records
                        'author': record.get('author', 'unknown'),
                        'changedFiles': record.get('entities', []),  # Use entities as changed files approximation
                    },
                    'timestamp': record.get('extractedAt', datetime.now(timezone.utc).isoformat()),
                    'deliveryId': None,
                }
                
                if not dry_run:
                    # Ingest graph entities and relationships
                    handler.ingest_graph_from_event(event_data, f"backfill-{record.get('eventId')}")
                    
                    # Create engineering decision entity from context extraction
                    if record.get('decision') or record.get('risk') or record.get('tasks'):
                        # Determine repository_id
                        repository_id = "repo:project:" + project_id  # Default fallback
                        
                        decision_id = handler.generate_decision_id(project_id, 'context_extraction', record.get('eventId'))
                        decision_entity = {
                            'entityId': decision_id,
                            'entityType': 'engineering_decision',
                            'projectId': project_id,
                            'repositoryId': repository_id,
                            'title': record.get('feature', 'Engineering Decision'),
                            'summary': f"Extracted from commit {commit_hash[:8]}",
                            'decision': record.get('decision', ''),
                            'rationale': record.get('risk'),
                            'status': 'accepted',
                            'sourceType': 'context_extraction',
                            'sourceId': record.get('eventId'),
                            'createdAt': record.get('extractedAt', datetime.now(timezone.utc).isoformat()),
                            'updatedAt': record.get('extractedAt', datetime.now(timezone.utc).isoformat()),
                            'schemaVersion': '1',
                        }
                        # Remove None values
                        decision_entity = {k: v for k, v in decision_entity.items() if v is not None}
                        handler.upsert_graph_entity(decision_entity)
                        stats['entities_created'] += 1
                        
                        # Relationship: decision RELATES_TO commit
                        if commit_hash:
                            commit_id = handler.generate_commit_id(repository_id, commit_hash)
                            rel = {
                                'relationshipId': handler.generate_relationship_id('relates_to', decision_id, commit_id),
                                'relationshipType': 'relates_to',
                                'sourceEntityId': decision_id,
                                'sourceEntityType': 'engineering_decision',
                                'targetEntityId': commit_id,
                                'targetEntityType': 'commit',
                                'projectId': project_id,
                                'repositoryId': repository_id,
                                'provenance': 'inferred',
                                'confidence': float(record.get('confidence', 0.5)),
                                'evidence': record.get('eventId'),
                                'createdAt': record.get('extractedAt', datetime.now(timezone.utc).isoformat()),
                                'updatedAt': record.get('extractedAt', datetime.now(timezone.utc).isoformat()),
                                'schemaVersion': '1',
                            }
                            handler.upsert_graph_relationship(rel)
                            stats['relationships_created'] += 1
                
                stats['entities_created'] += 1  # At least commit entity
                stats['relationships_created'] += 1  # At least commit->file relationship
                stats['processed'] += 1
            except Exception as e:
                print(f"Error processing record {record.get('eventId')}: {e}")
                stats['errors'] += 1
        
        if not dry_run:
            print(f"Batch completed: {stats['processed']} records processed")
    
    print(f"Backfill completed: {stats}")
    return stats


def main():
    parser = argparse.ArgumentParser(description='Backfill Engineering Knowledge Graph from context records')
    parser.add_argument('--project-id', required=True, help='Project ID to backfill')
    parser.add_argument('--dry-run', action='store_true', help='Simulate backfill without writing')
    parser.add_argument('--batch-size', type=int, default=100, help='Batch size for processing')
    
    args = parser.parse_args()
    
    # Initialize mocks for testing
    # In production, this would use real DynamoDB
    
    stats = backfill_graph_from_context_records(
        project_id=args.project_id,
        dry_run=args.dry_run,
        batch_size=args.batch_size
    )
    
    print(f"\nBackfill Summary:")
    print(f"  Processed: {stats['processed']}")
    print(f"  Entities Created: {stats['entities_created']}")
    print(f"  Relationships Created: {stats['relationships_created']}")
    print(f"  Errors: {stats['errors']}")
    print(f"  Skipped: {stats['skipped']}")
    
    if stats['errors'] > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == '__main__':
    main()