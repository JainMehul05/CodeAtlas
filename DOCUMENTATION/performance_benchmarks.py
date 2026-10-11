#!/usr/bin/env python3
"""
CodeAtlas Phase 5 Performance Benchmarks

This script runs synthetic benchmarks for:
1. Graph traversal at multiple deterministic graph sizes
2. RAG retrieval and context construction latency
3. Event-processing throughput

All benchmarks use mocked AWS services and deterministic data for reproducibility.
"""

import json
import os
import sys
import time
import statistics
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Dict, Any
from unittest.mock import Mock, MagicMock

# Add paths
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'infra', 'lambda', 'ai_processing'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'infra', 'lambda', 'shared', 'python'))

# Set AWS region
os.environ.setdefault('AWS_REGION', 'us-east-1')
os.environ.setdefault('AWS_DEFAULT_REGION', 'us-east-1')

# Import after path setup
import handler as ai_handler
from flowsync_common.helpers import search_context_rag, search_context_rag_with_graph, call_titan_embedding


# ─── Benchmark Configuration ───

BENCHMARK_REPETITIONS = 10
RANDOM_SEED = 42


# ─── Helper Functions ───

def create_mock_dynamodb():
    """Create a mock DynamoDB resource with configurable tables."""
    mock_dynamodb = MagicMock()
    mock_context_table = Mock()
    mock_graph_entities_table = Mock()
    mock_graph_relationships_table = Mock()
    mock_cache_table = Mock()
    mock_projects_table = Mock()

    def mock_table(name):
        if name == "flowsync-context":
            return mock_context_table
        elif name == "flowsync-graph-entities":
            return mock_graph_entities_table
        elif name == "flowsync-graph-relationships":
            return mock_graph_relationships_table
        elif name == "flowsync-cache":
            return mock_cache_table
        elif name == "flowsync-projects":
            return mock_projects_table
        return Mock()

    mock_dynamodb.Table.side_effect = mock_table
    return {
        'dynamodb': mock_dynamodb,
        'context_table': mock_context_table,
        'graph_entities_table': mock_graph_entities_table,
        'graph_relationships_table': mock_graph_relationships_table,
        'cache_table': mock_cache_table,
        'projects_table': mock_projects_table,
    }


def create_mock_bedrock():
    """Create a mock Bedrock client."""
    mock_bedrock = MagicMock()
    mock_bedrock.converse.return_value = {
        'output': {'message': {'content': [{'text': json.dumps({
            'answer': 'Benchmark answer',
            'answerGrounded': True,
            'citedSources': ['src1', 'src2', 'src3']
        })}]}},
        'usage': {'inputTokens': 500, 'outputTokens': 100}
    }
    mock_bedrock.invoke_model.return_value = {
        'body': Mock(read=Mock(return_value=json.dumps({'embedding': [0.1] * 1536}).encode()))
    }
    return mock_bedrock


def create_test_context_records(count: int, project_id: str = 'bench-project') -> List[Dict]:
    """Create test context records for benchmarking."""
    records = []
    base_time = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    for i in range(count):
        timestamp = base_time.isoformat().replace('+00:00', 'Z')
        records.append({
            'eventId': f'evt-{i:06d}',
            'projectId': project_id,
            'branch': 'main',
            'branchExtractedAt': f'main#{timestamp}',
            'commitHash': f'commit{i:06d}',
            'status': 'complete',
            'feature': f'Feature {i}',
            'decision': f'Decision for feature {i}',
            'tasks': [f'task{i}-1', f'task{i}-2'],
            'stage': 'Feature Development',
            'risk': f'Risk {i}',
            'confidence': Decimal('0.85'),
            'entities': [f'entity{i}-1', f'entity{i}-2'],
            'author': f'author{i % 5}',
            'agentReasoning': f'Reasoning for feature {i}',
            'modelVersion': 'us.amazon.nova-pro-v1:0',
            'embedding': [0.1] * 1536,
            'extractedAt': timestamp,
            'processingDuration': 1000,
        })
    return records


def create_test_graph_entities(count: int, project_id: str = 'bench-project') -> List[Dict]:
    """Create test graph entities for benchmarking."""
    entities = []
    entity_types = ['repository', 'commit', 'pull_request', 'file', 'engineering_decision']
    base_time = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    for i in range(count):
        etype = entity_types[i % len(entity_types)]
        timestamp = base_time.isoformat().replace('+00:00', 'Z')
        entities.append({
            'entityId': f'{etype}:repo:bench:{i:06d}',
            'entityType': etype,
            'projectId': project_id,
            'repositoryId': 'repo:github:bench:repo',
            'createdAt': timestamp,
            'updatedAt': timestamp,
            'schemaVersion': '1',
            'title': f'{etype.title()} {i}',
            'summary': f'Summary for {etype} {i}',
        })
    return entities


def create_test_relationships(entity_ids: List[str], count: int, project_id: str = 'bench-project') -> List[Dict]:
    """Create test graph relationships for benchmarking."""
    rel_types = ['contains', 'authored_in', 'modifies', 'has_parent', 'includes_commit', 'targets_file', 'has_decision', 'relates_to', 'derived_from']
    relationships = []
    base_time = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    for i in range(count):
        source = entity_ids[i % len(entity_ids)]
        target = entity_ids[(i + 1) % len(entity_ids)]
        rel_type = rel_types[i % len(rel_types)]
        timestamp = base_time.isoformat().replace('+00:00', 'Z')
        relationships.append({
            'relationshipId': f'rel:{rel_type}:{source}:{target}',
            'relationshipType': rel_type,
            'sourceEntityId': source,
            'sourceEntityType': 'commit',
            'targetEntityId': target,
            'targetEntityType': 'file',
            'projectId': project_id,
            'repositoryId': 'repo:github:bench:repo',
            'provenance': 'explicit',
            'evidence': f'evt-{i:06d}',
            'createdAt': timestamp,
            'updatedAt': timestamp,
            'schemaVersion': '1',
            'confidence': Decimal('0.9'),
        })
    return relationships


def run_benchmark(name: str, func, repetitions: int = BENCHMARK_REPETITIONS) -> Dict[str, Any]:
    """Run a benchmark function multiple times and return statistics."""
    print(f"\n{'='*60}")
    print(f"Benchmark: {name}")
    print(f"Repetitions: {repetitions}")
    print(f"{'='*60}")
    
    times = []
    for i in range(repetitions):
        start = time.perf_counter()
        result = func()
        end = time.perf_counter()
        elapsed_ms = (end - start) * 1000
        times.append(elapsed_ms)
        print(f"  Run {i+1}/{repetitions}: {elapsed_ms:.2f} ms")
    
    stats = {
        'name': name,
        'repetitions': repetitions,
        'times_ms': times,
        'min_ms': min(times),
        'max_ms': max(times),
        'mean_ms': statistics.mean(times),
        'median_ms': statistics.median(times),
        'stdev_ms': statistics.stdev(times) if len(times) > 1 else 0,
    }
    
    print(f"  Min:   {stats['min_ms']:.2f} ms")
    print(f"  Max:   {stats['max_ms']:.2f} ms")
    print(f"  Mean:  {stats['mean_ms']:.2f} ms")
    print(f"  Median:{stats['median_ms']:.2f} ms")
    print(f"  StDev: {stats['stdev_ms']:.2f} ms")
    
    return stats


# ─── Graph Traversal Benchmarks ───

def benchmark_graph_traversal_small():
    """Benchmark graph traversal with small graph (10 entities, 15 relationships)."""
    tables = create_mock_dynamodb()
    bedrock = create_mock_bedrock()
    
    entities = create_test_graph_entities(10)
    entity_ids = [e['entityId'] for e in entities]
    relationships = create_test_relationships(entity_ids, 15)
    
    # Configure mocks
    tables['graph_entities_table'].get_item.side_effect = lambda Key: {'Item': next((e for e in entities if e['entityId'] == Key['entityId']), None)}
    tables['graph_relationships_table'].query.return_value = {'Items': relationships, 'Count': len(relationships)}
    
    # Patch the handler's dependencies
    ai_handler.dynamodb = tables['dynamodb']
    ai_handler.bedrock_client = bedrock
    ai_handler.cloudwatch = MagicMock()
    
    from handler import query_knowledge_graph
    
    def run_query():
        return query_knowledge_graph({
            'projectId': 'bench-project',
            'entityId': entity_ids[0],
            'direction': 'both',
            'maxDepth': 2,
            'maxResults': 20,
        })
    
    return run_query()


def benchmark_graph_traversal_medium():
    """Benchmark graph traversal with medium graph (100 entities, 200 relationships)."""
    tables = create_mock_dynamodb()
    bedrock = create_mock_bedrock()
    
    entities = create_test_graph_entities(100)
    entity_ids = [e['entityId'] for e in entities]
    relationships = create_test_relationships(entity_ids, 200)
    
    tables['graph_entities_table'].get_item.side_effect = lambda Key: {'Item': next((e for e in entities if e['entityId'] == Key['entityId']), None)}
    tables['graph_relationships_table'].query.return_value = {'Items': relationships, 'Count': len(relationships)}
    
    ai_handler.dynamodb = tables['dynamodb']
    ai_handler.bedrock_client = bedrock
    ai_handler.cloudwatch = MagicMock()
    
    from handler import query_knowledge_graph
    
    def run_query():
        return query_knowledge_graph({
            'projectId': 'bench-project',
            'entityId': entity_ids[0],
            'direction': 'both',
            'maxDepth': 3,
            'maxResults': 50,
        })
    
    return run_query()


def benchmark_graph_traversal_large():
    """Benchmark graph traversal with large graph (1000 entities, 3000 relationships)."""
    tables = create_mock_dynamodb()
    bedrock = create_mock_bedrock()
    
    entities = create_test_graph_entities(1000)
    entity_ids = [e['entityId'] for e in entities]
    relationships = create_test_relationships(entity_ids, 3000)
    
    tables['graph_entities_table'].get_item.side_effect = lambda Key: {'Item': next((e for e in entities if e['entityId'] == Key['entityId']), None)}
    tables['graph_relationships_table'].query.return_value = {'Items': relationships, 'Count': len(relationships)}
    
    ai_handler.dynamodb = tables['dynamodb']
    ai_handler.bedrock_client = bedrock
    ai_handler.cloudwatch = MagicMock()
    
    from handler import query_knowledge_graph
    
    def run_query():
        return query_knowledge_graph({
            'projectId': 'bench-project',
            'entityId': entity_ids[0],
            'direction': 'both',
            'maxDepth': 3,
            'maxResults': 50,
        })
    
    return run_query()


def benchmark_graph_query_by_type():
    """Benchmark querying entities by type within project."""
    tables = create_mock_dynamodb()
    bedrock = create_mock_bedrock()
    
    entities = create_test_graph_entities(500)
    
    tables['graph_entities_table'].query.return_value = {'Items': entities, 'Count': len(entities)}
    
    ai_handler.dynamodb = tables['dynamodb']
    ai_handler.bedrock_client = bedrock
    ai_handler.cloudwatch = MagicMock()
    
    from handler import query_knowledge_graph
    
    def run_query():
        return query_knowledge_graph({
            'projectId': 'bench-project',
            'entityType': 'commit',
            'maxResults': 50,
        })
    
    return run_query()


# ─── RAG Retrieval and Context Construction Benchmarks ───

def benchmark_rag_retrieval_small():
    """Benchmark RAG retrieval with small context set (10 records)."""
    tables = create_mock_dynamodb()
    bedrock = create_mock_bedrock()
    
    records = create_test_context_records(10)
    tables['context_table'].query.return_value = {'Items': records, 'Count': len(records)}
    tables['cache_table'].get_item.return_value = {'Item': None}
    
    ai_handler.dynamodb = tables['dynamodb']
    ai_handler.bedrock_client = bedrock
    ai_handler.cloudwatch = MagicMock()
    
    def run_query():
        return search_context_rag(
            project_id='bench-project',
            query='What decisions were made about authentication?',
            branch='main',
            bedrock_client=bedrock,
            dynamodb=tables['dynamodb'],
            context_table_name='flowsync-context',
            cache_table_name='flowsync-cache',
        )
    
    return run_query()


def benchmark_rag_retrieval_medium():
    """Benchmark RAG retrieval with medium context set (100 records)."""
    tables = create_mock_dynamodb()
    bedrock = create_mock_bedrock()
    
    records = create_test_context_records(100)
    tables['context_table'].query.return_value = {'Items': records, 'Count': len(records)}
    tables['cache_table'].get_item.return_value = {'Item': None}
    
    ai_handler.dynamodb = tables['dynamodb']
    ai_handler.bedrock_client = bedrock
    ai_handler.cloudwatch = MagicMock()
    
    def run_query():
        return search_context_rag(
            project_id='bench-project',
            query='What decisions were made about authentication and authorization?',
            branch='main',
            bedrock_client=bedrock,
            dynamodb=tables['dynamodb'],
            context_table_name='flowsync-context',
            cache_table_name='flowsync-cache',
        )
    
    return run_query()


def benchmark_rag_retrieval_large():
    """Benchmark RAG retrieval with large context set (500 records, but limited by query)."""
    tables = create_mock_dynamodb()
    bedrock = create_mock_bedrock()
    
    records = create_test_context_records(500)
    tables['context_table'].query.return_value = {'Items': records[:50], 'Count': 50}  # Limited by query Limit
    tables['cache_table'].get_item.return_value = {'Item': None}
    
    ai_handler.dynamodb = tables['dynamodb']
    ai_handler.bedrock_client = bedrock
    ai_handler.cloudwatch = MagicMock()
    
    def run_query():
        return search_context_rag(
            project_id='bench-project',
            query='What are the security risks identified in the project?',
            branch='main',
            bedrock_client=bedrock,
            dynamodb=tables['dynamodb'],
            context_table_name='flowsync-context',
            cache_table_name='flowsync-cache',
        )
    
    return run_query()


def benchmark_rag_with_graph():
    """Benchmark RAG retrieval combined with graph context."""
    tables = create_mock_dynamodb()
    bedrock = create_mock_bedrock()
    
    records = create_test_context_records(50)
    tables['context_table'].query.return_value = {'Items': records, 'Count': len(records)}
    tables['cache_table'].get_item.return_value = {'Item': None}
    
    # Graph entities
    entities = create_test_graph_entities(20)
    entity_ids = [e['entityId'] for e in entities]
    relationships = create_test_relationships(entity_ids, 30)
    tables['graph_entities_table'].get_item.side_effect = lambda Key: {'Item': next((e for e in entities if e['entityId'] == Key['entityId']), None)}
    tables['graph_relationships_table'].query.return_value = {'Items': relationships, 'Count': len(relationships)}
    
    ai_handler.dynamodb = tables['dynamodb']
    ai_handler.bedrock_client = bedrock
    ai_handler.cloudwatch = MagicMock()
    
    def run_query():
        return search_context_rag_with_graph(
            project_id='bench-project',
            query='Why was the authentication module refactored?',
            branch='main',
            bedrock_client=bedrock,
            dynamodb=tables['dynamodb'],
            context_table_name='flowsync-context',
            cache_table_name='flowsync-cache',
            use_graph=True,
        )
    
    return run_query()


def benchmark_embedding_generation():
    """Benchmark Titan embedding generation (mocked)."""
    tables = create_mock_dynamodb()
    bedrock = create_mock_bedrock()
    
    ai_handler.dynamodb = tables['dynamodb']
    ai_handler.bedrock_client = bedrock
    ai_handler.cloudwatch = MagicMock()
    
    test_texts = [
        "Short text for embedding",
        "Medium length text for embedding generation " * 10,
        "Long text for embedding generation " * 100,
    ]
    
    def run_query():
        results = []
        for text in test_texts:
            results.append(call_titan_embedding(text, bedrock))
        return results
    
    return run_query()


# ─── Event Processing Throughput Benchmarks ───

def benchmark_event_processing_small_batch():
    """Benchmark processing a small batch of events (5 events)."""
    tables = create_mock_dynamodb()
    bedrock = create_mock_bedrock()
    
    tables['context_table'].put_item.return_value = {}
    tables['context_table'].get_item.return_value = {'Item': None}
    tables['graph_entities_table'].put_item.return_value = {}
    tables['graph_entities_table'].get_item.return_value = {'Item': None}
    tables['graph_relationships_table'].put_item.return_value = {}
    
    # Mock the event processing
    ai_handler.dynamodb = tables['dynamodb']
    ai_handler.bedrock_client = bedrock
    ai_handler.cloudwatch = MagicMock()
    
    # Create mock SQS event with 5 records
    sqs_event = {
        'Records': [
            {
                'messageId': f'msg-{i}',
                'receiptHandle': f'receipt-{i}',
                'body': json.dumps({
                    'eventId': f'evt-{i}',
                    'projectId': 'bench-project',
                    'eventType': 'push',
                    'payload': {'commitHash': f'commit{i}', 'message': f'Commit {i}', 'diff': 'diff', 'author': 'Test'},
                }),
                'attributes': {'SentTimestamp': str(int(time.time() * 1000))},
            }
            for i in range(5)
        ]
    }
    
    def run_query():
        return ai_handler.handler(sqs_event, {})
    
    return run_query()


def benchmark_event_processing_medium_batch():
    """Benchmark processing a medium batch of events (25 events)."""
    tables = create_mock_dynamodb()
    bedrock = create_mock_bedrock()
    
    tables['context_table'].put_item.return_value = {}
    tables['context_table'].get_item.return_value = {'Item': None}
    tables['graph_entities_table'].put_item.return_value = {}
    tables['graph_entities_table'].get_item.return_value = {'Item': None}
    tables['graph_relationships_table'].put_item.return_value = {}
    
    ai_handler.dynamodb = tables['dynamodb']
    ai_handler.bedrock_client = bedrock
    ai_handler.cloudwatch = MagicMock()
    
    sqs_event = {
        'Records': [
            {
                'messageId': f'msg-{i}',
                'receiptHandle': f'receipt-{i}',
                'body': json.dumps({
                    'eventId': f'evt-{i}',
                    'projectId': 'bench-project',
                    'eventType': 'push',
                    'payload': {'commitHash': f'commit{i}', 'message': f'Commit {i}', 'diff': 'diff', 'author': 'Test'},
                }),
                'attributes': {'SentTimestamp': str(int(time.time() * 1000))},
            }
            for i in range(25)
        ]
    }
    
    def run_query():
        return ai_handler.handler(sqs_event, {})
    
    return run_query()


def benchmark_idempotency_check():
    """Benchmark idempotency check performance."""
    tables = create_mock_dynamodb()
    bedrock = create_mock_bedrock()
    
    # Simulate idempotency table
    mock_idempotency_table = Mock()
    mock_idempotency_table.get_item.return_value = {'Item': None}  # Not found = new event
    
    tables['dynamodb'].Table.side_effect = lambda name: mock_idempotency_table if name == 'flowsync-idempotency' else tables['dynamodb'].Table(name)
    
    ai_handler.dynamodb = tables['dynamodb']
    ai_handler.bedrock_client = bedrock
    ai_handler.cloudwatch = MagicMock()
    
    def run_check():
        # Import the idempotency check function
        from handler import check_idempotency
        return check_idempotency('test-delivery-id', tables['dynamodb'], 'flowsync-idempotency')
    
    return run_check()


# ─── Main Benchmark Runner ───

def main():
    print("=" * 60)
    print("CodeAtlas Phase 5 Performance Benchmarks")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print(f"Repetitions per benchmark: {BENCHMARK_REPETITIONS}")
    print(f"Random seed: {RANDOM_SEED}")
    print("=" * 60)
    
    all_results = []
    
    # Graph Traversal Benchmarks
    print("\n\n>>> GRAPH TRAVERSAL BENCHMARKS <<<\n")
    
    for name, func in [
        ("Graph Traversal - Small (10 entities, 15 rels, depth=2, maxResults=20)", benchmark_graph_traversal_small),
        ("Graph Traversal - Medium (100 entities, 200 rels, depth=3, maxResults=50)", benchmark_graph_traversal_medium),
        ("Graph Traversal - Large (1000 entities, 3000 rels, depth=3, maxResults=50)", benchmark_graph_traversal_large),
        ("Graph Query by Type - 500 entities, filter by type", benchmark_graph_query_by_type),
    ]:
        try:
            result = run_benchmark(name, func)
            all_results.append(result)
        except Exception as e:
            print(f"  ERROR: {e}")
            all_results.append({'name': name, 'error': str(e)})
    
    # RAG Retrieval Benchmarks
    print("\n\n>>> RAG RETRIEVAL & CONTEXT CONSTRUCTION BENCHMARKS <<<\n")
    
    for name, func in [
        ("RAG Retrieval - Small (10 context records)", benchmark_rag_retrieval_small),
        ("RAG Retrieval - Medium (100 context records)", benchmark_rag_retrieval_medium),
        ("RAG Retrieval - Large (500 context records, limited to 50)", benchmark_rag_retrieval_large),
        ("RAG with Graph Context (50 records + 20 entities, 30 rels)", benchmark_rag_with_graph),
        ("Embedding Generation (3 texts: short/medium/long)", benchmark_embedding_generation),
    ]:
        try:
            result = run_benchmark(name, func)
            all_results.append(result)
        except Exception as e:
            print(f"  ERROR: {e}")
            all_results.append({'name': name, 'error': str(e)})
    
    # Event Processing Benchmarks
    print("\n\n>>> EVENT PROCESSING THROUGHPUT BENCHMARKS <<<\n")
    
    for name, func in [
        ("Event Processing - Small Batch (5 events)", benchmark_event_processing_small_batch),
        ("Event Processing - Medium Batch (25 events)", benchmark_event_processing_medium_batch),
        ("Idempotency Check (single check)", benchmark_idempotency_check),
    ]:
        try:
            result = run_benchmark(name, func)
            all_results.append(result)
        except Exception as e:
            print(f"  ERROR: {e}")
            all_results.append({'name': name, 'error': str(e)})
    
    # Summary
    print("\n\n" + "=" * 60)
    print("BENCHMARK SUMMARY")
    print("=" * 60)
    print(f"{'Benchmark':<65} {'Median (ms)':>12} {'Mean (ms)':>12} {'Min (ms)':>10} {'Max (ms)':>10}")
    print("-" * 110)
    
    for r in all_results:
        if 'error' not in r:
            print(f"{r['name']:<65} {r['median_ms']:>12.2f} {r['mean_ms']:>12.2f} {r['min_ms']:>10.2f} {r['max_ms']:>10.2f}")
        else:
            print(f"{r['name']:<65} {'ERROR':>12}")
    
    print("\n\nNOTES:")
    print("- All benchmarks use mocked AWS services (DynamoDB, Bedrock)")
    print("- Results are synthetic and do NOT reflect AWS production performance")
    print("- Bedrock model calls are mocked; no actual model inference occurs")
    print("- Graph traversal uses in-memory mock data; no network latency")
    print("- Event processing uses mock SQS events; no actual queue latency")
    print("- Measurements include Python overhead, mock dispatch, and serialization")
    print("- Use these as relative baselines only, not absolute performance targets")
    
    # Save results
    output_file = os.path.join(os.path.dirname(__file__), 'benchmark_results.json')
    with open(output_file, 'w') as f:
        json.dump({
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'repetitions': BENCHMARK_REPETITIONS,
            'seed': RANDOM_SEED,
            'environment': 'local-mocked',
            'results': all_results,
        }, f, indent=2)
    
    print(f"\nResults saved to: {output_file}")


if __name__ == '__main__':
    main()