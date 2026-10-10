# CodeAtlas Operational Runbooks

This document contains operational runbooks for investigating and resolving common issues in the CodeAtlas platform.

## Table of Contents

1. [Investigating Queue Backlog](#1-investigating-queue-backlog)
2. [Investigating Old Messages](#2-investigating-old-messages)
3. [Investigating DLQ Messages](#3-investigating-dlq-messages)
4. [Diagnosing Lambda Failures and Throttling](#4-diagnosing-lambda-failures-and-throttling)
5. [Handling GitHub API Rate Limits](#5-handling-github-api-rate-limits)
6. [Diagnosing Repeated Idempotency Conflicts](#6-diagnosing-repeated-idempotency-conflicts)
7. [Recovering from Stale Processing Records](#7-recovering-from-stale-processing-records)
8. [Investigating Graph Ingestion Failures](#8-investigating-graph-ingestion-failures)
9. [Verifying Configuration Without Deploying](#9-verifying-configuration-without-deploying)
10. [Preparing for Safe Recovery or Replay](#10-preparing-for-safe-recovery-or-replay)

---

## 1. Investigating Queue Backlog

### Symptoms
- CloudWatch alarm `flowsync-high-queue-backlog` firing
- `ApproximateNumberOfMessagesVisible` > 100 for 10+ minutes
- Increased processing latency

### Likely Causes
1. **AI Processing Lambda throttling** - Bedrock API rate limits
2. **Lambda concurrency limit** - Account or reserved concurrency limits
3. **Processing time per event too long** - Complex diffs, large payloads
4. **Upstream ingestion spike** - Burst of GitHub webhooks or VS Code events
5. **Downstream service degradation** - DynamoDB, Bedrock, S3 issues

### Diagnostic Steps
```bash
# Check Lambda metrics
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Invocations \
  --dimensions Name=FunctionName,Value=flowsync-ai-processing \
  --start-time $(date -u -d '30 minutes ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300 --statistics Sum

# Check for throttles
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Throttles \
  --dimensions Name=FunctionName,Value=flowsync-ai-processing \
  --start-time $(date -u -d '30 minutes ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300 --statistics Sum

# Check queue age
aws cloudwatch get-metric-statistics \
  --namespace AWS/SQS \
  --metric-name ApproximateAgeOfOldestMessage \
  --dimensions Name=QueueName,Value=flowsync-processing \
  --start-time $(date -u -d '30 minutes ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300 --statistics Maximum
```

### Recovery Decision
- **If throttling**: Request Bedrock quota increase or implement client-side rate limiting
- **If concurrency limited**: Increase Lambda reserved concurrency
- **If processing time high**: Optimize Bedrock prompts, enable caching
- **If ingestion spike**: Temporary - should self-resolve; consider scaling ingestion Lambda

### Risks of Replay
- Replaying messages without fixing root cause will just rebuild the backlog
- Duplicate processing is handled by idempotency, but wastes compute

---

## 2. Investigating Old Messages

### Symptoms
- CloudWatch alarm `flowsync-high-queue-age` firing
- `ApproximateAgeOfOldestMessage` > 90,000ms (90 seconds)
- Messages approaching visibility timeout

### Likely Causes
1. **Single slow message blocking batch** - One event taking >90s
2. **Lambda timeout** - Processing exceeds 60s timeout
3. **Batch processing stuck** - All messages in batch are slow

### Diagnostic Steps
```bash
# Check Lambda duration
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Duration \
  --dimensions Name=FunctionName,Value=flowsync-ai-processing \
  --start-time $(date -u -d '30 minutes ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300 --statistics Average,Maximum

# Check for timeouts in logs
aws logs filter-log-events \
  --log-group-name /aws/lambda/flowsync-ai-processing \
  --start-time $(date -d '30 minutes ago' +%s)000 \
  --filter-pattern "Task timed out"
```

### Recovery Decision
- **If single slow message**: Let it timeout, SQS will retry with new batch
- **If systematic timeout**: Increase Lambda timeout (max 15min) or optimize processing
- **Never manually delete messages** from queue - let DLQ handle permanent failures

---

## 3. Investigating DLQ Messages

### Symptoms
- CloudWatch alarm `flowsync-dlq-has-messages` firing
- Messages in `flowsync-dlq` queue

### ⚠️ IMPORTANT: Do NOT blindly redrive DLQ messages

### Investigation Procedure
1. **Inspect messages without deleting**:
```bash
# Peek at DLQ messages (max 10)
aws sqs receive-message \
  --queue-url https://sqs.us-east-1.amazonaws.com/<account>/flowsync-dlq \
  --max-number-of-messages 10 \
  --wait-time-seconds 20 \
  --visibility-timeout 60
```

2. **Analyze each message**:
   - Check `error` field in idempotency record
   - Look at CloudWatch logs for the correlationId
   - Classify failure: transient vs permanent

3. **Classify failure type**:
   - **Transient** (safe to redrive): Bedrock throttling, network timeout, DynamoDB throughput
   - **Permanent** (fix required): Schema validation error, malformed payload, missing repository mapping
   - **Conditional** (investigate): Authorization failure, resource not found

### Recovery Decision Matrix

| Failure Type | Action | Risk |
|-------------|--------|------|
| Bedrock throttling | Redrive after quota increase | Low - idempotent |
| Network timeout | Redrive | Low - idempotent |
| Schema validation | Fix payload/schema, then redrive | Medium - may need code change |
| Missing repo mapping | Add mapping to `flowsync-project-repo-mapping`, then redrive | Low |
| Auth failure | Check token/project config | Medium |
| Resource not found | Verify resource exists | Medium |

### Redrive Procedure (after root cause fix)
```bash
# Redrive messages back to processing queue
aws sqs start-message-move-task \
  --source-queue-url https://sqs.us-east-1.amazonaws.com/<account>/flowsync-dlq \
  --destination-queue-url https://sqs.us-east-1.amazonaws.com/<account>/flowsync-processing
```

### Verification After Recovery
- Monitor queue depth and processing latency
- Check idempotency records for `COMPLETED` status
- Verify no duplicate processing occurred

---

## 4. Diagnosing Lambda Failures and Throttling

### Symptoms
- CloudWatch alarm `flowsync-ai-processing-errors` or `flowsync-ai-processing-throttles` firing
- Increased error rate in Lambda metrics

### Diagnostic Steps
```bash
# Get error details from logs
aws logs filter-log-events \
  --log-group-name /aws/lambda/flowsync-ai-processing \
  --start-time $(date -d '30 minutes ago' +%s)000 \
  --filter-pattern "ERROR"

# Check specific error patterns
aws logs filter-log-events \
  --log-group-name /aws/lambda/flowsync-ai-processing \
  --start-time $(date -d '30 minutes ago' +%s)000 \
  --filter-pattern "ThrottlingException"
```

### Common Error Patterns
| Error | Cause | Resolution |
|-------|-------|------------|
| `ThrottlingException` | Bedrock rate limit | Request quota increase, add exponential backoff |
| `ModelTimeoutException` | Bedrock model timeout | Reduce prompt complexity, use faster model |
| `ConditionalCheckFailedException` | Idempotency conflict | Normal - duplicate delivery |
| `ValidationException` | Invalid input | Check payload structure |
| `AccessDeniedException` | IAM permissions | Review Lambda role policies |

---

## 5. Handling GitHub API Rate Limits

### Symptoms
- GitHub webhook deliveries failing
- `403 Forbidden` or `429 Too Many Requests` from GitHub API
- Ingestion Lambda errors when fetching diffs/PR details

### Diagnostic Steps
```bash
# Check GitHub API rate limit status (requires GH token)
curl -H "Authorization: Bearer $GITHUB_TOKEN" \
  https://api.github.com/rate_limit
```

### Recovery
- **Immediate**: Wait for rate limit reset (check `X-RateLimit-Reset` header)
- **Short-term**: Implement GitHub App with higher rate limits
- **Long-term**: Cache GitHub API responses, batch requests

---

## 6. Diagnosing Repeated Idempotency Conflicts

### Symptoms
- High rate of `ConditionalCheckFailedException` in idempotency table
- Many duplicate events being processed

### Likely Causes
1. **GitHub webhook redelivery** - GitHub retries failed deliveries
2. **SQS batch retry** - Partial batch failure causes full batch retry
3. **Client-side duplicate submission** - VS Code extension sending same event twice

### Diagnostic Steps
```bash
# Check idempotency table for conflict patterns
aws dynamodb query \
  --table-name flowsync-idempotency \
  --key-condition-expression "idempotencyKey = :key" \
  --expression-attribute-values '{":key": {"S": "github-delivery-123"}}'
```

### Resolution
- Conflicts are expected and handled - no action needed unless rate is abnormally high
- Check for client-side bugs causing duplicate submissions
- Verify GitHub webhook secret is correct (invalid signatures cause retries)

---

## 7. Recovering from Stale Processing Records

### Symptoms
- Event stuck in `PROCESSING` state for >10 minutes
- Subsequent deliveries of same event treated as duplicates

### Automatic Recovery
The system automatically detects stale `PROCESSING` records (>10 minutes) and allows reprocessing. No manual intervention needed.

### Manual Recovery (if automatic fails)
```bash
# Find stale records
aws dynamodb scan \
  --table-name flowsync-idempotency \
  --filter-expression "#status = :status AND startedAt < :threshold" \
  --expression-attribute-names '{"#status": "status"}' \
  --expression-attribute-values '{":status": {"S": "PROCESSING"}, ":threshold": {"S": "2024-01-01T00:00:00Z"}}'

# Reset stale record (allow reprocessing)
aws dynamodb update-item \
  --table-name flowsync-idempotency \
  --key '{"idempotencyKey": {"S": "github-delivery-123"}}' \
  --update-expression "SET #status = :status" \
  --expression-attribute-names '{"#status": "status"}' \
  --expression-attribute-values '{":status": {"S": "PENDING"}}'
```

---

## 8. Investigating Graph Ingestion Failures

### Symptoms
- Graph entities/relationships not created for events
- MCP graph queries return incomplete results
- `graph ingestion failed` in logs

### Diagnostic Steps
```bash
# Check CloudWatch logs for graph errors
aws logs filter-log-events \
  --log-group-name /aws/lambda/flowsync-ai-processing \
  --start-time $(date -d '30 minutes ago' +%s)000 \
  --filter-pattern "graph"

# Verify graph tables have data
aws dynamodb scan --table-name flowsync-graph-entities --max-items 5
aws dynamodb scan --table-name flowsync-graph-relationships --max-items 5
```

### Common Issues
| Issue | Cause | Resolution |
|-------|-------|------------|
| Missing repository | GitHub event lacks repo info | Add mapping to `flowsync-project-repo-mapping` |
| Duplicate entities | Race condition in upsert | Conditional writes handle this |
| Failed relationships | Entity not found | Check entity creation order |

---

## 9. Verifying Configuration Without Deploying

### Pre-Deployment Checklist
```bash
# 1. Synthesize CDK stack
cd infra && npx cdk synth --no-staging > template.yaml

# 2. Validate CloudFormation template
aws cloudformation validate-template --template-body file://template.yaml

# 3. Check for drift (if already deployed)
aws cloudformation detect-stack-drift --stack-name FlowSyncStack

# 4. Run all tests
cd ../packages/flowsync-shared && npm test
cd ../infra/lambda/ingestion && npm test
cd ../infra/lambda/ai_processing && py -m pytest test/
cd ../infra && npm test
```

### Configuration Validation
- All required environment variables documented in `.env.example`
- Secrets not committed (check `.gitignore`)
- IAM policies follow least privilege
- Resource naming consistent

---

## 10. Preparing for Safe Recovery or Replay

### Before Any Recovery Action
1. **Document current state**: Queue depths, DLQ count, Lambda error rates
2. **Identify root cause**: Don't replay without fixing the underlying issue
3. **Check idempotency**: Verify `COMPLETED` records won't cause duplicate effects
4. **Plan rollback**: Know how to revert if recovery makes things worse

### Safe Replay Procedure
```bash
# 1. Pause ingestion (disable GitHub webhook or API Gateway)
# 2. Fix root cause (code/config change)
# 3. Deploy fix
# 4. Redrive DLQ if appropriate
aws sqs start-message-move-task \
  --source-queue-url <dlq-url> \
  --destination-queue-url <processing-queue-url>

# 5. Monitor for 15 minutes
# 6. Re-enable ingestion
```

### Post-Recovery Verification
- [ ] Queue depth returning to normal
- [ ] No new DLQ messages
- [ ] Lambda error rate < 1%
- [ ] Graph ingestion completing successfully
- [ ] MCP queries returning expected results
- [ ] Dashboard showing recent activity

---

## Contact and Escalation

- **Primary**: Repository owner (GitHub issues)
- **AWS Support**: For Bedrock quota, Lambda limits, SQS issues
- **GitHub Support**: For webhook delivery issues

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-10-10 | Initial runbooks for Phase 5 |