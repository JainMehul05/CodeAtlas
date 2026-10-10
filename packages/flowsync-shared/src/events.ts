/**
 * FlowSync Shared Types
 * Canonical event model and validation schemas for V2
 */

import { z } from "zod";
import { randomUUID } from "crypto";

/**
 * Supported event sources
 */
export enum EventSource {
  VSCODE = "vscode",
  GITHUB = "github",
  CI_CD = "ci_cd",
  DEPLOYMENT = "deployment",
  MANUAL = "manual",
}

/**
 * Supported event types
 */
export enum EventType {
  PUSH = "push",
  DEVELOPER_NOTE = "developer_note",
  AGENT_REASONING = "agent_reasoning",
  MERGE = "merge",
  DEPLOYMENT = "deployment",
  BUILD = "build",
  WORKFLOW_RUN = "workflow_run",
  CHECK_RUN = "check_run",
  CHECK_SUITE = "check_suite",
}

/**
 * Actor information
 */
export interface Actor {
  id: string;
  name: string;
  email?: string;
  avatarUrl?: string;
}

/**
 * Base event payload - extended by specific event types
 */
export interface BaseEventPayload {
  [key: string]: unknown;
}

/**
 * Push event payload
 */
export interface PushEventPayload extends BaseEventPayload {
  commitHash: string;
  message: string;
  diff: string;
  author: string;
  parentBranch?: string;
  isMerge?: boolean;
  sourceBranch?: string;
  changedFiles?: string[];
}

/**
 * Developer note event payload
 */
export interface DeveloperNotePayload extends BaseEventPayload {
  text: string;
  filePath: string;
  lineNumber: number;
}

/**
 * Agent reasoning payload
 */
export interface AgentReasoningPayload extends BaseEventPayload {
  reasoning: string;
  branch: string;
  author: string;
  decision?: string;
  tasks?: string[];
  risk?: string;
}

/**
 * Merge event payload
 */
export interface MergeEventPayload extends BaseEventPayload {
  sourceBranch: string;
  targetBranch: string;
  commitHash: string;
  author: string;
}

/**
 * GitHub Actions workflow run event payload
 */
export interface WorkflowRunPayload extends BaseEventPayload {
  workflowId: number;
  workflowName: string;
  runId: number;
  runNumber: number;
  runAttempt: number;
  event: string;  // e.g., "push", "pull_request"
  status: string;  // "queued", "in_progress", "completed"
  conclusion?: string;  // "success", "failure", "cancelled", "skipped", "timed_out", "action_required"
  headBranch: string;
  headSha: string;
  repository: {
    id: number;
    name: string;
    fullName: string;
  };
  startedAt: string;
  completedAt?: string;
  htmlUrl: string;
  checkSuiteId?: number;
  pullRequests?: Array<{
    number: number;
    headBranch: string;
    baseBranch: string;
  }>;
}

/**
 * GitHub check run event payload
 */
export interface CheckRunPayload extends BaseEventPayload {
  checkRunId: number;
  name: string;
  headSha: string;
  status: string;  // "queued", "in_progress", "completed"
  conclusion?: string;  // "success", "failure", "neutral", "cancelled", "skipped", "timed_out", "action_required"
  startedAt: string;
  completedAt?: string;
  htmlUrl: string;
  repository: {
    id: number;
    name: string;
    fullName: string;
  };
  checkSuiteId: number;
  pullRequests?: Array<{
    number: number;
    headBranch: string;
    baseBranch: string;
  }>;
  output?: {
    title: string;
    summary: string;
    text?: string;
    annotationsCount?: number;
    annotationsUrl?: string;
  };
}

/**
 * GitHub check suite event payload
 */
export interface CheckSuitePayload extends BaseEventPayload {
  checkSuiteId: number;
  headBranch: string;
  headSha: string;
  status: string;  // "queued", "in_progress", "completed"
  conclusion?: string;
  repository: {
    id: number;
    name: string;
    fullName: string;
  };
  pullRequests?: Array<{
    number: number;
    headBranch: string;
    baseBranch: string;
  }>;
  createdAt: string;
  updatedAt: string;
}

/**
 * Canonical FlowSync Event
 * This is the foundation for all events in the system
 */
export interface FlowSyncEvent {
  /** Unique event identifier (UUID v4) */
  eventId: string;

  /** Event type discriminator */
  eventType: EventType;

  /** Schema version for forward compatibility */
  schemaVersion: string;

  /** Source of the event */
  source: EventSource;

  /** Project identifier */
  projectId: string;

  /** Organization identifier (future use) */
  organizationId?: string;

  /** Repository identifier (future use) */
  repositoryId?: string;

  /** Actor who triggered the event */
  actor: Actor;

  /** ISO 8601 timestamp */
  timestamp: string;

  /** Optional delivery ID from source system (e.g., GitHub delivery ID) */
  deliveryId?: string;

  /** Correlation ID for request tracing */
  correlationId?: string;

  /** Event-specific payload */
  payload: BaseEventPayload;

  /** Additional metadata */
  metadata?: Record<string, unknown>;
}

/**
 * Zod schemas for validation
 */

export const ActorSchema = z.object({
  id: z.string().min(1),
  name: z.string().min(1),
  email: z.string().email().optional(),
  avatarUrl: z.string().url().optional(),
});

export const PushEventPayloadSchema = z.object({
  commitHash: z.string().regex(/^[0-9a-f]{40}$/i),
  message: z.string().min(1),
  diff: z.string().max(50000),
  author: z.string().min(1),
  parentBranch: z.string().optional(),
  isMerge: z.boolean().optional(),
  sourceBranch: z.string().optional(),
  changedFiles: z.array(z.string()).optional(),
});

export const DeveloperNotePayloadSchema = z.object({
  text: z.string().min(1),
  filePath: z.string().min(1).refine((v) => !v.includes(".."), "Directory traversal not allowed"),
  lineNumber: z.number().int().nonnegative(),
});

export const AgentReasoningPayloadSchema = z.object({
  reasoning: z.string().min(10),
  branch: z.string().min(1),
  author: z.string().min(1),
  decision: z.string().optional(),
  tasks: z.array(z.string()).optional(),
  risk: z.string().optional(),
});

export const MergeEventPayloadSchema = z.object({
  sourceBranch: z.string().min(1),
  targetBranch: z.string().min(1),
  commitHash: z.string().regex(/^[0-9a-f]{40}$/i),
  author: z.string().min(1),
});

// Discriminated union for payload based on eventType
export const PushEventSchema = z.object({
  eventType: z.literal(EventType.PUSH),
  payload: PushEventPayloadSchema,
});

export const DeveloperNoteEventSchema = z.object({
  eventType: z.literal(EventType.DEVELOPER_NOTE),
  payload: DeveloperNotePayloadSchema,
});

export const AgentReasoningEventSchema = z.object({
  eventType: z.literal(EventType.AGENT_REASONING),
  payload: AgentReasoningPayloadSchema,
});

export const MergeEventSchema = z.object({
  eventType: z.literal(EventType.MERGE),
  payload: MergeEventPayloadSchema,
});

export const WorkflowRunPayloadSchema = z.object({
  workflowId: z.number().int().positive(),
  workflowName: z.string().min(1),
  runId: z.number().int().positive(),
  runNumber: z.number().int().positive(),
  runAttempt: z.number().int().positive(),
  event: z.string().min(1),
  status: z.enum(["queued", "in_progress", "completed"]),
  conclusion: z.enum(["success", "failure", "cancelled", "skipped", "timed_out", "action_required"]).optional(),
  headBranch: z.string().min(1),
  headSha: z.string().regex(/^[0-9a-f]{40}$/i),
  repository: z.object({
    id: z.number().int().positive(),
    name: z.string().min(1),
    fullName: z.string().min(1),
  }),
  startedAt: z.string().datetime({ offset: true }),
  completedAt: z.string().datetime({ offset: true }).optional(),
  htmlUrl: z.string().url(),
  checkSuiteId: z.number().int().positive().optional(),
  pullRequests: z.array(z.object({
    number: z.number().int().positive(),
    headBranch: z.string().min(1),
    baseBranch: z.string().min(1),
  })).optional(),
});

export const CheckRunPayloadSchema = z.object({
  checkRunId: z.number().int().positive(),
  name: z.string().min(1),
  headSha: z.string().regex(/^[0-9a-f]{40}$/i),
  status: z.enum(["queued", "in_progress", "completed"]),
  conclusion: z.enum(["success", "failure", "neutral", "cancelled", "skipped", "timed_out", "action_required"]).optional(),
  startedAt: z.string().datetime({ offset: true }),
  completedAt: z.string().datetime({ offset: true }).optional(),
  htmlUrl: z.string().url(),
  repository: z.object({
    id: z.number().int().positive(),
    name: z.string().min(1),
    fullName: z.string().min(1),
  }),
  checkSuiteId: z.number().int().positive(),
  pullRequests: z.array(z.object({
    number: z.number().int().positive(),
    headBranch: z.string().min(1),
    baseBranch: z.string().min(1),
  })).optional(),
  output: z.object({
    title: z.string().min(1),
    summary: z.string().min(1),
    text: z.string().optional(),
    annotationsCount: z.number().int().nonnegative().optional(),
    annotationsUrl: z.string().url().optional(),
  }).optional(),
});

export const CheckSuitePayloadSchema = z.object({
  checkSuiteId: z.number().int().positive(),
  headBranch: z.string().min(1),
  headSha: z.string().regex(/^[0-9a-f]{40}$/i),
  status: z.enum(["queued", "in_progress", "completed"]),
  conclusion: z.enum(["success", "failure", "neutral", "cancelled", "skipped", "timed_out", "action_required"]).optional(),
  repository: z.object({
    id: z.number().int().positive(),
    name: z.string().min(1),
    fullName: z.string().min(1),
  }),
  pullRequests: z.array(z.object({
    number: z.number().int().positive(),
    headBranch: z.string().min(1),
    baseBranch: z.string().min(1),
  })).optional(),
  createdAt: z.string().datetime({ offset: true }),
  updatedAt: z.string().datetime({ offset: true }),
});

export const WorkflowRunEventSchema = z.object({
  eventType: z.literal(EventType.WORKFLOW_RUN),
  payload: WorkflowRunPayloadSchema,
});

export const CheckRunEventSchema = z.object({
  eventType: z.literal(EventType.CHECK_RUN),
  payload: CheckRunPayloadSchema,
});

export const CheckSuiteEventSchema = z.object({
  eventType: z.literal(EventType.CHECK_SUITE),
  payload: CheckSuitePayloadSchema,
});

export const BaseEventSchema = z.object({
  eventId: z.string().uuid(),
  eventType: z.nativeEnum(EventType),
  schemaVersion: z.string().default("1"),
  source: z.nativeEnum(EventSource),
  projectId: z.string().min(1),
  organizationId: z.string().optional(),
  repositoryId: z.string().optional(),
  actor: ActorSchema,
  timestamp: z.string().datetime({ offset: true }),
  deliveryId: z.string().optional(),
  correlationId: z.string().optional(),
  metadata: z.record(z.unknown()).optional(),
});

export const FlowSyncEventSchema = BaseEventSchema.and(
  z.discriminatedUnion("eventType", [
    PushEventSchema,
    DeveloperNoteEventSchema,
    AgentReasoningEventSchema,
    MergeEventSchema,
    WorkflowRunEventSchema,
    CheckRunEventSchema,
    CheckSuiteEventSchema,
  ])
);

/**
 * Validation result
 */
export interface ValidationResult<T> {
  success: boolean;
  data?: T;
  errors?: ValidationError[];
}

export interface ValidationError {
  field: string;
  message: string;
  code: string;
}

/**
 * Validates a FlowSync event
 */
export function validateEvent(event: unknown): ValidationResult<FlowSyncEvent> {
  const result = FlowSyncEventSchema.safeParse(event);

  if (result.success) {
    return { success: true, data: result.data };
  }

  const errors: ValidationError[] = result.error.issues.map((issue) => ({
    field: issue.path.join("."),
    message: issue.message,
    code: issue.code,
  }));

  return { success: false, errors };
}

/**
 * Creates a new FlowSync event with generated IDs
 */
export function createEvent<T extends BaseEventPayload>(
  eventType: EventType,
  source: EventSource,
  projectId: string,
  actor: Actor,
  payload: T,
  options?: {
    organizationId?: string;
    repositoryId?: string;
    deliveryId?: string;
    correlationId?: string;
    metadata?: Record<string, unknown>;
  }
): FlowSyncEvent {
  return {
    eventId: randomUUID(),
    eventType,
    schemaVersion: "1",
    source,
    projectId,
    organizationId: options?.organizationId,
    repositoryId: options?.repositoryId,
    actor,
    timestamp: new Date().toISOString(),
    deliveryId: options?.deliveryId,
    correlationId: options?.correlationId,
    payload,
    metadata: options?.metadata,
  };
}

/**
 * Type guards for payload types
 */
export function isPushPayload(payload: BaseEventPayload): payload is PushEventPayload {
  return (
    typeof payload === "object" &&
    payload !== null &&
    "commitHash" in payload &&
    "message" in payload &&
    "diff" in payload &&
    "author" in payload
  );
}

export function isDeveloperNotePayload(payload: BaseEventPayload): payload is DeveloperNotePayload {
  return (
    typeof payload === "object" &&
    payload !== null &&
    "text" in payload &&
    "filePath" in payload &&
    "lineNumber" in payload
  );
}

export function isAgentReasoningPayload(payload: BaseEventPayload): payload is AgentReasoningPayload {
  return (
    typeof payload === "object" &&
    payload !== null &&
    "reasoning" in payload &&
    "branch" in payload &&
    "author" in payload
  );
}

export function isMergePayload(payload: BaseEventPayload): payload is MergeEventPayload {
  return (
    typeof payload === "object" &&
    payload !== null &&
    "sourceBranch" in payload &&
    "targetBranch" in payload &&
    "commitHash" in payload &&
    "author" in payload
  );
}

export function isWorkflowRunPayload(payload: BaseEventPayload): payload is WorkflowRunPayload {
  return (
    typeof payload === "object" &&
    payload !== null &&
    "workflowId" in payload &&
    "workflowName" in payload &&
    "runId" in payload &&
    "headSha" in payload
  );
}

export function isCheckRunPayload(payload: BaseEventPayload): payload is CheckRunPayload {
  return (
    typeof payload === "object" &&
    payload !== null &&
    "checkRunId" in payload &&
    "name" in payload &&
    "headSha" in payload &&
    "status" in payload
  );
}

export function isCheckSuitePayload(payload: BaseEventPayload): payload is CheckSuitePayload {
  return (
    typeof payload === "object" &&
    payload !== null &&
    "checkSuiteId" in payload &&
    "headBranch" in payload &&
    "headSha" in payload &&
    "status" in payload
  );
}