"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.handler = void 0;
const client_dynamodb_1 = require("@aws-sdk/client-dynamodb");
const lib_dynamodb_1 = require("@aws-sdk/lib-dynamodb");
const client_s3_1 = require("@aws-sdk/client-s3");
const client_lambda_1 = require("@aws-sdk/client-lambda");
const crypto_1 = __importDefault(require("crypto"));
const shared_1 = require("@flowsync/shared");
// ── Config — from Lambda environment variables set by CDK ──────────────────
const PROJECTS_TABLE = process.env.PROJECTS_TABLE; // flowsync-projects
const EVENTS_TABLE = process.env.EVENTS_TABLE; // flowsync-events
const AUDIT_TABLE = process.env.AUDIT_TABLE; // flowsync-audit
const RAW_EVENTS_BUCKET = process.env.RAW_EVENTS_BUCKET; // flowsync-raw-events-{account}
const AI_FUNCTION = process.env.AI_PROCESSING_FUNCTION_NAME; // flowsync-ai-processing
// ── Singleton SDK clients ───────────────────────────────────────────────────
const dynamo = lib_dynamodb_1.DynamoDBDocumentClient.from(new client_dynamodb_1.DynamoDBClient({ region: 'us-east-1' }), { marshallOptions: { removeUndefinedValues: true } });
const s3 = new client_s3_1.S3Client({ region: 'us-east-1' });
const lambdaClient = new client_lambda_1.LambdaClient({ region: 'us-east-1' });
// ── Correlation ID support ──────────────────────────────────────────────────
function generateCorrelationId() {
    return crypto_1.default.randomUUID();
}
function extractCorrelationId(headers) {
    if (!headers)
        return generateCorrelationId();
    const headerKeys = Object.keys(headers).map(k => k.toLowerCase());
    const corrIdx = headerKeys.indexOf('x-correlation-id');
    const reqIdx = headerKeys.indexOf('x-request-id');
    const originalKeys = Object.keys(headers);
    if (corrIdx !== -1)
        return headers[originalKeys[corrIdx]] || generateCorrelationId();
    if (reqIdx !== -1)
        return headers[originalKeys[reqIdx]] || generateCorrelationId();
    return generateCorrelationId();
}
// ── Token Helpers (scrypt KDF) ──────────────────────────────────────────────
function hashToken(plaintext) {
    const salt = crypto_1.default.randomBytes(16).toString('hex');
    const hash = crypto_1.default.scryptSync(plaintext, salt, 64).toString('hex');
    return `${salt}:${hash}`;
}
function verifyToken(plaintext, stored) {
    const [salt, hash] = (stored ?? '').split(':');
    if (!salt || !hash)
        return false;
    const candidate = crypto_1.default.scryptSync(plaintext, salt, 64).toString('hex');
    return crypto_1.default.timingSafeEqual(Buffer.from(hash, 'hex'), Buffer.from(candidate, 'hex'));
}
// ── Response Builder ────────────────────────────────────────────────────────
function respond(statusCode, body, correlationId) {
    const headers = {
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
// ── Auth ────────────────────────────────────────────────────────────────────
function extractToken(headers) {
    const raw = headers?.Authorization ?? headers?.authorization;
    if (!raw)
        return null;
    const [scheme, token] = raw.split(' ');
    return scheme === 'Bearer' && token ? token : null;
}
async function authenticate(headers, projectId) {
    const token = extractToken(headers);
    if (!token)
        return { error: { error: 'invalid_token', message: 'Missing Authorization header' }, statusCode: 401 };
    const { Item } = await dynamo.send(new lib_dynamodb_1.GetCommand({ TableName: PROJECTS_TABLE, Key: { projectId } }));
    if (!Item?.apiTokenHash)
        return { error: { error: 'invalid_token' }, statusCode: 401 };
    if (!verifyToken(token, Item.apiTokenHash))
        return { error: { error: 'invalid_token' }, statusCode: 401 };
    return { project: Item };
}
// ── Validation ──────────────────────────────────────────────────────────────
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const COMMIT_HASH = /^[0-9a-f]{40}$/i;
const ISO_8601 = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})$/;
const NAME_REGEX = /^[a-zA-Z0-9_-]+$/;
function validateProjectInput(input) {
    const errors = [];
    if (!input.name)
        errors.push('name: required');
    else if (!NAME_REGEX.test(String(input.name)))
        errors.push('name: alphanumeric, hyphens, underscores only');
    if (!input.description)
        errors.push('description: required');
    if (!Array.isArray(input.languages) || input.languages.length === 0)
        errors.push('languages: required, must be a non-empty array');
    if (!input.defaultBranch)
        errors.push('defaultBranch: required');
    if (input.frameworks && !Array.isArray(input.frameworks))
        errors.push('frameworks: must be an array');
    if (input.teamMembers && !Array.isArray(input.teamMembers))
        errors.push('teamMembers: must be an array');
    else if (Array.isArray(input.teamMembers)) {
        input.teamMembers.forEach((m, i) => {
            if (!m.name)
                errors.push(`teamMembers[${i}].name: required`);
            if (!m.role)
                errors.push(`teamMembers[${i}].role: required`);
        });
    }
    return errors;
}
function validateEventLocal(ev) {
    const errors = [];
    if (!ev.eventId)
        errors.push('eventId: required');
    else if (!UUID_V4.test(String(ev.eventId)))
        errors.push('eventId: must be a valid UUID v4');
    if (!ev.projectId)
        errors.push('projectId: required');
    if (!['push', 'developer_note'].includes(String(ev.eventType)))
        errors.push('eventType: must be push or developer_note');
    if (!ev.timestamp)
        errors.push('timestamp: required');
    else if (!ISO_8601.test(String(ev.timestamp)))
        errors.push('timestamp: must be valid ISO 8601 UTC');
    if (!ev.branch)
        errors.push('branch: required');
    else if (String(ev.branch).length > 255)
        errors.push('branch: max 255 characters');
    if (!ev.payload) {
        errors.push('payload: required');
        return errors;
    }
    const payload = ev.payload;
    if (ev.eventType === 'push') {
        if (!payload.commitHash)
            errors.push('commitHash: required for push events');
        else if (!COMMIT_HASH.test(String(payload.commitHash)))
            errors.push('commitHash: must be exactly 40 hex characters');
        if (payload.message == null)
            errors.push('message: required for push events');
        if (!payload.author)
            errors.push('author: required for push events');
        if (payload.changedFiles && !Array.isArray(payload.changedFiles))
            errors.push('changedFiles: must be an array');
        if (typeof payload.diff === 'string' && payload.diff.length > 50_000)
            errors.push('diff: max 50000 characters');
    }
    if (ev.eventType === 'developer_note') {
        if (!payload.text)
            errors.push('text: required for developer_note events');
        if (!payload.filePath)
            errors.push('filePath: required for developer_note events');
        else if (String(payload.filePath).includes('..'))
            errors.push('filePath: directory traversal (..) not allowed');
        if (payload.lineNumber == null)
            errors.push('lineNumber: required for developer_note events');
        else if (!Number.isInteger(Number(payload.lineNumber)) || Number(payload.lineNumber) < 0)
            errors.push('lineNumber: must be a non-negative integer');
    }
    return errors;
}
// ── Route: POST /api/v1/projects ────────────────────────────────────────────
async function createProject(body, correlationId) {
    const input = typeof body === 'string' ? JSON.parse(body) : body;
    const errors = validateProjectInput(input);
    if (errors.length)
        return respond(400, { error: 'validation_failed', details: errors }, correlationId);
    const projectId = crypto_1.default.randomUUID();
    const apiToken = crypto_1.default.randomBytes(32).toString('hex');
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
    await dynamo.send(new lib_dynamodb_1.PutCommand({ TableName: PROJECTS_TABLE, Item: record }));
    try {
        await dynamo.send(new lib_dynamodb_1.PutCommand({
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
    }
    catch (err) {
        console.error('[non-fatal] Audit write failed:', err.message);
    }
    return respond(200, { projectId, apiToken }, correlationId);
}
// ── Route: POST /api/v1/events ──────────────────────────────────────────────
async function ingestEvent(headers, body, correlationId) {
    let event;
    try {
        event = typeof body === 'string' ? JSON.parse(body) : body;
    }
    catch {
        return respond(400, { error: 'invalid_json', message: 'Body must be valid JSON' }, correlationId);
    }
    const auth = await authenticate(headers, String(event.projectId));
    if (auth.error)
        return respond(auth.statusCode, auth.error, correlationId);
    // Use shared validation
    const validation = (0, shared_1.validateEvent)(event);
    if (!validation.success) {
        return respond(400, { error: 'validation_failed', details: validation.errors?.map(e => e.message) }, correlationId);
    }
    const eventCorrelationId = event.correlationId || correlationId;
    const receivedAt = new Date().toISOString();
    const t_ingestion_start = Date.now();
    const record = {
        projectId: event.projectId,
        timestampEventId: `${event.timestamp}#${event.eventId}`,
        branchTimestamp: `${event.branch}#${event.timestamp}`,
        eventId: event.eventId,
        eventType: event.eventType,
        branch: event.branch,
        parentBranch: event.payload.parentBranch ?? null,
        payload: event.payload,
        receivedAt,
        processingStatus: 'pending',
        correlationId: eventCorrelationId,
    };
    await dynamo.send(new lib_dynamodb_1.PutCommand({ TableName: EVENTS_TABLE, Item: record }));
    try {
        await s3.send(new client_s3_1.PutObjectCommand({
            Bucket: RAW_EVENTS_BUCKET,
            Key: `raw-events/${event.projectId}/${event.eventId}.json`,
            Body: JSON.stringify(event, null, 2),
            ContentType: 'application/json',
        }));
    }
    catch (err) {
        console.error('[non-fatal] S3 archive failed:', err.message);
    }
    try {
        await lambdaClient.send(new client_lambda_1.InvokeCommand({
            FunctionName: AI_FUNCTION,
            InvocationType: 'Event',
            Payload: Buffer.from(JSON.stringify({
                eventId: event.eventId,
                projectId: event.projectId,
                eventType: event.eventType,
                branch: event.branch,
                parentBranch: event.payload.parentBranch ?? null,
                payload: event.payload,
                timestamp: event.timestamp,
                correlationId: eventCorrelationId,
            })),
        }));
    }
    catch (err) {
        console.error('[non-fatal] AI invoke failed:', err.message);
    }
    if (event.payload.isMerge && event.payload.sourceBranch) {
        try {
            await lambdaClient.send(new client_lambda_1.InvokeCommand({
                FunctionName: AI_FUNCTION,
                InvocationType: 'Event',
                Payload: Buffer.from(JSON.stringify({
                    propagate: true,
                    projectId: event.projectId,
                    sourceBranch: event.payload.sourceBranch,
                    targetBranch: event.branch,
                    timestamp: event.timestamp,
                    correlationId: eventCorrelationId,
                })),
            }));
            console.log(`[merge-propagate] queued propagation: ${event.payload.sourceBranch} → ${event.branch}`);
        }
        catch (err) {
            console.error('[non-fatal] Merge propagation invoke failed:', err.message);
        }
    }
    try {
        await dynamo.send(new lib_dynamodb_1.PutCommand({
            TableName: AUDIT_TABLE,
            Item: {
                entityId: event.eventId,
                timestamp: receivedAt,
                entityType: 'event',
                action: 'created',
                actor: event.payload.author ?? 'system',
                changes: {
                    eventType: event.eventType,
                    branch: event.branch,
                    commitHash: event.payload.commitHash ?? null,
                },
                correlationId: eventCorrelationId,
            },
        }));
    }
    catch (err) {
        console.error('[non-fatal] Audit write failed:', err.message);
    }
    const ingestion_ms = Date.now() - t_ingestion_start;
    console.log(JSON.stringify({ INGESTION_TIMING: true, eventId: event.eventId, ingestion_ms, receivedAt, correlationId: eventCorrelationId }));
    return respond(200, {
        eventId: event.eventId,
        projectId: event.projectId,
        branch: event.branch,
        status: 'processing',
        receivedAt,
        correlationId: eventCorrelationId,
    }, correlationId);
}
// ── Route: GET /api/v1/projects/{projectId} ─────────────────────────────────
async function getProject(headers, pathParameters, correlationId) {
    const { projectId } = pathParameters ?? {};
    if (!projectId)
        return respond(400, { error: 'validation_failed', details: ['projectId: required'] }, correlationId);
    const auth = await authenticate(headers, projectId);
    if (auth.error)
        return respond(auth.statusCode, auth.error, correlationId);
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
// ── Main Handler ────────────────────────────────────────────────────────────
const handler = async (event) => {
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
        if (method === 'OPTIONS')
            return respond(200, { ok: true }, correlationId);
        if (method === 'POST' && resource === '/api/v1/projects')
            return await createProject(event.body ?? '', correlationId);
        if (method === 'POST' && resource === '/api/v1/events')
            return await ingestEvent(event.headers ?? {}, event.body ?? '', correlationId);
        if (method === 'GET' && resource === '/api/v1/projects/{projectId}')
            return await getProject(event.headers ?? {}, event.pathParameters, correlationId);
        return respond(404, { error: 'not_found', message: `No route for ${method} ${resource}` }, correlationId);
    }
    catch (err) {
        console.error(JSON.stringify({
            msg: 'unhandled_error',
            error: err.message,
            stack: err.stack,
            correlationId,
        }));
        return respond(500, { error: 'internal_error', message: 'An unexpected error occurred' }, correlationId);
    }
};
exports.handler = handler;
//# sourceMappingURL=data:application/json;base64,eyJ2ZXJzaW9uIjozLCJmaWxlIjoiaW5kZXguanMiLCJzb3VyY2VSb290IjoiIiwic291cmNlcyI6WyJpbmRleC50cyJdLCJuYW1lcyI6W10sIm1hcHBpbmdzIjoiOzs7Ozs7QUFBQSw4REFFa0M7QUFDbEMsd0RBSStCO0FBQy9CLGtEQUc0QjtBQUM1QiwwREFHZ0M7QUFDaEMsb0RBQTRCO0FBQzVCLDZDQVEwQjtBQUUxQiw4RUFBOEU7QUFDOUUsTUFBTSxjQUFjLEdBQUcsT0FBTyxDQUFDLEdBQUcsQ0FBQyxjQUFjLENBQUMsQ0FBZSxvQkFBb0I7QUFDckYsTUFBTSxZQUFZLEdBQUcsT0FBTyxDQUFDLEdBQUcsQ0FBQyxZQUFZLENBQUMsQ0FBaUIsa0JBQWtCO0FBQ2pGLE1BQU0sV0FBVyxHQUFHLE9BQU8sQ0FBQyxHQUFHLENBQUMsV0FBVyxDQUFDLENBQW1CLGlCQUFpQjtBQUNoRixNQUFNLGlCQUFpQixHQUFHLE9BQU8sQ0FBQyxHQUFHLENBQUMsaUJBQWlCLENBQUMsQ0FBTyxnQ0FBZ0M7QUFDL0YsTUFBTSxXQUFXLEdBQUcsT0FBTyxDQUFDLEdBQUcsQ0FBQywyQkFBMkIsQ0FBQyxDQUFHLHlCQUF5QjtBQUV4RiwrRUFBK0U7QUFDL0UsTUFBTSxNQUFNLEdBQUcscUNBQXNCLENBQUMsSUFBSSxDQUN4QyxJQUFJLGdDQUFjLENBQUMsRUFBRSxNQUFNLEVBQUUsV0FBVyxFQUFFLENBQUMsRUFDM0MsRUFBRSxlQUFlLEVBQUUsRUFBRSxxQkFBcUIsRUFBRSxJQUFJLEVBQUUsRUFBRSxDQUNyRCxDQUFDO0FBQ0YsTUFBTSxFQUFFLEdBQUcsSUFBSSxvQkFBUSxDQUFDLEVBQUUsTUFBTSxFQUFFLFdBQVcsRUFBRSxDQUFDLENBQUM7QUFDakQsTUFBTSxZQUFZLEdBQUcsSUFBSSw0QkFBWSxDQUFDLEVBQUUsTUFBTSxFQUFFLFdBQVcsRUFBRSxDQUFDLENBQUM7QUFFL0QsK0VBQStFO0FBQy9FLFNBQVMscUJBQXFCO0lBQzVCLE9BQU8sZ0JBQU0sQ0FBQyxVQUFVLEVBQUUsQ0FBQztBQUM3QixDQUFD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RyxDQUFDO0lBQUMsT0FBTyxHQUFHLEVBQUUsQ0FBQztRQUNiLE9BQU8sQ0FBQyxLQUFLLENBQUMsSUFBSSxDQUFDLFNBQVMsQ0FBQztZQUMzQixHQUFHLEVBQUUsaUJBQWlCO1lBQ3RCLEtBQUssRUFBRyxHQUFhLENBQUMsT0FBTztZQUM3QixLQUFLLEVBQUcsR0FBYSxDQUFDLEtBQUs7WUFDM0IsYUFBYTtTQUNkLENBQUMsQ0FBQyxDQUFDO1FBQ0osT0FBTyxPQUFPLENBQUMsR0FBRyxFQUFFLEVBQUUsS0FBSyxFQUFFLGdCQUFnQixFQUFFLE9BQU8sRUFBRSw4QkFBOEIsRUFBRSxFQUFFLGFBQWEsQ0FBQyxDQUFDO0lBQzNHLENBQUM7QUFDSCxDQUFDLENBQUM7QUExQ1csUUFBQSxPQUFPLFdBMENsQiIsInNvdXJjZXNDb250ZW50IjpbImltcG9ydCB7XG4gIER5bmFtb0RCQ2xpZW50LFxufSBmcm9tICdAYXdzLXNkay9jbGllbnQtZHluYW1vZGInO1xuaW1wb3J0IHtcbiAgRHluYW1vREJEb2N1bWVudENsaWVudCxcbiAgUHV0Q29tbWFuZCxcbiAgR2V0Q29tbWFuZCxcbn0gZnJvbSAnQGF3cy1zZGsvbGliLWR5bmFtb2RiJztcbmltcG9ydCB7XG4gIFMzQ2xpZW50LFxuICBQdXRPYmplY3RDb21tYW5kLFxufSBmcm9tICdAYXdzLXNkay9jbGllbnQtczMnO1xuaW1wb3J0IHtcbiAgTGFtYmRhQ2xpZW50LFxuICBJbnZva2VDb21tYW5kLFxufSBmcm9tICdAYXdzLXNkay9jbGllbnQtbGFtYmRhJztcbmltcG9ydCBjcnlwdG8gZnJvbSAnY3J5cHRvJztcbmltcG9ydCB7XG4gIEZsb3dTeW5jRXZlbnQsXG4gIEV2ZW50VHlwZSxcbiAgRXZlbnRTb3VyY2UsXG4gIHZhbGlkYXRlRXZlbnQsXG4gIGNyZWF0ZUV2ZW50LFxuICBQdXNoRXZlbnRQYXlsb2FkLFxuICBBY3Rvcixcbn0gZnJvbSAnQGZsb3dzeW5jL3NoYXJlZCc7XG5cbi8vIOKUgOKUgCBDb25maWcg4oCUIGZyb20gTGFtYmRhIGVudmlyb25tZW50IHZhcmlhYmxlcyBzZXQgYnkgQ0RLIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgFxuY29uc3QgUFJPSkVDVFNfVEFCTEUgPSBwcm9jZXNzLmVudi5QUk9KRUNUU19UQUJMRTsgICAgICAgICAgICAgICAvLyBmbG93c3luYy1wcm9qZWN0c1xuY29uc3QgRVZFTlRTX1RBQkxFID0gcHJvY2Vzcy5lbnYuRVZFTlRTX1RBQkxFOyAgICAgICAgICAgICAgICAgLy8gZmxvd3N5bmMtZXZlbnRzXG5jb25zdCBBVURJVF9UQUJMRSA9IHByb2Nlc3MuZW52LkFVRElUX1RBQkxFOyAgICAgICAgICAgICAgICAgICAvLyBmbG93c3luYy1hdWRpdFxuY29uc3QgUkFXX0VWRU5UU19CVUNLRVQgPSBwcm9jZXNzLmVudi5SQVdfRVZFTlRTX0JVQ0tFVDsgICAgICAgLy8gZmxvd3N5bmMtcmF3LWV2ZW50cy17YWNjb3VudH1cbmNvbnN0IEFJX0ZVTkNUSU9OID0gcHJvY2Vzcy5lbnYuQUlfUFJPQ0VTU0lOR19GVU5DVElPTl9OQU1FOyAgIC8vIGZsb3dzeW5jLWFpLXByb2Nlc3NpbmdcblxuLy8g4pSA4pSAIFNpbmdsZXRvbiBTREsgY2xpZW50cyDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIBcbmNvbnN0IGR5bmFtbyA9IER5bmFtb0RCRG9jdW1lbnRDbGllbnQuZnJvbShcbiAgbmV3IER5bmFtb0RCQ2xpZW50KHsgcmVnaW9uOiAndXMtZWFzdC0xJyB9KSxcbiAgeyBtYXJzaGFsbE9wdGlvbnM6IHsgcmVtb3ZlVW5kZWZpbmVkVmFsdWVzOiB0cnVlIH0gfSxcbik7XG5jb25zdCBzMyA9IG5ldyBTM0NsaWVudCh7IHJlZ2lvbjogJ3VzLWVhc3QtMScgfSk7XG5jb25zdCBsYW1iZGFDbGllbnQgPSBuZXcgTGFtYmRhQ2xpZW50KHsgcmVnaW9uOiAndXMtZWFzdC0xJyB9KTtcblxuLy8g4pSA4pSAIENvcnJlbGF0aW9uIElEIHN1cHBvcnQg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSAXG5mdW5jdGlvbiBnZW5lcmF0ZUNvcnJlbGF0aW9uSWQoKTogc3RyaW5nIHtcbiAgcmV0dXJuIGNyeXB0by5yYW5kb21VVUlEKCk7XG59XG5cbmZ1bmN0aW9uIGV4dHJhY3RDb3JyZWxhdGlvbklkKGhlYWRlcnM6IFJlY29yZDxzdHJpbmcsIHN0cmluZyB8IHVuZGVmaW5lZD4pOiBzdHJpbmcge1xuICBpZiAoIWhlYWRlcnMpIHJldHVybiBnZW5lcmF0ZUNvcnJlbGF0aW9uSWQoKTtcbiAgY29uc3QgaGVhZGVyS2V5cyA9IE9iamVjdC5rZXlzKGhlYWRlcnMpLm1hcChrID0+IGsudG9Mb3dlckNhc2UoKSk7XG4gIGNvbnN0IGNvcnJJZHggPSBoZWFkZXJLZXlzLmluZGV4T2YoJ3gtY29ycmVsYXRpb24taWQnKTtcbiAgY29uc3QgcmVxSWR4ID0gaGVhZGVyS2V5cy5pbmRleE9mKCd4LXJlcXVlc3QtaWQnKTtcbiAgY29uc3Qgb3JpZ2luYWxLZXlzID0gT2JqZWN0LmtleXMoaGVhZGVycyk7XG4gIGlmIChjb3JySWR4ICE9PSAtMSkgcmV0dXJuIGhlYWRlcnNbb3JpZ2luYWxLZXlzW2NvcnJJZHhdXSB8fCBnZW5lcmF0ZUNvcnJlbGF0aW9uSWQoKTtcbiAgaWYgKHJlcUlkeCAhPT0gLTEpIHJldHVybiBoZWFkZXJzW29yaWdpbmFsS2V5c1tyZXFJZHhdXSB8fCBnZW5lcmF0ZUNvcnJlbGF0aW9uSWQoKTtcbiAgcmV0dXJuIGdlbmVyYXRlQ29ycmVsYXRpb25JZCgpO1xufVxuXG4vLyDilIDilIAgVG9rZW4gSGVscGVycyAoc2NyeXB0IEtERikg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSAXG5mdW5jdGlvbiBoYXNoVG9rZW4ocGxhaW50ZXh0OiBzdHJpbmcpOiBzdHJpbmcge1xuICBjb25zdCBzYWx0ID0gY3J5cHRvLnJhbmRvbUJ5dGVzKDE2KS50b1N0cmluZygnaGV4Jyk7XG4gIGNvbnN0IGhhc2ggPSBjcnlwdG8uc2NyeXB0U3luYyhwbGFpbnRleHQsIHNhbHQsIDY0KS50b1N0cmluZygnaGV4Jyk7XG4gIHJldHVybiBgJHtzYWx0fToke2hhc2h9YDtcbn1cblxuZnVuY3Rpb24gdmVyaWZ5VG9rZW4ocGxhaW50ZXh0OiBzdHJpbmcsIHN0b3JlZDogc3RyaW5nKTogYm9vbGVhbiB7XG4gIGNvbnN0IFtzYWx0LCBoYXNoXSA9IChzdG9yZWQgPz8gJycpLnNwbGl0KCc6Jyk7XG4gIGlmICghc2FsdCB8fCAhaGFzaCkgcmV0dXJuIGZhbHNlO1xuICBjb25zdCBjYW5kaWRhdGUgPSBjcnlwdG8uc2NyeXB0U3luYyhwbGFpbnRleHQsIHNhbHQsIDY0KS50b1N0cmluZygnaGV4Jyk7XG4gIHJldHVybiBjcnlwdG8udGltaW5nU2FmZUVxdWFsKEJ1ZmZlci5mcm9tKGhhc2gsICdoZXgnKSwgQnVmZmVyLmZyb20oY2FuZGlkYXRlLCAnaGV4JykpO1xufVxuXG4vLyDilIDilIAgUmVzcG9uc2UgQnVpbGRlciDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIBcbmZ1bmN0aW9uIHJlc3BvbmQoc3RhdHVzQ29kZTogbnVtYmVyLCBib2R5OiBSZWNvcmQ8c3RyaW5nLCB1bmtub3duPiwgY29ycmVsYXRpb25JZD86IHN0cmluZykge1xuICBjb25zdCBoZWFkZXJzOiBSZWNvcmQ8c3RyaW5nLCBzdHJpbmc+ID0ge1xuICAgICdDb250ZW50LVR5cGUnOiAnYXBwbGljYXRpb24vanNvbicsXG4gICAgJ0FjY2Vzcy1Db250cm9sLUFsbG93LU9yaWdpbic6ICcqJyxcbiAgICAnQWNjZXNzLUNvbnRyb2wtQWxsb3ctSGVhZGVycyc6ICdDb250ZW50LVR5cGUsQXV0aG9yaXphdGlvbicsXG4gIH07XG4gIGlmIChjb3JyZWxhdGlvbklkKSB7XG4gICAgaGVhZGVyc1sneC1jb3JyZWxhdGlvbi1pZCddID0gY29ycmVsYXRpb25JZDtcbiAgfVxuICByZXR1cm4ge1xuICAgIHN0YXR1c0NvZGUsXG4gICAgaGVhZGVycyxcbiAgICBib2R5OiBKU09OLnN0cmluZ2lmeShib2R5KSxcbiAgfTtcbn1cblxuLy8g4pSA4pSAIEF1dGgg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSAXG5mdW5jdGlvbiBleHRyYWN0VG9rZW4oaGVhZGVyczogUmVjb3JkPHN0cmluZywgc3RyaW5nIHwgdW5kZWZpbmVkPik6IHN0cmluZyB8IG51bGwge1xuICBjb25zdCByYXcgPSBoZWFkZXJzPy5BdXRob3JpemF0aW9uID8/IGhlYWRlcnM/LmF1dGhvcml6YXRpb247XG4gIGlmICghcmF3KSByZXR1cm4gbnVsbDtcbiAgY29uc3QgW3NjaGVtZSwgdG9rZW5dID0gcmF3LnNwbGl0KCcgJyk7XG4gIHJldHVybiBzY2hlbWUgPT09ICdCZWFyZXInICYmIHRva2VuID8gdG9rZW4gOiBudWxsO1xufVxuXG5hc3luYyBmdW5jdGlvbiBhdXRoZW50aWNhdGUoaGVhZGVyczogUmVjb3JkPHN0cmluZywgc3RyaW5nIHwgdW5kZWZpbmVkPiwgcHJvamVjdElkOiBzdHJpbmcpIHtcbiAgY29uc3QgdG9rZW4gPSBleHRyYWN0VG9rZW4oaGVhZGVycyk7XG4gIGlmICghdG9rZW4pIHJldHVybiB7IGVycm9yOiB7IGVycm9yOiAnaW52YWxpZF90b2tlbicsIG1lc3NhZ2U6ICdNaXNzaW5nIEF1dGhvcml6YXRpb24gaGVhZGVyJyB9LCBzdGF0dXNDb2RlOiA0MDEgfTtcblxuICBjb25zdCB7IEl0ZW0gfSA9IGF3YWl0IGR5bmFtby5zZW5kKG5ldyBHZXRDb21tYW5kKHsgVGFibGVOYW1lOiBQUk9KRUNUU19UQUJMRSwgS2V5OiB7IHByb2plY3RJZCB9IH0pKTtcblxuICBpZiAoIUl0ZW0/LmFwaVRva2VuSGFzaCkgcmV0dXJuIHsgZXJyb3I6IHsgZXJyb3I6ICdpbnZhbGlkX3Rva2VuJyB9LCBzdGF0dXNDb2RlOiA0MDEgfTtcbiAgaWYgKCF2ZXJpZnlUb2tlbih0b2tlbiwgSXRlbS5hcGlUb2tlbkhhc2gpKSByZXR1cm4geyBlcnJvcjogeyBlcnJvcjogJ2ludmFsaWRfdG9rZW4nIH0sIHN0YXR1c0NvZGU6IDQwMSB9O1xuXG4gIHJldHVybiB7IHByb2plY3Q6IEl0ZW0gfTtcbn1cblxuLy8g4pSA4pSAIFZhbGlkYXRpb24g4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSAXG5jb25zdCBVVUlEX1Y0ID0gL15bMC05YS1mXXs4fS1bMC05YS1mXXs0fS00WzAtOWEtZl17M30tWzg5YWJdWzAtOWEtZl17M30tWzAtOWEtZl17MTJ9JC9pO1xuY29uc3QgQ09NTUlUX0hBU0ggPSAvXlswLTlhLWZdezQwfSQvaTtcbmNvbnN0IElTT184NjAxID0gL15cXGR7NH0tXFxkezJ9LVxcZHsyfVRcXGR7Mn06XFxkezJ9OlxcZHsyfShcXC5cXGQrKT8oWnxbKy1dXFxkezJ9Oj9cXGR7Mn0pJC87XG5jb25zdCBOQU1FX1JFR0VYID0gL15bYS16QS1aMC05Xy1dKyQvO1xuXG5mdW5jdGlvbiB2YWxpZGF0ZVByb2plY3RJbnB1dChpbnB1dDogUmVjb3JkPHN0cmluZywgdW5rbm93bj4pOiBzdHJpbmdbXSB7XG4gIGNvbnN0IGVycm9yczogc3RyaW5nW10gPSBbXTtcbiAgaWYgKCFpbnB1dC5uYW1lKSAgICAgICAgICAgICAgICAgICAgICAgICAgZXJyb3JzLnB1c2goJ25hbWU6IHJlcXVpcmVkJyk7XG4gIGVsc2UgaWYgKCFOQU1FX1JFR0VYLnRlc3QoU3RyaW5nKGlucHV0Lm5hbWUpKSkgICAgZXJyb3JzLnB1c2goJ25hbWU6IGFscGhhbnVtZXJpYywgaHlwaGVucywgdW5kZXJzY29yZXMgb25seScpO1xuICBpZiAoIWlucHV0LmRlc2NyaXB0aW9uKSAgICAgICAgICAgICAgICAgICBlcnJvcnMucHVzaCgnZGVzY3JpcHRpb246IHJlcXVpcmVkJyk7XG4gIGlmICghQXJyYXkuaXNBcnJheShpbnB1dC5sYW5ndWFnZXMpIHx8IGlucHV0Lmxhbmd1YWdlcy5sZW5ndGggPT09IDApXG4gICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIGVycm9ycy5wdXNoKCdsYW5ndWFnZXM6IHJlcXVpcmVkLCBtdXN0IGJlIGEgbm9uLWVtcHR5IGFycmF5Jyk7XG4gIGlmICghaW5wdXQuZGVmYXVsdEJyYW5jaCkgICAgICAgICAgICAgICAgIGVycm9ycy5wdXNoKCdkZWZhdWx0QnJhbmNoOiByZXF1aXJlZCcpO1xuICBpZiAoaW5wdXQuZnJhbWV3b3JrcyAgJiYgIUFycmF5LmlzQXJyYXkoaW5wdXQuZnJhbWV3b3JrcykpICBlcnJvcnMucHVzaCgnZnJhbWV3b3JrczogbXVzdCBiZSBhbiBhcnJheScpO1xuICBpZiAoaW5wdXQudGVhbU1lbWJlcnMgJiYgIUFycmF5LmlzQXJyYXkoaW5wdXQudGVhbU1lbWJlcnMpKSBlcnJvcnMucHVzaCgndGVhbU1lbWJlcnM6IG11c3QgYmUgYW4gYXJyYXknKTtcbiAgZWxzZSBpZiAoQXJyYXkuaXNBcnJheShpbnB1dC50ZWFtTWVtYmVycykpIHtcbiAgICBpbnB1dC50ZWFtTWVtYmVycy5mb3JFYWNoKChtOiBSZWNvcmQ8c3RyaW5nLCB1bmtub3duPiwgaTogbnVtYmVyKSA9PiB7XG4gICAgICBpZiAoIW0ubmFtZSkgZXJyb3JzLnB1c2goYHRlYW1NZW1iZXJzWyR7aX1dLm5hbWU6IHJlcXVpcmVkYCk7XG4gICAgICBpZiAoIW0ucm9sZSkgZXJyb3JzLnB1c2goYHRlYW1NZW1iZXJzWyR7aX1dLnJvbGU6IHJlcXVpcmVkYCk7XG4gICAgfSk7XG4gIH1cbiAgcmV0dXJuIGVycm9ycztcbn1cblxuZnVuY3Rpb24gdmFsaWRhdGVFdmVudExvY2FsKGV2OiBSZWNvcmQ8c3RyaW5nLCB1bmtub3duPik6IHN0cmluZ1tdIHtcbiAgY29uc3QgZXJyb3JzOiBzdHJpbmdbXSA9IFtdO1xuICBpZiAoIWV2LmV2ZW50SWQpICAgICAgICAgICAgICAgICAgICAgICAgICBlcnJvcnMucHVzaCgnZXZlbnRJZDogcmVxdWlyZWQnKTtcbiAgZWxzZSBpZiAoIVVVSURfVjQudGVzdChTdHJpbmcoZXYuZXZlbnRJZCkpKSAgICAgICBlcnJvcnMucHVzaCgnZXZlbnRJZDogbXVzdCBiZSBhIHZhbGlkIFVVSUQgdjQnKTtcbiAgaWYgKCFldi5wcm9qZWN0SWQpICAgICAgICAgICAgICAgICAgICAgICAgZXJyb3JzLnB1c2goJ3Byb2plY3RJZDogcmVxdWlyZWQnKTtcbiAgaWYgKCFbJ3B1c2gnLCAnZGV2ZWxvcGVyX25vdGUnXS5pbmNsdWRlcyhTdHJpbmcoZXYuZXZlbnRUeXBlKSkpXG4gICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIGVycm9ycy5wdXNoKCdldmVudFR5cGU6IG11c3QgYmUgcHVzaCBvciBkZXZlbG9wZXJfbm90ZScpO1xuICBpZiAoIWV2LnRpbWVzdGFtcCkgICAgICAgICAgICAgICAgICAgICAgICBlcnJvcnMucHVzaCgndGltZXN0YW1wOiByZXF1aXJlZCcpO1xuICBlbHNlIGlmICghSVNPXzg2MDEudGVzdChTdHJpbmcoZXYudGltZXN0YW1wKSkpICAgIGVycm9ycy5wdXNoKCd0aW1lc3RhbXA6IG11c3QgYmUgdmFsaWQgSVNPIDg2MDEgVVRDJyk7XG4gIGlmICghZXYuYnJhbmNoKSAgICAgICAgICAgICAgICAgICAgICAgICAgIGVycm9ycy5wdXNoKCdicmFuY2g6IHJlcXVpcmVkJyk7XG4gIGVsc2UgaWYgKFN0cmluZyhldi5icmFuY2gpLmxlbmd0aCA+IDI1NSkgICAgICAgICAgZXJyb3JzLnB1c2goJ2JyYW5jaDogbWF4IDI1NSBjaGFyYWN0ZXJzJyk7XG5cbiAgaWYgKCFldi5wYXlsb2FkKSB7IGVycm9ycy5wdXNoKCdwYXlsb2FkOiByZXF1aXJlZCcpOyByZXR1cm4gZXJyb3JzOyB9XG5cbiAgY29uc3QgcGF5bG9hZCA9IGV2LnBheWxvYWQgYXMgUmVjb3JkPHN0cmluZywgdW5rbm93bj47XG5cbiAgaWYgKGV2LmV2ZW50VHlwZSA9PT0gJ3B1c2gnKSB7XG4gICAgaWYgKCFwYXlsb2FkLmNvbW1pdEhhc2gpICAgICAgICAgICAgICAgICAgICAgIGVycm9ycy5wdXNoKCdjb21taXRIYXNoOiByZXF1aXJlZCBmb3IgcHVzaCBldmVudHMnKTtcbiAgICBlbHNlIGlmICghQ09NTUlUX0hBU0gudGVzdChTdHJpbmcocGF5bG9hZC5jb21taXRIYXNoKSkpIGVycm9ycy5wdXNoKCdjb21taXRIYXNoOiBtdXN0IGJlIGV4YWN0bHkgNDAgaGV4IGNoYXJhY3RlcnMnKTtcbiAgICBpZiAocGF5bG9hZC5tZXNzYWdlID09IG51bGwpICAgICAgICAgICAgICAgICAgZXJyb3JzLnB1c2goJ21lc3NhZ2U6IHJlcXVpcmVkIGZvciBwdXNoIGV2ZW50cycpO1xuICAgIGlmICghcGF5bG9hZC5hdXRob3IpICAgICAgICAgICAgICAgICAgICAgICAgICBlcnJvcnMucHVzaCgnYXV0aG9yOiByZXF1aXJlZCBmb3IgcHVzaCBldmVudHMnKTtcbiAgICBpZiAocGF5bG9hZC5jaGFuZ2VkRmlsZXMgJiYgIUFycmF5LmlzQXJyYXkocGF5bG9hZC5jaGFuZ2VkRmlsZXMpKSBlcnJvcnMucHVzaCgnY2hhbmdlZEZpbGVzOiBtdXN0IGJlIGFuIGFycmF5Jyk7XG4gICAgaWYgKHR5cGVvZiBwYXlsb2FkLmRpZmYgPT09ICdzdHJpbmcnICYmIHBheWxvYWQuZGlmZi5sZW5ndGggPiA1MF8wMDApIGVycm9ycy5wdXNoKCdkaWZmOiBtYXggNTAwMDAgY2hhcmFjdGVycycpO1xuICB9XG5cbiAgaWYgKGV2LmV2ZW50VHlwZSA9PT0gJ2RldmVsb3Blcl9ub3RlJykge1xuICAgIGlmICghcGF5bG9hZC50ZXh0KSAgICAgICAgICAgICAgICAgICAgICAgICAgICBlcnJvcnMucHVzaCgndGV4dDogcmVxdWlyZWQgZm9yIGRldmVsb3Blcl9ub3RlIGV2ZW50cycpO1xuICAgIGlmICghcGF5bG9hZC5maWxlUGF0aCkgICAgICAgICAgICAgICAgICAgICAgICBlcnJvcnMucHVzaCgnZmlsZVBhdGg6IHJlcXVpcmVkIGZvciBkZXZlbG9wZXJfbm90ZSBldmVudHMnKTtcbiAgICBlbHNlIGlmIChTdHJpbmcocGF5bG9hZC5maWxlUGF0aCkuaW5jbHVkZXMoJy4uJykpICAgICBlcnJvcnMucHVzaCgnZmlsZVBhdGg6IGRpcmVjdG9yeSB0cmF2ZXJzYWwgKC4uKSBub3QgYWxsb3dlZCcpO1xuICAgIGlmIChwYXlsb2FkLmxpbmVOdW1iZXIgPT0gbnVsbCkgICAgICAgICAgICAgICBlcnJvcnMucHVzaCgnbGluZU51bWJlcjogcmVxdWlyZWQgZm9yIGRldmVsb3Blcl9ub3RlIGV2ZW50cycpO1xuICAgIGVsc2UgaWYgKCFOdW1iZXIuaXNJbnRlZ2VyKE51bWJlcihwYXlsb2FkLmxpbmVOdW1iZXIpKSB8fCBOdW1iZXIocGF5bG9hZC5saW5lTnVtYmVyKSA8IDApXG4gICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIGVycm9ycy5wdXNoKCdsaW5lTnVtYmVyOiBtdXN0IGJlIGEgbm9uLW5lZ2F0aXZlIGludGVnZXInKTtcbiAgfVxuXG4gIHJldHVybiBlcnJvcnM7XG59XG5cbi8vIOKUgOKUgCBSb3V0ZTogUE9TVCAvYXBpL3YxL3Byb2plY3RzIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgFxuYXN5bmMgZnVuY3Rpb24gY3JlYXRlUHJvamVjdChib2R5OiBzdHJpbmcgfCBSZWNvcmQ8c3RyaW5nLCB1bmtub3duPiwgY29ycmVsYXRpb25JZDogc3RyaW5nKSB7XG4gIGNvbnN0IGlucHV0ID0gdHlwZW9mIGJvZHkgPT09ICdzdHJpbmcnID8gSlNPTi5wYXJzZShib2R5KSA6IGJvZHk7XG5cbiAgY29uc3QgZXJyb3JzID0gdmFsaWRhdGVQcm9qZWN0SW5wdXQoaW5wdXQpO1xuICBpZiAoZXJyb3JzLmxlbmd0aCkgcmV0dXJuIHJlc3BvbmQoNDAwLCB7IGVycm9yOiAndmFsaWRhdGlvbl9mYWlsZWQnLCBkZXRhaWxzOiBlcnJvcnMgfSwgY29ycmVsYXRpb25JZCk7XG5cbiAgY29uc3QgcHJvamVjdElkID0gY3J5cHRvLnJhbmRvbVVVSUQoKTtcbiAgY29uc3QgYXBpVG9rZW4gPSBjcnlwdG8ucmFuZG9tQnl0ZXMoMzIpLnRvU3RyaW5nKCdoZXgnKTtcbiAgY29uc3QgYXBpVG9rZW5IYXNoID0gaGFzaFRva2VuKGFwaVRva2VuKTtcblxuICBjb25zdCBub3cgPSBuZXcgRGF0ZSgpLnRvSVNPU3RyaW5nKCk7XG4gIGNvbnN0IHJlY29yZCA9IHtcbiAgICBwcm9qZWN0SWQsXG4gICAgbmFtZTogaW5wdXQubmFtZSxcbiAgICBkZXNjcmlwdGlvbjogaW5wdXQuZGVzY3JpcHRpb24sXG4gICAgbGFuZ3VhZ2VzOiBpbnB1dC5sYW5ndWFnZXMsXG4gICAgZnJhbWV3b3JrczogaW5wdXQuZnJhbWV3b3JrcyA/PyBbXSxcbiAgICBkZWZhdWx0QnJhbmNoOiBpbnB1dC5kZWZhdWx0QnJhbmNoLFxuICAgIHRlYW1NZW1iZXJzOiBpbnB1dC50ZWFtTWVtYmVycyA/PyBbXSxcbiAgICBhcGlUb2tlbkhhc2gsXG4gICAgY3JlYXRlZEF0OiBub3csXG4gICAgbGFzdEFjdGl2aXR5QXQ6IG5vdyxcbiAgICBldmVudENvdW50OiAwLFxuICB9O1xuXG4gIGF3YWl0IGR5bmFtby5zZW5kKG5ldyBQdXRDb21tYW5kKHsgVGFibGVOYW1lOiBQUk9KRUNUU19UQUJMRSwgSXRlbTogcmVjb3JkIH0pKTtcblxuICB0cnkge1xuICAgIGF3YWl0IGR5bmFtby5zZW5kKG5ldyBQdXRDb21tYW5kKHtcbiAgICAgIFRhYmxlTmFtZTogQVVESVRfVEFCTEUsXG4gICAgICBJdGVtOiB7XG4gICAgICAgIGVudGl0eUlkOiBwcm9qZWN0SWQsXG4gICAgICAgIHRpbWVzdGFtcDogbm93LFxuICAgICAgICBlbnRpdHlUeXBlOiAncHJvamVjdCcsXG4gICAgICAgIGFjdGlvbjogJ2NyZWF0ZWQnLFxuICAgICAgICBhY3RvcjogJ3N5c3RlbScsXG4gICAgICAgIGNoYW5nZXM6IHsgbmFtZTogaW5wdXQubmFtZSwgZGVmYXVsdEJyYW5jaDogaW5wdXQuZGVmYXVsdEJyYW5jaCB9LFxuICAgICAgICByZWFzb246ICdQcm9qZWN0IGluaXRpYWxpc2VkIHZpYSBvbmJvYXJkaW5nIHdpemFyZCcsXG4gICAgICAgIGNvcnJlbGF0aW9uSWQsXG4gICAgICB9LFxuICAgIH0pKTtcbiAgfSBjYXRjaCAoZXJyKSB7IGNvbnNvbGUuZXJyb3IoJ1tub24tZmF0YWxdIEF1ZGl0IHdyaXRlIGZhaWxlZDonLCAoZXJyIGFzIEVycm9yKS5tZXNzYWdlKTsgfVxuXG4gIHJldHVybiByZXNwb25kKDIwMCwgeyBwcm9qZWN0SWQsIGFwaVRva2VuIH0sIGNvcnJlbGF0aW9uSWQpO1xufVxuXG4vLyDilIDilIAgUm91dGU6IFBPU1QgL2FwaS92MS9ldmVudHMg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSAXG5hc3luYyBmdW5jdGlvbiBpbmdlc3RFdmVudChoZWFkZXJzOiBSZWNvcmQ8c3RyaW5nLCBzdHJpbmcgfCB1bmRlZmluZWQ+LCBib2R5OiBzdHJpbmcgfCBSZWNvcmQ8c3RyaW5nLCB1bmtub3duPiwgY29ycmVsYXRpb25JZDogc3RyaW5nKSB7XG4gIGxldCBldmVudDogUmVjb3JkPHN0cmluZywgdW5rbm93bj47XG4gIHRyeSAgIHsgZXZlbnQgPSB0eXBlb2YgYm9keSA9PT0gJ3N0cmluZycgPyBKU09OLnBhcnNlKGJvZHkpIDogYm9keTsgfVxuICBjYXRjaCB7IHJldHVybiByZXNwb25kKDQwMCwgeyBlcnJvcjogJ2ludmFsaWRfanNvbicsIG1lc3NhZ2U6ICdCb2R5IG11c3QgYmUgdmFsaWQgSlNPTicgfSwgY29ycmVsYXRpb25JZCk7IH1cblxuICBjb25zdCBhdXRoID0gYXdhaXQgYXV0aGVudGljYXRlKGhlYWRlcnMsIFN0cmluZyhldmVudC5wcm9qZWN0SWQpKTtcbiAgaWYgKGF1dGguZXJyb3IpIHJldHVybiByZXNwb25kKGF1dGguc3RhdHVzQ29kZSwgYXV0aC5lcnJvciwgY29ycmVsYXRpb25JZCk7XG5cbiAgLy8gVXNlIHNoYXJlZCB2YWxpZGF0aW9uXG4gIGNvbnN0IHZhbGlkYXRpb24gPSB2YWxpZGF0ZUV2ZW50KGV2ZW50IGFzIHVua25vd24pO1xuICBpZiAoIXZhbGlkYXRpb24uc3VjY2Vzcykge1xuICAgIHJldHVybiByZXNwb25kKDQwMCwgeyBlcnJvcjogJ3ZhbGlkYXRpb25fZmFpbGVkJywgZGV0YWlsczogdmFsaWRhdGlvbi5lcnJvcnM/Lm1hcChlID0+IGUubWVzc2FnZSkgfSwgY29ycmVsYXRpb25JZCk7XG4gIH1cblxuICBjb25zdCBldmVudENvcnJlbGF0aW9uSWQgPSAoZXZlbnQuY29ycmVsYXRpb25JZCBhcyBzdHJpbmcpIHx8IGNvcnJlbGF0aW9uSWQ7XG5cbiAgY29uc3QgcmVjZWl2ZWRBdCA9IG5ldyBEYXRlKCkudG9JU09TdHJpbmcoKTtcbiAgY29uc3QgdF9pbmdlc3Rpb25fc3RhcnQgPSBEYXRlLm5vdygpO1xuXG4gIGNvbnN0IHJlY29yZCA9IHtcbiAgICBwcm9qZWN0SWQ6IGV2ZW50LnByb2plY3RJZCxcbiAgICB0aW1lc3RhbXBFdmVudElkOiBgJHtldmVudC50aW1lc3RhbXB9IyR7ZXZlbnQuZXZlbnRJZH1gLFxuICAgIGJyYW5jaFRpbWVzdGFtcDogYCR7ZXZlbnQuYnJhbmNofSMke2V2ZW50LnRpbWVzdGFtcH1gLFxuICAgIGV2ZW50SWQ6IGV2ZW50LmV2ZW50SWQsXG4gICAgZXZlbnRUeXBlOiBldmVudC5ldmVudFR5cGUsXG4gICAgYnJhbmNoOiBldmVudC5icmFuY2gsXG4gICAgcGFyZW50QnJhbmNoOiAoZXZlbnQucGF5bG9hZCBhcyBSZWNvcmQ8c3RyaW5nLCB1bmtub3duPikucGFyZW50QnJhbmNoID8/IG51bGwsXG4gICAgcGF5bG9hZDogZXZlbnQucGF5bG9hZCxcbiAgICByZWNlaXZlZEF0LFxuICAgIHByb2Nlc3NpbmdTdGF0dXM6ICdwZW5kaW5nJyxcbiAgICBjb3JyZWxhdGlvbklkOiBldmVudENvcnJlbGF0aW9uSWQsXG4gIH07XG5cbiAgYXdhaXQgZHluYW1vLnNlbmQobmV3IFB1dENvbW1hbmQoeyBUYWJsZU5hbWU6IEVWRU5UU19UQUJMRSwgSXRlbTogcmVjb3JkIH0pKTtcblxuICB0cnkge1xuICAgIGF3YWl0IHMzLnNlbmQobmV3IFB1dE9iamVjdENvbW1hbmQoe1xuICAgICAgQnVja2V0OiBSQVdfRVZFTlRTX0JVQ0tFVCxcbiAgICAgIEtleTogYHJhdy1ldmVudHMvJHtldmVudC5wcm9qZWN0SWR9LyR7ZXZlbnQuZXZlbnRJZH0uanNvbmAsXG4gICAgICBCb2R5OiBKU09OLnN0cmluZ2lmeShldmVudCwgbnVsbCwgMiksXG4gICAgICBDb250ZW50VHlwZTogJ2FwcGxpY2F0aW9uL2pzb24nLFxuICAgIH0pKTtcbiAgfSBjYXRjaCAoZXJyKSB7IGNvbnNvbGUuZXJyb3IoJ1tub24tZmF0YWxdIFMzIGFyY2hpdmUgZmFpbGVkOicsIChlcnIgYXMgRXJyb3IpLm1lc3NhZ2UpOyB9XG5cbiAgdHJ5IHtcbiAgICBhd2FpdCBsYW1iZGFDbGllbnQuc2VuZChuZXcgSW52b2tlQ29tbWFuZCh7XG4gICAgICBGdW5jdGlvbk5hbWU6IEFJX0ZVTkNUSU9OLFxuICAgICAgSW52b2NhdGlvblR5cGU6ICdFdmVudCcsXG4gICAgICBQYXlsb2FkOiBCdWZmZXIuZnJvbShKU09OLnN0cmluZ2lmeSh7XG4gICAgICAgIGV2ZW50SWQ6IGV2ZW50LmV2ZW50SWQsXG4gICAgICAgIHByb2plY3RJZDogZXZlbnQucHJvamVjdElkLFxuICAgICAgICBldmVudFR5cGU6IGV2ZW50LmV2ZW50VHlwZSxcbiAgICAgICAgYnJhbmNoOiBldmVudC5icmFuY2gsXG4gICAgICAgIHBhcmVudEJyYW5jaDogKGV2ZW50LnBheWxvYWQgYXMgUmVjb3JkPHN0cmluZywgdW5rbm93bj4pLnBhcmVudEJyYW5jaCA/PyBudWxsLFxuICAgICAgICBwYXlsb2FkOiBldmVudC5wYXlsb2FkLFxuICAgICAgICB0aW1lc3RhbXA6IGV2ZW50LnRpbWVzdGFtcCxcbiAgICAgICAgY29ycmVsYXRpb25JZDogZXZlbnRDb3JyZWxhdGlvbklkLFxuICAgICAgfSkpLFxuICAgIH0pKTtcbiAgfSBjYXRjaCAoZXJyKSB7IGNvbnNvbGUuZXJyb3IoJ1tub24tZmF0YWxdIEFJIGludm9rZSBmYWlsZWQ6JywgKGVyciBhcyBFcnJvcikubWVzc2FnZSk7IH1cblxuICBpZiAoKGV2ZW50LnBheWxvYWQgYXMgUmVjb3JkPHN0cmluZywgdW5rbm93bj4pLmlzTWVyZ2UgJiYgKGV2ZW50LnBheWxvYWQgYXMgUmVjb3JkPHN0cmluZywgdW5rbm93bj4pLnNvdXJjZUJyYW5jaCkge1xuICAgIHRyeSB7XG4gICAgICBhd2FpdCBsYW1iZGFDbGllbnQuc2VuZChuZXcgSW52b2tlQ29tbWFuZCh7XG4gICAgICAgIEZ1bmN0aW9uTmFtZTogQUlfRlVOQ1RJT04sXG4gICAgICAgIEludm9jYXRpb25UeXBlOiAnRXZlbnQnLFxuICAgICAgICBQYXlsb2FkOiBCdWZmZXIuZnJvbShKU09OLnN0cmluZ2lmeSh7XG4gICAgICAgICAgcHJvcGFnYXRlOiB0cnVlLFxuICAgICAgICAgIHByb2plY3RJZDogZXZlbnQucHJvamVjdElkLFxuICAgICAgICAgIHNvdXJjZUJyYW5jaDogKGV2ZW50LnBheWxvYWQgYXMgUmVjb3JkPHN0cmluZywgdW5rbm93bj4pLnNvdXJjZUJyYW5jaCxcbiAgICAgICAgICB0YXJnZXRCcmFuY2g6IGV2ZW50LmJyYW5jaCxcbiAgICAgICAgICB0aW1lc3RhbXA6IGV2ZW50LnRpbWVzdGFtcCxcbiAgICAgICAgICBjb3JyZWxhdGlvbklkOiBldmVudENvcnJlbGF0aW9uSWQsXG4gICAgICAgIH0pKSxcbiAgICAgIH0pKTtcbiAgICAgIGNvbnNvbGUubG9nKGBbbWVyZ2UtcHJvcGFnYXRlXSBxdWV1ZWQgcHJvcGFnYXRpb246ICR7KGV2ZW50LnBheWxvYWQgYXMgUmVjb3JkPHN0cmluZywgdW5rbm93bj4pLnNvdXJjZUJyYW5jaH0g4oaSICR7ZXZlbnQuYnJhbmNofWApO1xuICAgIH0gY2F0Y2ggKGVycikgeyBjb25zb2xlLmVycm9yKCdbbm9uLWZhdGFsXSBNZXJnZSBwcm9wYWdhdGlvbiBpbnZva2UgZmFpbGVkOicsIChlcnIgYXMgRXJyb3IpLm1lc3NhZ2UpOyB9XG4gIH1cblxuICB0cnkge1xuICAgIGF3YWl0IGR5bmFtby5zZW5kKG5ldyBQdXRDb21tYW5kKHtcbiAgICAgIFRhYmxlTmFtZTogQVVESVRfVEFCTEUsXG4gICAgICBJdGVtOiB7XG4gICAgICAgIGVudGl0eUlkOiBldmVudC5ldmVudElkLFxuICAgICAgICB0aW1lc3RhbXA6IHJlY2VpdmVkQXQsXG4gICAgICAgIGVudGl0eVR5cGU6ICdldmVudCcsXG4gICAgICAgIGFjdGlvbjogJ2NyZWF0ZWQnLFxuICAgICAgICBhY3RvcjogKGV2ZW50LnBheWxvYWQgYXMgUmVjb3JkPHN0cmluZywgdW5rbm93bj4pLmF1dGhvciA/PyAnc3lzdGVtJyxcbiAgICAgICAgY2hhbmdlczoge1xuICAgICAgICAgIGV2ZW50VHlwZTogZXZlbnQuZXZlbnRUeXBlLFxuICAgICAgICAgIGJyYW5jaDogZXZlbnQuYnJhbmNoLFxuICAgICAgICAgIGNvbW1pdEhhc2g6IChldmVudC5wYXlsb2FkIGFzIFJlY29yZDxzdHJpbmcsIHVua25vd24+KS5jb21taXRIYXNoID8/IG51bGwsXG4gICAgICAgIH0sXG4gICAgICAgIGNvcnJlbGF0aW9uSWQ6IGV2ZW50Q29ycmVsYXRpb25JZCxcbiAgICAgIH0sXG4gICAgfSkpO1xuICB9IGNhdGNoIChlcnIpIHsgY29uc29sZS5lcnJvcignW25vbi1mYXRhbF0gQXVkaXQgd3JpdGUgZmFpbGVkOicsIChlcnIgYXMgRXJyb3IpLm1lc3NhZ2UpOyB9XG5cbiAgY29uc3QgaW5nZXN0aW9uX21zID0gRGF0ZS5ub3coKSAtIHRfaW5nZXN0aW9uX3N0YXJ0O1xuICBjb25zb2xlLmxvZyhKU09OLnN0cmluZ2lmeSh7IElOR0VTVElPTl9USU1JTkc6IHRydWUsIGV2ZW50SWQ6IGV2ZW50LmV2ZW50SWQsIGluZ2VzdGlvbl9tcywgcmVjZWl2ZWRBdCwgY29ycmVsYXRpb25JZDogZXZlbnRDb3JyZWxhdGlvbklkIH0pKTtcblxuICByZXR1cm4gcmVzcG9uZCgyMDAsIHtcbiAgICBldmVudElkOiBldmVudC5ldmVudElkLFxuICAgIHByb2plY3RJZDogZXZlbnQucHJvamVjdElkLFxuICAgIGJyYW5jaDogZXZlbnQuYnJhbmNoLFxuICAgIHN0YXR1czogJ3Byb2Nlc3NpbmcnLFxuICAgIHJlY2VpdmVkQXQsXG4gICAgY29ycmVsYXRpb25JZDogZXZlbnRDb3JyZWxhdGlvbklkLFxuICB9LCBjb3JyZWxhdGlvbklkKTtcbn1cblxuLy8g4pSA4pSAIFJvdXRlOiBHRVQgL2FwaS92MS9wcm9qZWN0cy97cHJvamVjdElkfSDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIBcbmFzeW5jIGZ1bmN0aW9uIGdldFByb2plY3QoaGVhZGVyczogUmVjb3JkPHN0cmluZywgc3RyaW5nIHwgdW5kZWZpbmVkPiwgcGF0aFBhcmFtZXRlcnM6IFJlY29yZDxzdHJpbmcsIHN0cmluZz4gfCB1bmRlZmluZWQsIGNvcnJlbGF0aW9uSWQ6IHN0cmluZykge1xuICBjb25zdCB7IHByb2plY3RJZCB9ID0gcGF0aFBhcmFtZXRlcnMgPz8ge307XG4gIGlmICghcHJvamVjdElkKSByZXR1cm4gcmVzcG9uZCg0MDAsIHsgZXJyb3I6ICd2YWxpZGF0aW9uX2ZhaWxlZCcsIGRldGFpbHM6IFsncHJvamVjdElkOiByZXF1aXJlZCddIH0sIGNvcnJlbGF0aW9uSWQpO1xuXG4gIGNvbnN0IGF1dGggPSBhd2FpdCBhdXRoZW50aWNhdGUoaGVhZGVycywgcHJvamVjdElkKTtcbiAgaWYgKGF1dGguZXJyb3IpIHJldHVybiByZXNwb25kKGF1dGguc3RhdHVzQ29kZSwgYXV0aC5lcnJvciwgY29ycmVsYXRpb25JZCk7XG5cbiAgY29uc3QgcCA9IGF1dGgucHJvamVjdDtcbiAgcmV0dXJuIHJlc3BvbmQoMjAwLCB7XG4gICAgcHJvamVjdElkOiBwLnByb2plY3RJZCxcbiAgICBuYW1lOiBwLm5hbWUsXG4gICAgZGVzY3JpcHRpb246IHAuZGVzY3JpcHRpb24sXG4gICAgbGFuZ3VhZ2VzOiBwLmxhbmd1YWdlcyxcbiAgICBmcmFtZXdvcmtzOiBwLmZyYW1ld29ya3MsXG4gICAgZGVmYXVsdEJyYW5jaDogcC5kZWZhdWx0QnJhbmNoLFxuICAgIHRlYW1NZW1iZXJzOiBwLnRlYW1NZW1iZXJzLFxuICAgIGxhc3RBY3Rpdml0eUF0OiBwLmxhc3RBY3Rpdml0eUF0LFxuICAgIGV2ZW50Q291bnQ6IHAuZXZlbnRDb3VudCxcbiAgfSwgY29ycmVsYXRpb25JZCk7XG59XG5cbi8vIOKUgOKUgCBNYWluIEhhbmRsZXIg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSAXG5leHBvcnQgY29uc3QgaGFuZGxlciA9IGFzeW5jIChldmVudDoge1xuICBodHRwTWV0aG9kPzogc3RyaW5nO1xuICByZXNvdXJjZT86IHN0cmluZztcbiAgcGF0aFBhcmFtZXRlcnM/OiBSZWNvcmQ8c3RyaW5nLCBzdHJpbmc+O1xuICBoZWFkZXJzPzogUmVjb3JkPHN0cmluZywgc3RyaW5nIHwgdW5kZWZpbmVkPjtcbiAgYm9keT86IHN0cmluZztcbn0pID0+IHtcbiAgY29uc3QgY29ycmVsYXRpb25JZCA9IGV4dHJhY3RDb3JyZWxhdGlvbklkKGV2ZW50LmhlYWRlcnMpO1xuXG4gIGNvbnNvbGUubG9nKEpTT04uc3RyaW5naWZ5KHtcbiAgICBtc2c6ICdyZXF1ZXN0JyxcbiAgICBtZXRob2Q6IGV2ZW50Lmh0dHBNZXRob2QsXG4gICAgcmVzb3VyY2U6IGV2ZW50LnJlc291cmNlLFxuICAgIGNvcnJlbGF0aW9uSWQsXG4gIH0pKTtcblxuICB0cnkge1xuICAgIGNvbnN0IG1ldGhvZCA9IGV2ZW50Lmh0dHBNZXRob2QgPz8gJyc7XG4gICAgY29uc3QgcmVzb3VyY2UgPSBldmVudC5yZXNvdXJjZSA/PyAnJztcblxuICAgIGlmIChtZXRob2QgPT09ICdPUFRJT05TJykgcmV0dXJuIHJlc3BvbmQoMjAwLCB7IG9rOiB0cnVlIH0sIGNvcnJlbGF0aW9uSWQpO1xuXG4gICAgaWYgKG1ldGhvZCA9PT0gJ1BPU1QnICYmIHJlc291cmNlID09PSAnL2FwaS92MS9wcm9qZWN0cycpXG4gICAgICByZXR1cm4gYXdhaXQgY3JlYXRlUHJvamVjdChldmVudC5ib2R5ID8/ICcnLCBjb3JyZWxhdGlvbklkKTtcblxuICAgIGlmIChtZXRob2QgPT09ICdQT1NUJyAmJiByZXNvdXJjZSA9PT0gJy9hcGkvdjEvZXZlbnRzJylcbiAgICAgIHJldHVybiBhd2FpdCBpbmdlc3RFdmVudChldmVudC5oZWFkZXJzID8/IHt9LCBldmVudC5ib2R5ID8/ICcnLCBjb3JyZWxhdGlvbklkKTtcblxuICAgIGlmIChtZXRob2QgPT09ICdHRVQnICYmIHJlc291cmNlID09PSAnL2FwaS92MS9wcm9qZWN0cy97cHJvamVjdElkfScpXG4gICAgICByZXR1cm4gYXdhaXQgZ2V0UHJvamVjdChldmVudC5oZWFkZXJzID8/IHt9LCBldmVudC5wYXRoUGFyYW1ldGVycywgY29ycmVsYXRpb25JZCk7XG5cbiAgICByZXR1cm4gcmVzcG9uZCg0MDQsIHsgZXJyb3I6ICdub3RfZm91bmQnLCBtZXNzYWdlOiBgTm8gcm91dGUgZm9yICR7bWV0aG9kfSAke3Jlc291cmNlfWAgfSwgY29ycmVsYXRpb25JZCk7XG5cbiAgfSBjYXRjaCAoZXJyKSB7XG4gICAgY29uc29sZS5lcnJvcihKU09OLnN0cmluZ2lmeSh7XG4gICAgICBtc2c6ICd1bmhhbmRsZWRfZXJyb3InLFxuICAgICAgZXJyb3I6IChlcnIgYXMgRXJyb3IpLm1lc3NhZ2UsXG4gICAgICBzdGFjazogKGVyciBhcyBFcnJvcikuc3RhY2ssXG4gICAgICBjb3JyZWxhdGlvbklkLFxuICAgIH0pKTtcbiAgICByZXR1cm4gcmVzcG9uZCg1MDAsIHsgZXJyb3I6ICdpbnRlcm5hbF9lcnJvcicsIG1lc3NhZ2U6ICdBbiB1bmV4cGVjdGVkIGVycm9yIG9jY3VycmVkJyB9LCBjb3JyZWxhdGlvbklkKTtcbiAgfVxufTsiXX0=