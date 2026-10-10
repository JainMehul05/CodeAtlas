import {
  DynamoDBClient,
} from '@aws-sdk/client-dynamodb';
import {
  DynamoDBDocumentClient,
  PutCommand,
  GetCommand,
} from '@aws-sdk/lib-dynamodb';
import {
  S3Client,
  PutObjectCommand,
} from '@aws-sdk/client-s3';
import {
  SQSClient,
  SendMessageCommand,
} from '@aws-sdk/client-sqs';
import * as crypto from 'crypto';
import {
  FlowSyncEvent,
  EventType,
  EventSource,
  validateEvent,
  createEvent,
  PushEventPayload,
  Actor,
  WorkflowRunPayload,
  CheckRunPayload,
  CheckSuitePayload,
  isWorkflowRunPayload,
  isCheckRunPayload,
  isCheckSuitePayload,
} from '@flowsync/shared';
import { authenticate, verifyToken, hashToken } from './src/auth';

// ── GitHub Webhook Payload Types ─────────────────────────────────────────────
interface GitHubUser {
  login: string;
  name?: string;
}

interface GitHubCommit {
  id: string;
  message: string;
  author: GitHubUser;
}

interface GitHubRepository {
  id: number;
  name: string;
  full_name: string;
}

interface GitHubPullRequest {
  number: number;
  title: string;
  body: string | null;
  action: string;
  user: GitHubUser;
  head: { ref: string };
  base: { ref: string };
  merged: boolean;
  merge_commit_sha: string;
}

interface GitHubPushPayload {
  ref: string;
  repository: GitHubRepository;
  commits: GitHubCommit[];
  head_commit?: GitHubCommit;
  pusher: GitHubUser;
}

interface GitHubPullRequestPayload {
  action: string;
  pull_request: GitHubPullRequest;
  repository: GitHubRepository;
}

// ── GitHub Actions Webhook Payload Types ──────────────────────────────────────
interface GitHubWorkflowRun {
  id: number;
  name: string;
  run_id: number;
  run_number: number;
  run_attempt: number;
  event: string;
  status: string;
  conclusion?: string;
  head_branch: string;
  head_sha: string;
  repository: GitHubRepository;
  started_at: string;
  completed_at?: string;
  html_url: string;
  check_suite_id?: number;
  pull_requests?: Array<{
    number: number;
    head_branch: string;
    base_branch: string;
  }>;
}

interface GitHubWorkflowRunPayload {
  action: string;  // "requested", "in_progress", "completed"
  workflow_run: GitHubWorkflowRun;
  repository: GitHubRepository;
}

interface GitHubCheckRun {
  id: number;
  name: string;
  head_sha: string;
  status: string;
  conclusion?: string;
  started_at: string;
  completed_at?: string;
  html_url: string;
  repository: GitHubRepository;
  check_suite_id: number;
  pull_requests?: Array<{
    number: number;
    head_branch: string;
    base_branch: string;
  }>;
  output?: {
    title: string;
    summary: string;
    text?: string;
    annotations_count?: number;
    annotations_url?: string;
  };
}

interface GitHubCheckRunPayload {
  action: string;  // "created", "in_progress", "completed", "rerequested", "requested_action"
  check_run: GitHubCheckRun;
  repository: GitHubRepository;
}

interface GitHubCheckSuite {
  id: number;
  head_branch: string;
  head_sha: string;
  status: string;
  conclusion?: string;
  repository: GitHubRepository;
  pull_requests?: Array<{
    number: number;
    head_branch: string;
    base_branch: string;
  }>;
  created_at: string;
  updated_at: string;
}

interface GitHubCheckSuitePayload {
  action: string;  // "completed", "requested", "rerequested"
  check_suite: GitHubCheckSuite;
  repository: GitHubRepository;
}

// ── Config — from Lambda environment variables set by CDK ──────────────────
const PROJECTS_TABLE = process.env.PROJECTS_TABLE;               // flowsync-projects
const EVENTS_TABLE = process.env.EVENTS_TABLE;                 // flowsync-events
const AUDIT_TABLE = process.env.AUDIT_TABLE;                   // flowsync-audit
const RAW_EVENTS_BUCKET = process.env.RAW_EVENTS_BUCKET;       // flowsync-raw-events-{account}
const PROJECT_REPO_MAPPING_TABLE = process.env.PROJECT_REPO_MAPPING_TABLE; // flowsync-project-repo-mapping

function getProcessingQueueUrl() {
  return process.env.PROCESSING_QUEUE_URL;
}

// ── SDK client factory functions (allows mocking in tests) ───────────────────
let dynamoClient: ReturnType<typeof DynamoDBDocumentClient.from> | null = null;
let s3Client: S3Client | null = null;
let sqsClient: SQSClient | null = null;

export function getDynamoClient() {
  if (!dynamoClient) {
    dynamoClient = DynamoDBDocumentClient.from(
      new DynamoDBClient({ region: 'us-east-1' }),
      { marshallOptions: { removeUndefinedValues: true } },
    );
  }
  return dynamoClient;
}

export function getS3Client() {
  if (!s3Client) {
    s3Client = new S3Client({ region: 'us-east-1' });
  }
  return s3Client;
}

export function getSqsClient() {
  if (!sqsClient) {
    sqsClient = new SQSClient({ region: 'us-east-1' });
  }
  return sqsClient;
}

export function setClientsForTesting(clients: { dynamo?: typeof dynamoClient; s3?: typeof s3Client; sqs?: typeof sqsClient }) {
  if (clients.dynamo) dynamoClient = clients.dynamo;
  if (clients.s3) s3Client = clients.s3;
  if (clients.sqs) sqsClient = clients.sqs;
}

export function clearClientsForTesting() {
  dynamoClient = null;
  s3Client = null;
  sqsClient = null;
}

// Test helper to access the mock SQS send function
export let __mockSqsSend: ((cmd: SendMessageCommand) => Promise<any>) | null = null;

export function setMockSqsSend(mock: (cmd: SendMessageCommand) => Promise<any>) {
  __mockSqsSend = mock;
}

export function getMockSqsSend() {
  return __mockSqsSend;
}

export function clearMockSqsSend() {
  __mockSqsSend = null;
}

// ── Project-Repository Mapping ──────────────────────────────────────────────
function getProjectRepoMappingTable(): string | undefined {
  return process.env.PROJECT_REPO_MAPPING_TABLE;
}

async function getProjectIdFromRepository(repoFullName: string): Promise<string | null> {
  const tableName = getProjectRepoMappingTable();
  if (!tableName) {
    console.error('[fatal] PROJECT_REPO_MAPPING_TABLE not configured');
    return null;
  }
  const repositoryId = `github:${repoFullName.toLowerCase()}`;
  try {
    const result = await getDynamoClient().send(new GetCommand({
      TableName: tableName,
      Key: { repositoryId },
    }));
    return result.Item?.projectId ?? null;
  } catch (err) {
    console.error('[error] Failed to lookup project-repo mapping', { repositoryId, error: (err as Error).message });
    return null;
  }
}

// ── Correlation ID support ──────────────────────────────────────────────────
function generateCorrelationId(): string {
  return crypto.randomUUID();
}

function extractCorrelationId(headers: Record<string, string | undefined>): string {
  if (!headers) return generateCorrelationId();
  const headerKeys = Object.keys(headers).map(k => k.toLowerCase());
  const corrIdx = headerKeys.indexOf('x-correlation-id');
  const reqIdx = headerKeys.indexOf('x-request-id');
  const originalKeys = Object.keys(headers);
  if (corrIdx !== -1) return headers[originalKeys[corrIdx]] || generateCorrelationId();
  if (reqIdx !== -1) return headers[originalKeys[reqIdx]] || generateCorrelationId();
  return generateCorrelationId();
}

// ── Response Builder ────────────────────────────────────────────────────────
function respond(statusCode: number, body: Record<string, unknown>, correlationId?: string) {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Content-Type,Authorization',
  };
  if (correlationId) {
    headers['x-correlation-id'] = correlationId;
  }
  return {
    statusCode,
    headers,
    body: JSON.stringify(body),
  };
}

// ── Validation ──────────────────────────────────────────────────────────────
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const COMMIT_HASH = /^[0-9a-f]{40}$/i;
const ISO_8601 = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})$/;
const NAME_REGEX = /^[a-zA-Z0-9_-]+$/;

function validateProjectInput(input: Record<string, unknown>): string[] {
  const errors: string[] = [];
  if (!input.name)                          errors.push('name: required');
  else if (!NAME_REGEX.test(String(input.name)))    errors.push('name: alphanumeric, hyphens, underscores only');
  if (!input.description)                   errors.push('description: required');
  if (!Array.isArray(input.languages) || input.languages.length === 0)
                                            errors.push('languages: required, must be a non-empty array');
  if (!input.defaultBranch)                 errors.push('defaultBranch: required');
  if (input.frameworks  && !Array.isArray(input.frameworks))  errors.push('frameworks: must be an array');
  if (input.teamMembers && !Array.isArray(input.teamMembers)) errors.push('teamMembers: must be an array');
  else if (Array.isArray(input.teamMembers)) {
    input.teamMembers.forEach((m: Record<string, unknown>, i: number) => {
      if (!m.name) errors.push(`teamMembers[${i}].name: required`);
      if (!m.role) errors.push(`teamMembers[${i}].role: required`);
    });
  }
  return errors;
}

// ── GitHub Webhook Signature Verification ───────────────────────────────────
export function verifyGitHubSignature(payload: string, signature: string | undefined, secret: string): boolean {
  if (!signature || !signature.startsWith('sha256=')) {
    return false;
  }
  const expectedSignature = signature.slice(7);
  const hmac = crypto.createHmac('sha256', secret);
  hmac.update(payload);
  const computedSignature = hmac.digest('hex');
  return crypto.timingSafeEqual(Buffer.from(expectedSignature), Buffer.from(computedSignature));
}

// ── Route: POST /api/v1/projects ────────────────────────────────────────────
export async function createProject(body: string | Record<string, unknown>, correlationId: string) {
  const input = typeof body === 'string' ? JSON.parse(body) : body;

  const errors = validateProjectInput(input);
  if (errors.length) return respond(400, { error: 'validation_failed', details: errors }, correlationId);

  const projectId = crypto.randomUUID();
  const apiToken = crypto.randomBytes(32).toString('hex');
  const apiTokenHash = hashToken(apiToken);

  const now = new Date().toISOString();
  const record = {
    projectId,
    name: input.name,
    description: input.description,
    languages: input.languages,
    frameworks: input.frameworks ?? [],
    defaultBranch: input.defaultBranch,
    teamMembers: input.teamMembers ?? [],
    apiTokenHash,
    createdAt: now,
    lastActivityAt: now,
    eventCount: 0,
  };

  await getDynamoClient().send(new PutCommand({ TableName: PROJECTS_TABLE, Item: record }));

  try {
    await getDynamoClient().send(new PutCommand({
      TableName: AUDIT_TABLE,
      Item: {
        entityId: projectId,
        timestamp: now,
        entityType: 'project',
        action: 'created',
        actor: 'system',
        changes: { name: input.name, defaultBranch: input.defaultBranch },
        reason: 'Project initialised via onboarding wizard',
        correlationId,
      },
    }));
  } catch (err) { console.error('[non-fatal] Audit write failed:', (err as Error).message); }

  return respond(200, { projectId, apiToken }, correlationId);
}

// ── Route: POST /api/v1/events ──────────────────────────────────────────────
export async function handleApiEvent(headers: Record<string, string | undefined>, body: string | Record<string, unknown>, correlationId: string) {
  let event: Record<string, unknown>;
  try   { event = typeof body === 'string' ? JSON.parse(body) : body; }
  catch { return respond(400, { error: 'invalid_json', message: 'Body must be valid JSON' }, correlationId); }

  const auth = await authenticate(headers, String(event.projectId));
  if (auth.error) return respond(auth.statusCode, auth.error, correlationId);

  // Use shared validation
  const validation = validateEvent(event as unknown);
  if (!validation.success) {
    return respond(400, { error: 'validation_failed', details: validation.errors?.map(e => e.message) }, correlationId);
  }

  const eventCorrelationId = (event.correlationId as string) || correlationId;

  const receivedAt = new Date().toISOString();
  const t_ingestion_start = Date.now();

  const record = {
    projectId: event.projectId,
    timestampEventId: `${event.timestamp}#${event.eventId}`,
    branchTimestamp: `${event.branch}#${event.timestamp}`,
    eventId: event.eventId,
    eventType: event.eventType,
    branch: event.branch,
    parentBranch: (event.payload as Record<string, unknown>).parentBranch ?? null,
    payload: event.payload,
    receivedAt,
    processingStatus: 'pending',
    correlationId: eventCorrelationId,
  };

  await getDynamoClient().send(new PutCommand({ TableName: EVENTS_TABLE, Item: record }));

  try {
    await getS3Client().send(new PutObjectCommand({
      Bucket: RAW_EVENTS_BUCKET,
      Key: `raw-events/${event.projectId}/${event.eventId}.json`,
      Body: JSON.stringify(event, null, 2),
      ContentType: 'application/json',
    }));
  } catch (err) { console.error('[non-fatal] S3 archive failed:', (err as Error).message); }

  // Send to SQS processing queue instead of direct Lambda invocation
  try {
    const sqsMessage = {
      eventId: event.eventId,
      projectId: event.projectId,
      eventType: event.eventType,
      branch: event.branch,
      parentBranch: (event.payload as Record<string, unknown>).parentBranch ?? null,
      payload: event.payload,
      timestamp: event.timestamp,
      correlationId: eventCorrelationId,
    };
    const command = new SendMessageCommand({
      QueueUrl: getProcessingQueueUrl(),
      MessageBody: JSON.stringify(sqsMessage),
      MessageAttributes: {
        correlationId: {
          DataType: 'String',
          StringValue: eventCorrelationId,
        },
        eventType: {
          DataType: 'String',
          StringValue: String(event.eventType),
        },
      },
    });
    if (__mockSqsSend) {
      await __mockSqsSend(command);
    } else {
      await getSqsClient().send(command);
    }
    console.log(`[SQS] Event ${event.eventId} sent to processing queue`);
  } catch (err) {
    console.error('[fatal] SQS send failed:', (err as Error).message);
    return respond(500, { error: 'queue_failed', message: 'Failed to queue event for processing' }, correlationId);
  }

  if ((event.payload as Record<string, unknown>).isMerge && (event.payload as Record<string, unknown>).sourceBranch) {
    // For merge propagation, send a separate message
    try {
      const propagateMessage = {
        propagate: true,
        projectId: event.projectId,
        sourceBranch: (event.payload as Record<string, unknown>).sourceBranch,
        targetBranch: event.branch,
        timestamp: event.timestamp,
        correlationId: eventCorrelationId,
      };
      const command = new SendMessageCommand({
        QueueUrl: getProcessingQueueUrl(),
        MessageBody: JSON.stringify(propagateMessage),
        MessageAttributes: {
          correlationId: {
            DataType: 'String',
            StringValue: eventCorrelationId,
          },
        },
      });
      if (__mockSqsSend) {
        await __mockSqsSend(command);
      } else {
        await getSqsClient().send(command);
      }
      console.log(`[SQS] Merge propagation queued: ${(event.payload as Record<string, unknown>).sourceBranch} → ${event.branch}`);
    } catch (err) { console.error('[non-fatal] Merge propagation SQS send failed:', (err as Error).message); }
  }

  try {
    await getDynamoClient().send(new PutCommand({
      TableName: AUDIT_TABLE,
      Item: {
        entityId: event.eventId,
        timestamp: receivedAt,
        entityType: 'event',
        action: 'created',
        actor: (event.payload as Record<string, unknown>).author ?? 'system',
        changes: {
          eventType: event.eventType,
          branch: event.branch,
          commitHash: (event.payload as Record<string, unknown>).commitHash ?? null,
        },
        correlationId: eventCorrelationId,
      },
    }));
  } catch (err) { console.error('[non-fatal] Audit write failed:', (err as Error).message); }

  const ingestion_ms = Date.now() - t_ingestion_start;
  console.log(JSON.stringify({ INGESTION_TIMING: true, eventId: event.eventId, ingestion_ms, receivedAt, correlationId: eventCorrelationId }));

  return respond(200, {
    eventId: event.eventId,
    projectId: event.projectId,
    branch: event.branch,
    status: 'queued',
    receivedAt,
    correlationId: eventCorrelationId,
  }, correlationId);
}

// ── Route: POST /webhooks/github ────────────────────────────────────────────
export async function handleGitHubWebhook(
  headers: Record<string, string | undefined>,
  body: string,
  correlationId: string
) {
  // Extract GitHub headers
  const githubEvent = headers['x-github-event']?.toLowerCase();
  const githubDelivery = headers['x-github-delivery'];
  const githubSignature = headers['x-hub-signature-256'];

  // Support push, pull_request, workflow_run, check_run, and check_suite events
  const supportedEvents = ['push', 'pull_request', 'workflow_run', 'check_run', 'check_suite'];
  if (!githubEvent || !supportedEvents.includes(githubEvent)) {
    return respond(400, { error: 'unsupported_event', message: `Unsupported GitHub event type: ${githubEvent}` }, correlationId);
  }

  if (!githubDelivery) {
    return respond(400, { error: 'missing_delivery_id', message: 'Missing X-GitHub-Delivery header' }, correlationId);
  }

  // Verify webhook signature
  const webhookSecret = process.env.GITHUB_WEBHOOK_SECRET;
  if (!webhookSecret) {
    console.error('[fatal] GITHUB_WEBHOOK_SECRET not configured');
    return respond(500, { error: 'configuration_error', message: 'Webhook secret not configured' }, correlationId);
  }

  if (!verifyGitHubSignature(body, githubSignature, webhookSecret)) {
    return respond(401, { error: 'invalid_signature', message: 'Invalid GitHub webhook signature' }, correlationId);
  }

  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(body);
  } catch {
    return respond(400, { error: 'invalid_json', message: 'Body must be valid JSON' }, correlationId);
  }

  // Extract project ID from repository
  const repository = (payload as Record<string, unknown>).repository as { full_name?: string } | undefined;
  const repoFullName = repository?.full_name;
  if (!repoFullName) {
    return respond(400, { error: 'missing_repository', message: 'Repository information missing from payload' }, correlationId);
  }

  // Look up project ID from repository mapping table
  const projectId = await getProjectIdFromRepository(repoFullName);
  if (!projectId) {
    return respond(403, { error: 'project_not_found', message: `Repository ${repoFullName} is not mapped to a CodeAtlas project` }, correlationId);
  }

  // Extract event data based on event type
  const eventId = githubDelivery; // Use GitHub delivery ID as event ID for idempotency
  const timestamp = new Date().toISOString();
  let eventData: FlowSyncEvent;

  if (githubEvent === 'push') {
    // ... existing push handler
    const pushPayload = payload as Record<string, unknown>;
    const commits = (payload.commits as Array<Record<string, unknown>>) || [];
    const headCommit = (commits[commits.length - 1] || {}) as Record<string, unknown>;
    const diff = ''; // GitHub push webhook doesn't include diff; would need to fetch separately
    const commitHash = (headCommit.id as string) || '';
    const message = (headCommit.message as string) || (payload.head_commit?.message as string) || '';
    const author = (headCommit.author?.name as string) || (payload.pusher?.name as string) || 'unknown';
    const branch = (payload.ref as string)?.replace('refs/heads/', '') || 'main';

    eventData = {
      eventId,
      eventType: EventType.PUSH,
      source: EventSource.GITHUB,
      projectId,
      schemaVersion: '1',
      timestamp,
      correlationId,
      deliveryId: githubDelivery,
      actor: { id: author, name: author },
      payload: {
        commitHash,
        message,
        diff,
        author,
        parentBranch: undefined,
        isMerge: false,
        sourceBranch: undefined,
        branch,
        changedFiles: undefined,
      },
    };
  } else if (githubEvent === 'pull_request') {
    // ... existing pull_request handler
    const prPayload = payload as Record<string, unknown>;
    const action = prPayload.action as string;
    const pullRequest = prPayload.pull_request as Record<string, unknown> | undefined;
    
    if (!pullRequest || !['opened', 'closed', 'reopened', 'merged'].includes(action)) {
      return respond(200, { message: `Pull request action '${action}' acknowledged but not processed` }, correlationId);
    }

    const prNumber = pullRequest.number as number;
    const prTitle = pullRequest.title as string;
    const prBody = (pullRequest.body as string) || '';
    const prAuthor = (pullRequest.user?.login as string) || 'unknown';
    const sourceBranch = (pullRequest.head?.ref as string) || '';
    const targetBranch = (pullRequest.base?.ref as string) || '';
    const commitHash = (pullRequest.merge_commit_sha as string) || '';
    const isMerged = action === 'closed' && (pullRequest.merged as boolean) === true;

    eventData = {
      eventId,
      eventType: isMerged ? EventType.MERGE : EventType.PUSH,
      source: EventSource.GITHUB,
      projectId,
      schemaVersion: '1',
      timestamp,
      correlationId,
      deliveryId: githubDelivery,
      actor: { id: prAuthor, name: prAuthor },
      payload: {
        commitHash: isMerged ? commitHash : '',
        message: prTitle,
        diff: '', // Would need to fetch separately
        author: prAuthor,
        parentBranch: targetBranch,
        isMerge: isMerged,
        sourceBranch: isMerged ? sourceBranch : undefined,
        branch: targetBranch,
        changedFiles: undefined,
        pullRequest: {
          number: prNumber,
          title: prTitle,
          body: prBody,
          action,
          merged: isMerged,
        },
      },
    }
  } else if (githubEvent === 'workflow_run') {
    // Handle workflow_run events (GitHub Actions)
    const wrPayload = payload as unknown as GitHubWorkflowRunPayload;
    const action = wrPayload.action;
    const workflowRun = wrPayload.workflow_run;

    // Only process completed workflow runs for now
    if (action !== 'completed' || !workflowRun.conclusion) {
      return respond(200, { message: `Workflow run action '${action}' acknowledged but not processed` }, correlationId);
    }

    const repoId = workflowRun.repository.full_name.replace('/', '-');
    const repositoryId = `repo:github:${repoId}`;
    
    // Extract PR numbers if available
    const prNumbers = workflowRun.pull_requests?.map(pr => pr.number) || [];

    eventData = {
      eventId,
      eventType: EventType.WORKFLOW_RUN,
      source: EventSource.CI_CD,
      projectId,
      schemaVersion: '1',
      timestamp,
      correlationId,
      deliveryId: githubDelivery,
      actor: { id: 'github-actions', name: 'GitHub Actions' },
      payload: {
        workflowId: workflowRun.id,
        workflowName: workflowRun.name,
        runId: workflowRun.run_id,
        runNumber: workflowRun.run_number,
        runAttempt: workflowRun.run_attempt,
        event: workflowRun.event,
        status: workflowRun.status,
        conclusion: workflowRun.conclusion,
        headBranch: workflowRun.head_branch,
        headSha: workflowRun.head_sha,
        repository: {
          id: workflowRun.repository.id,
          name: workflowRun.repository.name,
          fullName: workflowRun.repository.full_name,
        },
        startedAt: workflowRun.started_at,
        completedAt: workflowRun.completed_at,
        htmlUrl: workflowRun.html_url,
        checkSuiteId: workflowRun.check_suite_id,
        pullRequests: workflowRun.pull_requests,
      } as WorkflowRunPayload,
    };
  } else if (githubEvent === 'check_run') {
    // Handle check_run events
    const crPayload = payload as unknown as GitHubCheckRunPayload;
    const action = crPayload.action;
    const checkRun = crPayload.check_run;

    // Only process completed check runs
    if (action !== 'completed' || !checkRun.conclusion) {
      return respond(200, { message: `Check run action '${action}' acknowledged but not processed` }, correlationId);
    }

    const repoId = checkRun.repository.full_name.replace('/', '-');
    const repositoryId = `repo:github:${repoId}`;
    
    // Extract PR numbers if available
    const prNumbers = checkRun.pull_requests?.map(pr => pr.number) || [];

    eventData = {
      eventId,
      eventType: EventType.CHECK_RUN,
      source: EventSource.CI_CD,
      projectId,
      schemaVersion: '1',
      timestamp,
      correlationId,
      deliveryId: githubDelivery,
      actor: { id: 'github-actions', name: 'GitHub Actions' },
      payload: {
        checkRunId: checkRun.id,
        name: checkRun.name,
        headSha: checkRun.head_sha,
        status: checkRun.status,
        conclusion: checkRun.conclusion,
        startedAt: checkRun.started_at,
        completedAt: checkRun.completed_at,
        htmlUrl: checkRun.html_url,
        repository: {
          id: checkRun.repository.id,
          name: checkRun.repository.name,
          fullName: checkRun.repository.full_name,
        },
        checkSuiteId: checkRun.check_suite_id,
        pullRequests: checkRun.pull_requests,
        output: checkRun.output,
      } as CheckRunPayload,
    };
  } else if (githubEvent === 'check_suite') {
    // Handle check_suite events
    const csPayload = payload as unknown as GitHubCheckSuitePayload;
    const action = csPayload.action;
    const checkSuite = csPayload.check_suite;

    if (action !== 'completed' || !checkSuite.conclusion) {
      return respond(200, { message: `Check suite action '${action}' acknowledged but not processed` }, correlationId);
    }

    const repoId = checkSuite.repository.full_name.replace('/', '-');
    const repositoryId = `repo:github:${repoId}`;

    eventData = {
      eventId,
      eventType: EventType.CHECK_SUITE,
      source: EventSource.CI_CD,
      projectId,
      schemaVersion: '1',
      timestamp,
      correlationId,
      deliveryId: githubDelivery,
      actor: { id: 'github-actions', name: 'GitHub Actions' },
      payload: {
        checkSuiteId: checkSuite.id,
        headBranch: checkSuite.head_branch,
        headSha: checkSuite.head_sha,
        status: checkSuite.status,
        conclusion: checkSuite.conclusion,
        repository: {
          id: checkSuite.repository.id,
          name: checkSuite.repository.name,
          fullName: checkSuite.repository.full_name,
        },
        pullRequests: checkSuite.pull_requests,
        createdAt: checkSuite.created_at,
        updatedAt: checkSuite.updated_at,
      } as CheckSuitePayload,
    };
  } else {
    return respond(400, { error: 'unsupported_event', message: `Unsupported GitHub event type: ${githubEvent}` }, correlationId);
  }

  // Validate the constructed event
  const validation = validateEvent(eventData);
  if (!validation.success) {
    return respond(400, { error: 'validation_failed', details: validation.errors?.map(e => e.message) }, correlationId);
  }

  // Store the raw webhook payload for audit
  try {
    await getS3Client().send(new PutObjectCommand({
      Bucket: RAW_EVENTS_BUCKET,
      Key: `raw-webhooks/${projectId}/${eventId}.json`,
      Body: body,
      ContentType: 'application/json',
    }));
  } catch (err) { console.error('[non-fatal] S3 webhook archive failed:', (err as Error).message); }

  // Send to SQS processing queue
  try {
    const sqsMessage = {
      eventId: eventData.eventId,
      projectId: eventData.projectId,
      eventType: eventData.eventType,
      branch: (eventData.payload as Record<string, unknown>).branch as string | undefined ?? 
              (eventData.payload as Record<string, unknown>).headBranch as string | undefined,
      parentBranch: (eventData.payload as Record<string, unknown>).parentBranch ?? null,
      payload: eventData.payload,
      timestamp: eventData.timestamp,
      correlationId: eventData.correlationId,
      deliveryId: eventData.deliveryId,
    };
    const command = new SendMessageCommand({
      QueueUrl: getProcessingQueueUrl(),
      MessageBody: JSON.stringify(sqsMessage),
      MessageAttributes: {
        correlationId: {
          DataType: 'String',
          StringValue: eventData.correlationId || correlationId,
        },
        eventType: {
          DataType: 'String',
          StringValue: String(eventData.eventType),
        },
        deliveryId: {
          DataType: 'String',
          StringValue: eventData.deliveryId || '',
        },
      },
    });
    if (__mockSqsSend) {
      await __mockSqsSend(command);
    } else {
      await getSqsClient().send(command);
    }
    console.log(`[SQS] GitHub webhook event ${eventId} sent to processing queue`);
  } catch (err) {
    console.error('[fatal] SQS send failed:', (err as Error).message);
    return respond(500, { error: 'queue_failed', message: 'Failed to queue event for processing' }, correlationId);
  }

  return respond(200, {
    eventId: eventData.eventId,
    projectId: eventData.projectId,
    branch: (eventData.payload as Record<string, unknown>).branch as string | undefined ?? 
            (eventData.payload as Record<string, unknown>).headBranch as string | undefined,
    status: 'queued',
    receivedAt: timestamp,
    correlationId: eventData.correlationId,
  }, correlationId);
}

// ── Route: GET /api/v1/projects/{projectId} ─────────────────────────────────
export async function getProject(headers: Record<string, string | undefined>, pathParameters: Record<string, string> | undefined, correlationId: string) {
  const { projectId } = pathParameters ?? {};
  if (!projectId) return respond(400, { error: 'validation_failed', details: ['projectId: required'] }, correlationId);

  const auth = await authenticate(headers, projectId);
  if (auth.error) return respond(auth.statusCode, auth.error, correlationId);

  const p = auth.project;
  return respond(200, {
    projectId: p.projectId,
    name: p.name,
    description: p.description,
    languages: p.languages,
    frameworks: p.frameworks,
    defaultBranch: p.defaultBranch,
    teamMembers: p.teamMembers,
    lastActivityAt: p.lastActivityAt,
    eventCount: p.eventCount,
  }, correlationId);
}

// ─────────────────────────────────────────────────────────────────────────────
// MAIN HANDLER ────────────────────────────────────────────────────────────────
// ─────────────────────────────────────────────────────────────────────────────

export const handler = async (event: {
  httpMethod?: string;
  resource?: string;
  pathParameters?: Record<string, string>;
  headers?: Record<string, string | undefined>;
  body?: string;
}) => {
  const correlationId = extractCorrelationId(event.headers);

  console.log(JSON.stringify({
    msg: 'request',
    method: event.httpMethod,
    resource: event.resource,
    correlationId,
  }));

  try {
    const method = event.httpMethod ?? '';
    const resource = event.resource ?? '';

    if (method === 'OPTIONS') return respond(200, { ok: true }, correlationId);

    if (method === 'POST' && resource === '/api/v1/projects')
      return await createProject(event.body ?? '', correlationId);

    if (method === 'POST' && resource === '/api/v1/events')
      return await handleApiEvent(event.headers ?? {}, event.body ?? '', correlationId);

    if (method === 'POST' && resource === '/webhooks/github')
      return await handleGitHubWebhook(event.headers ?? {}, event.body ?? '', correlationId);

    if (method === 'GET' && resource === '/api/v1/projects/{projectId}')
      return await getProject(event.headers ?? {}, event.pathParameters, correlationId);

    return respond(404, { error: 'not_found', message: `No route for ${method} ${resource}` }, correlationId);

  } catch (err) {
    console.error(JSON.stringify({
      msg: 'unhandled_error',
      error: (err as Error).message,
      stack: (err as Error).stack,
      correlationId,
    }));
    return respond(500, { error: 'internal_error', message: 'An unexpected error occurred' }, correlationId);
  }
};