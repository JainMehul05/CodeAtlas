import * as cdk from 'aws-cdk-lib/core';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as lambdaEventSources from 'aws-cdk-lib/aws-lambda-event-sources';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cloudwatchActions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as snsSubscriptions from 'aws-cdk-lib/aws-sns-subscriptions';
import { Construct } from 'constructs';
import * as path from 'path';

export class InfraStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // ─────────────────────────────────────────────
    // DYNAMODB TABLES
    // ─────────────────────────────────────────────

    const projectsTable = new dynamodb.Table(this, 'FlowSyncProjects', {
      tableName: 'flowsync-projects',
      partitionKey: { name: 'projectId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY, // easy cleanup after hackathon
    });

    const eventsTable = new dynamodb.Table(this, 'FlowSyncEvents', {
      tableName: 'flowsync-events',
      partitionKey: { name: 'projectId', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'timestampEventId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    // GSI: direct lookup by eventId
    eventsTable.addGlobalSecondaryIndex({
      indexName: 'EventIdIndex',
      partitionKey: { name: 'eventId', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });
    // GSI: all events for a branch (app writes branchTimestamp = "branch#timestamp")
    eventsTable.addGlobalSecondaryIndex({
      indexName: 'BranchIndex',
      partitionKey: { name: 'projectId', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'branchTimestamp', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    const contextTable = new dynamodb.Table(this, 'FlowSyncContext', {
      tableName: 'flowsync-context',
      partitionKey: { name: 'eventId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    // GSI: all context for a project (sorted by time)
    contextTable.addGlobalSecondaryIndex({
      indexName: 'ProjectContextIndex',
      partitionKey: { name: 'projectId', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'extractedAt', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });
    // GSI: all context for a branch (app writes branchExtractedAt = "branch#extractedAt")
    contextTable.addGlobalSecondaryIndex({
      indexName: 'BranchContextIndex',
      partitionKey: { name: 'projectId', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'branchExtractedAt', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    const auditTable = new dynamodb.Table(this, 'FlowSyncAudit', {
      tableName: 'flowsync-audit',
      partitionKey: { name: 'entityId', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'timestamp', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const chatSessionsTable = new dynamodb.Table(this, 'FlowSyncChatSessions', {
      tableName: 'flowsync-chat-sessions',
      partitionKey: { name: 'sessionId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      timeToLiveAttribute: 'ttl', // auto-cleanup old sessions
    });

    // RAG response cache — avoids re-running Bedrock for identical queries (1-hour TTL)
    const cacheTable = new dynamodb.Table(this, 'FlowSyncCache', {
      tableName: 'flowsync-cache',
      partitionKey: { name: 'cacheKey', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      timeToLiveAttribute: 'expiresAt', // auto-expire cache entries after 1 hour
    });

    // ─────────────────────────────────────────────
    // IDEMPOTENCY TABLE (for duplicate event detection)
    // ─────────────────────────────────────────────
    const idempotencyTable = new dynamodb.Table(this, 'FlowSyncIdempotency', {
      tableName: 'flowsync-idempotency',
      partitionKey: { name: 'idempotencyKey', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      timeToLiveAttribute: 'expiresAt', // auto-expire after 7 days
    });

    // ─────────────────────────────────────────────
    // PROJECT-REPOSITORY MAPPING TABLE (for secure GitHub repo to project mapping)
    // ─────────────────────────────────────────────
    const projectRepoMappingTable = new dynamodb.Table(this, 'FlowSyncProjectRepoMapping', {
      tableName: 'flowsync-project-repo-mapping',
      partitionKey: { name: 'repositoryId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    // GSI: Query mappings by project
    projectRepoMappingTable.addGlobalSecondaryIndex({
      indexName: 'ProjectMappingIndex',
      partitionKey: { name: 'projectId', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'repositoryId', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // ─────────────────────────────────────────────
    // GRAPH TABLES (Phase 3 - Engineering Knowledge Graph)
    // ─────────────────────────────────────────────
    
    // Graph Entities table - stores all graph entities (repositories, commits, PRs, files, decisions)
    const graphEntitiesTable = new dynamodb.Table(this, 'FlowSyncGraphEntities', {
      tableName: 'flowsync-graph-entities',
      partitionKey: { name: 'entityId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    // GSI: Query entities by project and type
    graphEntitiesTable.addGlobalSecondaryIndex({
      indexName: 'ProjectEntityIndex',
      partitionKey: { name: 'projectId', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'entityType#createdAt', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });
    // GSI: Query entities by repository
    graphEntitiesTable.addGlobalSecondaryIndex({
      indexName: 'RepositoryEntityIndex',
      partitionKey: { name: 'repositoryId', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'entityType#createdAt', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });
    // GSI: Query entities by type (for cross-project analytics)
    graphEntitiesTable.addGlobalSecondaryIndex({
      indexName: 'EntityTypeIndex',
      partitionKey: { name: 'entityType', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'createdAt', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // Graph Relationships table - stores all relationships between entities
    const graphRelationshipsTable = new dynamodb.Table(this, 'FlowSyncGraphRelationships', {
      tableName: 'flowsync-graph-relationships',
      partitionKey: { name: 'relationshipId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    // GSI: Query outgoing relationships from an entity
    graphRelationshipsTable.addGlobalSecondaryIndex({
      indexName: 'SourceEntityIndex',
      partitionKey: { name: 'sourceEntityId', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'relationshipType#createdAt', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });
    // GSI: Query incoming relationships to an entity
    graphRelationshipsTable.addGlobalSecondaryIndex({
      indexName: 'TargetEntityIndex',
      partitionKey: { name: 'targetEntityId', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'relationshipType#createdAt', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });
    // GSI: Query relationships by project
    graphRelationshipsTable.addGlobalSecondaryIndex({
      indexName: 'ProjectRelationshipIndex',
      partitionKey: { name: 'projectId', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'relationshipType#createdAt', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });
    // GSI: Query relationships by type
    graphRelationshipsTable.addGlobalSecondaryIndex({
      indexName: 'RelationshipTypeIndex',
      partitionKey: { name: 'relationshipType', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'createdAt', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    const allTables = [projectsTable, eventsTable, contextTable, auditTable, chatSessionsTable, cacheTable, idempotencyTable, graphEntitiesTable, graphRelationshipsTable, projectRepoMappingTable];

    // ─────────────────────────────────────────────
    // S3 BUCKETS
    // ─────────────────────────────────────────────

    const rawEventsBucket = new s3.Bucket(this, 'FlowSyncRawEvents', {
      bucketName: `flowsync-raw-events-${this.account}`,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    // ─────────────────────────────────────────────
    // SQS QUEUES (for reliable event processing)
    // ─────────────────────────────────────────────

    // Dead Letter Queue for failed event processing
    const dlq = new sqs.Queue(this, 'FlowSyncDLQ', {
      queueName: 'flowsync-dlq',
      retentionPeriod: cdk.Duration.days(14), // Keep failed messages for 14 days for inspection
      encryption: sqs.QueueEncryption.SQS_MANAGED,
    });

    // Main processing queue with DLQ
    const processingQueue = new sqs.Queue(this, 'FlowSyncProcessingQueue', {
      queueName: 'flowsync-processing',
      visibilityTimeout: cdk.Duration.seconds(90), // 90s > Lambda timeout (60s) + buffer
      retentionPeriod: cdk.Duration.days(4), // Keep messages for 4 days
      receiveMessageWaitTime: cdk.Duration.seconds(20), // Long polling
      deadLetterQueue: {
        queue: dlq,
        maxReceiveCount: 3, // Move to DLQ after 3 failed attempts
      },
      encryption: sqs.QueueEncryption.SQS_MANAGED,
    });

    // ─────────────────────────────────────────────
    // IAM: BEDROCK POLICY (for AI Processing Lambda)
    // ─────────────────────────────────────────────

    const bedrockPolicy = new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['bedrock:InvokeModel', 'bedrock:Converse'],
      resources: [
        // Nova Pro cross-region inference profile (account-scoped)
        `arn:aws:bedrock:us-east-1:357229249502:inference-profile/us.amazon.nova-pro-v1:0`,
        // Underlying Nova Pro foundation model (required for cross-region routing)
        `arn:aws:bedrock:*::foundation-model/amazon.nova-pro-v1:0`,
        // Nova Lite for chat (cost-effective conversational AI)
        `arn:aws:bedrock:us-east-1:357229249502:inference-profile/us.amazon.nova-lite-v1:0`,
        `arn:aws:bedrock:*::foundation-model/amazon.nova-lite-v1:0`,
        // Titan embeddings v2 (currently used by all Lambdas)
        `arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0`,
        // Titan embeddings v1 (legacy support for old records)
        `arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v1`,
      ],
    });

    // ─────────────────────────────────────────────
    // LAMBDA LAYER (shared code for MCP and Query Lambdas)
    // ─────────────────────────────────────────────

    const sharedLayer = new lambda.LayerVersion(this, 'FlowSyncSharedLayer', {
      layerVersionName: 'flowsync-shared-layer',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/shared')),
      compatibleRuntimes: [lambda.Runtime.PYTHON_3_12],
      description: 'Shared utilities for MCP and Query Lambda functions',
    });

    // ─────────────────────────────────────────────
    // LAMBDA FUNCTIONS
    // ─────────────────────────────────────────────

    const ingestionFn = new lambda.Function(this, 'IngestionFn', {
      functionName: 'flowsync-ingestion',
      runtime: lambda.Runtime.NODEJS_20_X,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/ingestion/dist')),
      timeout: cdk.Duration.seconds(10),
      memorySize: 256,
      environment: {
        PROJECTS_TABLE: projectsTable.tableName,
        EVENTS_TABLE: eventsTable.tableName,
        CONTEXT_TABLE: contextTable.tableName,
        AUDIT_TABLE: auditTable.tableName,
        RAW_EVENTS_BUCKET: rawEventsBucket.bucketName,
        PROCESSING_QUEUE_URL: processingQueue.queueUrl,
        PROJECT_REPO_MAPPING_TABLE: projectRepoMappingTable.tableName,
      },
    });

    const aiProcessingFn = new lambda.Function(this, 'AiProcessingFn', {
      functionName: 'flowsync-ai-processing',
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/ai_processing')),
      timeout: cdk.Duration.seconds(60),
      memorySize: 512,
      environment: {
        PROJECTS_TABLE: projectsTable.tableName,
        EVENTS_TABLE: eventsTable.tableName,
        CONTEXT_TABLE: contextTable.tableName,
        AUDIT_TABLE: auditTable.tableName,
        IDEMPOTENCY_TABLE: idempotencyTable.tableName,
        GRAPH_ENTITIES_TABLE: graphEntitiesTable.tableName,
        GRAPH_RELATIONSHIPS_TABLE: graphRelationshipsTable.tableName,
        FALLBACK_MODEL_ID: 'us.amazon.nova-lite-v1:0',
      },
    });
    aiProcessingFn.addToRolePolicy(bedrockPolicy);

    // Add SQS event source mapping for AI Processing Lambda
    aiProcessingFn.addEventSource(new lambdaEventSources.SqsEventSource(processingQueue, {
      batchSize: 5, // Process up to 5 messages at a time
      maxBatchingWindow: cdk.Duration.seconds(30), // Wait up to 30s to fill batch
      reportBatchItemFailures: true, // Allow partial batch failure reporting
    }));

    const mcpFn = new lambda.Function(this, 'McpFn', {
      functionName: 'flowsync-mcp',
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/mcp')),
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      layers: [sharedLayer],
      environment: {
        PROJECTS_TABLE: projectsTable.tableName,
        CONTEXT_TABLE: contextTable.tableName,
        AUDIT_TABLE: auditTable.tableName,
        FALLBACK_MODEL_ID: 'us.amazon.nova-lite-v1:0',
        CACHE_TABLE: cacheTable.tableName,
      },
    });
    mcpFn.addToRolePolicy(bedrockPolicy); // needed for search_context answer generation

    const queryFn = new lambda.Function(this, 'QueryFn', {
      functionName: 'flowsync-query',
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/query')),
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      layers: [sharedLayer],
      environment: {
        PROJECTS_TABLE: projectsTable.tableName,
        CONTEXT_TABLE: contextTable.tableName,
        FALLBACK_MODEL_ID: 'us.amazon.nova-lite-v1:0',
        CACHE_TABLE: cacheTable.tableName,
      },
    });
    queryFn.addToRolePolicy(bedrockPolicy);

    const chatFn = new lambda.Function(this, 'ChatFn', {
      functionName: 'flowsync-chat',
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'handler.lambda_handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/chat')),
      timeout: cdk.Duration.seconds(30),
      memorySize: 512,
      layers: [sharedLayer],
      environment: {
        PROJECT_TABLE_NAME: projectsTable.tableName,
        CONTEXT_TABLE_NAME: contextTable.tableName,
        SESSIONS_TABLE_NAME: chatSessionsTable.tableName,
        FALLBACK_MODEL_ID: 'us.amazon.nova-lite-v1:0',
        CACHE_TABLE_NAME: cacheTable.tableName,
      },
    });
    chatFn.addToRolePolicy(bedrockPolicy);

    // Grant DynamoDB permissions
    allTables.forEach(table => {
      table.grantReadWriteData(ingestionFn);
      table.grantReadWriteData(aiProcessingFn);
      table.grantReadWriteData(mcpFn);
      table.grantReadWriteData(queryFn);
      table.grantReadWriteData(chatFn);
    });

    // Grant S3 permissions
    rawEventsBucket.grantPut(ingestionFn);

    // Grant Ingestion Lambda permission to send messages to processing queue
    processingQueue.grantSendMessages(ingestionFn);

    // Grant AI Processing Lambda permissions for idempotency table
    idempotencyTable.grantReadWriteData(aiProcessingFn);

    // ─────────────────────────────────────────────
    // API GATEWAY (REST API)
    // ─────────────────────────────────────────────

    const api = new apigateway.RestApi(this, 'FlowSyncApi', {
      restApiName: 'flowsync-api',
      defaultCorsPreflightOptions: {
        allowOrigins: apigateway.Cors.ALL_ORIGINS,
        allowMethods: apigateway.Cors.ALL_METHODS,
        allowHeaders: ['Content-Type', 'Authorization'],
      },
    });

    const ingestionIntegration = new apigateway.LambdaIntegration(ingestionFn);
    const mcpIntegration = new apigateway.LambdaIntegration(mcpFn);
    const queryIntegration = new apigateway.LambdaIntegration(queryFn);
    const chatIntegration = new apigateway.LambdaIntegration(chatFn);

    // /api/v1
    const apiV1 = api.root.addResource('api').addResource('v1');

    // POST /api/v1/events
    apiV1.addResource('events').addMethod('POST', ingestionIntegration);

    // POST /api/v1/projects
    // GET  /api/v1/projects/{projectId}
    // GET  /api/v1/projects/{projectId}/events
    const projects = apiV1.addResource('projects');
    projects.addMethod('POST', ingestionIntegration);
    const projectById = projects.addResource('{projectId}');
    projectById.addMethod('GET', ingestionIntegration);
    projectById.addResource('events').addMethod('GET', queryIntegration);

    // POST /api/v1/query
    apiV1.addResource('query').addMethod('POST', queryIntegration);

    // POST /api/v1/chat
    apiV1.addResource('chat').addMethod('POST', chatIntegration);

    // POST /mcp
    api.root.addResource('mcp').addMethod('POST', mcpIntegration);

    // POST /webhooks/github - GitHub webhook ingestion
    const githubWebhookIntegration = new apigateway.LambdaIntegration(ingestionFn);
    api.root.addResource('webhooks').addResource('github').addMethod('POST', githubWebhookIntegration);

    // ─────────────────────────────────────────────
    // OUTPUTS (printed after deploy)
    // ─────────────────────────────────────────────

    new cdk.CfnOutput(this, 'ApiUrl', {
      value: api.url,
      description: 'API Gateway base URL — share with team',
    });

    new cdk.CfnOutput(this, 'RawEventsBucket', {
      value: rawEventsBucket.bucketName,
    });

    new cdk.CfnOutput(this, 'ProcessingQueueUrl', {
      value: processingQueue.queueUrl,
      description: 'SQS queue URL for event processing',
    });

    new cdk.CfnOutput(this, 'DLQUrl', {
      value: dlq.queueUrl,
      description: 'Dead Letter Queue URL for failed event processing',
    });

    // ─────────────────────────────────────────────
    // CLOUDWATCH METRICS & ALARMS (Observability)
    // ─────────────────────────────────────────────

    // SNS topic for alarm notifications
    const alarmTopic = new sns.Topic(this, 'FlowSyncAlarms', {
      topicName: 'flowsync-alarms',
      displayName: 'FlowSync Operational Alarms',
    });

    // Queue metrics
    const queueVisibleMessages = processingQueue.metricApproximateNumberOfMessagesVisible({
      period: cdk.Duration.minutes(5),
      statistic: 'Average',
    });
    const queueInFlightMessages = processingQueue.metricApproximateNumberOfMessagesNotVisible({
      period: cdk.Duration.minutes(5),
      statistic: 'Average',
    });
    const queueAgeMs = processingQueue.metricApproximateAgeOfOldestMessage({
      period: cdk.Duration.minutes(5),
      statistic: 'Maximum',
    });

    // DLQ metric
    const dlqMessages = dlq.metricApproximateNumberOfMessagesVisible({
      period: cdk.Duration.minutes(5),
      statistic: 'Maximum',
    });

    // Lambda metrics
    const ingestionErrors = ingestionFn.metricErrors({
      period: cdk.Duration.minutes(5),
      statistic: 'Sum',
    });
    const aiProcessingErrors = aiProcessingFn.metricErrors({
      period: cdk.Duration.minutes(5),
      statistic: 'Sum',
    });
    const aiProcessingThrottles = aiProcessingFn.metricThrottles({
      period: cdk.Duration.minutes(5),
      statistic: 'Sum',
    });
    const aiProcessingDuration = aiProcessingFn.metricDuration({
      period: cdk.Duration.minutes(5),
      statistic: 'Average',
    });
    const aiProcessingInvocations = aiProcessingFn.metricInvocations({
      period: cdk.Duration.minutes(5),
      statistic: 'Sum',
    });

    // Alarms
    new cloudwatch.Alarm(this, 'HighQueueBacklog', {
      alarmName: 'flowsync-high-queue-backlog',
      alarmDescription: 'Processing queue has >100 visible messages for 10 minutes',
      metric: queueVisibleMessages,
      threshold: 100,
      evaluationPeriods: 2,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    }).addAlarmAction(new cloudwatchActions.SnsAction(alarmTopic));

    new cloudwatch.Alarm(this, 'HighQueueAge', {
      alarmName: 'flowsync-high-queue-age',
      alarmDescription: 'Oldest message in queue exceeds visibility timeout (90s)',
      metric: queueAgeMs,
      threshold: 90000,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    }).addAlarmAction(new cloudwatchActions.SnsAction(alarmTopic));

    new cloudwatch.Alarm(this, 'DLQHasMessages', {
      alarmName: 'flowsync-dlq-has-messages',
      alarmDescription: 'Dead letter queue has messages requiring investigation',
      metric: dlqMessages,
      threshold: 0,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    }).addAlarmAction(new cloudwatchActions.SnsAction(alarmTopic));

    new cloudwatch.Alarm(this, 'AIProcessingErrors', {
      alarmName: 'flowsync-ai-processing-errors',
      alarmDescription: 'AI Processing Lambda error rate > 5% over 5 minutes',
      metric: new cloudwatch.MathExpression({
        expression: 'errors / invocations * 100',
        usingMetrics: { errors: aiProcessingErrors, invocations: aiProcessingInvocations },
        period: cdk.Duration.minutes(5),
      }),
      threshold: 5,
      evaluationPeriods: 2,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    }).addAlarmAction(new cloudwatchActions.SnsAction(alarmTopic));

    new cloudwatch.Alarm(this, 'AIProcessingThrottles', {
      alarmName: 'flowsync-ai-processing-throttles',
      alarmDescription: 'AI Processing Lambda throttled',
      metric: aiProcessingThrottles,
      threshold: 1,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    }).addAlarmAction(new cloudwatchActions.SnsAction(alarmTopic));

    new cloudwatch.Alarm(this, 'AIProcessingHighLatency', {
      alarmName: 'flowsync-ai-processing-high-latency',
      alarmDescription: 'AI Processing Lambda p50 latency > 30s',
      metric: aiProcessingDuration,
      threshold: 30000,
      evaluationPeriods: 3,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    }).addAlarmAction(new cloudwatchActions.SnsAction(alarmTopic));

    new cloudwatch.Alarm(this, 'IngestionErrors', {
      alarmName: 'flowsync-ingestion-errors',
      alarmDescription: 'Ingestion Lambda error rate > 1% over 5 minutes',
      metric: ingestionErrors,
      threshold: 5, // 5 errors in 5 min
      evaluationPeriods: 2,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    }).addAlarmAction(new cloudwatchActions.SnsAction(alarmTopic));

    // Dashboard for quick operational visibility
    const dashboard = new cloudwatch.Dashboard(this, 'FlowSyncDashboard', {
      dashboardName: 'FlowSync-Operational',
      widgets: [
        [
          new cloudwatch.GraphWidget({
            title: 'Queue Health',
            left: [queueVisibleMessages, queueInFlightMessages],
            right: [queueAgeMs],
            width: 12,
            height: 6,
          }),
          new cloudwatch.GraphWidget({
            title: 'DLQ Messages',
            left: [dlqMessages],
            width: 12,
            height: 6,
          }),
        ],
        [
          new cloudwatch.GraphWidget({
            title: 'AI Processing Lambda',
            left: [aiProcessingInvocations, aiProcessingErrors],
            right: [aiProcessingDuration, aiProcessingThrottles],
            width: 12,
            height: 6,
          }),
          new cloudwatch.GraphWidget({
            title: 'Ingestion Lambda',
            left: [ingestionErrors],
            width: 12,
            height: 6,
          }),
        ],
      ],
    });

    // ─────────────────────────────────────────────
    // FRONTEND — S3 + CLOUDFRONT STATIC HOSTING
    // ─────────────────────────────────────────────

    const frontendBucket = new s3.Bucket(this, 'FlowSyncFrontend', {
      bucketName: `flowsync-frontend-${this.account}-${this.region}`,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    const oac = new cloudfront.CfnOriginAccessControl(this, 'FrontendOAC', {
      originAccessControlConfig: {
        name: 'flowsync-frontend-oac',
        originAccessControlOriginType: 's3',
        signingBehavior: 'always',
        signingProtocol: 'sigv4',
      },
    });

    const distribution = new cloudfront.Distribution(this, 'FlowSyncCFN', {
      defaultBehavior: {
        origin: new origins.S3Origin(frontendBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
      },
      defaultRootObject: 'index.html',
      errorResponses: [
        { httpStatus: 403, responseHttpStatus: 200, responsePagePath: '/index.html' },
        { httpStatus: 404, responseHttpStatus: 200, responsePagePath: '/index.html' },
      ],
    });

    // Attach OAC to the S3 origin and grant bucket policy
    const cfnDistribution = distribution.node.defaultChild as cloudfront.CfnDistribution;
    cfnDistribution.addPropertyOverride(
      'DistributionConfig.Origins.0.OriginAccessControlId',
      oac.attrId,
    );
    cfnDistribution.addPropertyOverride(
      'DistributionConfig.Origins.0.S3OriginConfig.OriginAccessIdentity',
      '',
    );

    frontendBucket.addToResourcePolicy(new iam.PolicyStatement({
      actions: ['s3:GetObject'],
      principals: [new iam.ServicePrincipal('cloudfront.amazonaws.com')],
      resources: [frontendBucket.arnForObjects('*')],
      conditions: {
        StringEquals: {
          'AWS:SourceArn': `arn:aws:cloudfront::${this.account}:distribution/${distribution.distributionId}`,
        },
      },
    }));

    new s3deploy.BucketDeployment(this, 'FrontendDeploy', {
      sources: [s3deploy.Source.asset(path.join(__dirname, '../../frontend/out'))],
      destinationBucket: frontendBucket,
      distribution,
      distributionPaths: ['/*'],
    });

    new cdk.CfnOutput(this, 'FrontendUrl', {
      value: `https://${distribution.distributionDomainName}`,
      description: 'CloudFront URL for FlowSync frontend',
    });

  }
}
