/**
 * FlowSync Engineering Knowledge Graph
 * Canonical graph model and validation schemas for Phase 3
 */

import { z } from "zod";
import { randomUUID } from "crypto";

/**
 * Graph entity types
 */
export enum GraphEntityType {
  REPOSITORY = "repository",
  COMMIT = "commit",
  PULL_REQUEST = "pull_request",
  FILE = "file",
  ENGINEERING_DECISION = "engineering_decision",
  DEVELOPER = "developer",
}

/**
 * Graph relationship types
 */
export enum GraphRelationshipType {
  CONTAINS = "contains",                    // Repository -> File
  AUTHORED_IN = "authored_in",              // Commit -> Repository
  MODIFIES = "modifies",                    // Commit -> File
  HAS_PARENT = "has_parent",                // Commit -> Parent Commit
  INCLUDES_COMMIT = "includes_commit",      // Pull Request -> Commit
  TARGETS_FILE = "targets_file",            // Pull Request -> File
  HAS_DECISION = "has_decision",            // Repository/Project -> Engineering Decision
  RELATES_TO = "relates_to",                // Engineering Decision/Context -> Commit, PR, File, Repository
  DERIVED_FROM = "derived_from",            // Graph entity/relationship -> originating event/context
  ASSOCIATED_WITH = "associated_with",      // Generic association between entities
}

/**
 * Provenance types for relationships
 */
export enum RelationshipProvenance {
  EXPLICIT = "explicit",       // Directly stated in source (e.g., commit modifies file)
  INFERRED = "inferred",       // Derived from heuristics or rules
  USER_DEFINED = "user_defined", // Manually created by user
}

/**
 * Base graph entity interface
 */
export interface GraphEntity {
  entityId: string;
  entityType: GraphEntityType;
  projectId: string;
  repositoryId?: string;
  createdAt: string;
  updatedAt: string;
  schemaVersion: string;
  metadata?: Record<string, unknown>;
}

/**
 * Repository entity
 */
export interface RepositoryEntity extends GraphEntity {
  entityType: GraphEntityType.REPOSITORY;
  provider: string;           // e.g., "github", "gitlab"
  owner: string;
  name: string;
  canonicalUrl?: string;
  defaultBranch?: string;
}

/**
 * Commit entity
 */
export interface CommitEntity extends GraphEntity {
  entityType: GraphEntityType.COMMIT;
  sha: string;                // Full 40-char SHA
  author: string;
  committer: string;
  message: string;
  committedAt: string;
  parentShas: string[];
  source: string;             // e.g., "github", "vscode"
}

/**
 * Pull Request entity
 */
export interface PullRequestEntity extends GraphEntity {
  entityType: GraphEntityType.PULL_REQUEST;
  number: number;
  title: string;
  description?: string;
  state: string;              // "open", "closed", "merged"
  author: string;
  sourceBranch: string;
  targetBranch: string;
  createdAt: string;
  updatedAt: string;
  mergedAt?: string;
  closedAt?: string;
  url?: string;
  source: string;
}

/**
 * File entity
 */
export interface FileEntity extends GraphEntity {
  entityType: GraphEntityType.FILE;
  path: string;               // Normalized repository-relative path
  language?: string;
  lastObservedAt: string;
}

/**
 * Engineering Decision entity
 */
export interface EngineeringDecisionEntity extends GraphEntity {
  entityType: GraphEntityType.ENGINEERING_DECISION;
  title: string;
  summary: string;
  decision: string;
  rationale?: string;
  status?: string;            // e.g., "proposed", "accepted", "superseded"
  sourceType: string;         // e.g., "context_record", "developer_note", "agent_reasoning"
  sourceId: string;
  sourceUrl?: string;
}

/**
 * Developer entity (optional - only if identity model justifies it)
 */
export interface DeveloperEntity extends GraphEntity {
  entityType: GraphEntityType.DEVELOPER;
  name: string;
  email?: string;
  avatarUrl?: string;
  source: string;
}

/**
 * Union type for all entity types
 */
export type AnyGraphEntity =
  | RepositoryEntity
  | CommitEntity
  | PullRequestEntity
  | FileEntity
  | EngineeringDecisionEntity
  | DeveloperEntity;

/**
 * Graph relationship interface
 */
export interface GraphRelationship {
  relationshipId: string;
  relationshipType: GraphRelationshipType;
  sourceEntityId: string;
  sourceEntityType: GraphEntityType;
  targetEntityId: string;
  targetEntityType: GraphEntityType;
  projectId: string;
  repositoryId?: string;
  provenance: RelationshipProvenance;
  confidence?: number;        // 0-1, only for inferred relationships
  evidence?: string;          // Reference to source evidence (eventId, contextId, etc.)
  createdAt: string;
  updatedAt: string;
  schemaVersion: string;
  metadata?: Record<string, unknown>;
}

/**
 * Stable ID generation functions
 */
export function generateRepositoryId(provider: string, owner: string, name: string): string {
  return `repo:${provider}:${owner}:${name}`.toLowerCase();
}

export function generateCommitId(repositoryId: string, sha: string): string {
  return `commit:${repositoryId}:${sha.toLowerCase()}`;
}

export function generatePullRequestId(repositoryId: string, number: number): string {
  return `pr:${repositoryId}:${number}`;
}

export function generateFileId(repositoryId: string, path: string): string {
  const normalizedPath = normalizeFilePath(path);
  return `file:${repositoryId}:${normalizedPath}`;
}

export function generateEngineeringDecisionId(projectId: string, sourceType: string, sourceId: string): string {
  return `decision:${projectId}:${sourceType}:${sourceId}`;
}

export function generateDeveloperId(email: string): string {
  return `dev:${email.toLowerCase()}`;
}

export function generateRelationshipId(
  relationshipType: GraphRelationshipType,
  sourceEntityId: string,
  targetEntityId: string
): string {
  // Deterministic ID based on relationship type and entity IDs
  const parts = [relationshipType, sourceEntityId, targetEntityId].sort();
  // Use a simple hash for deterministic ID
  let hash = 0;
  for (const part of parts) {
    for (let i = 0; i < part.length; i++) {
      hash = ((hash << 5) - hash) + part.charCodeAt(i);
      hash |= 0;
    }
  }
  return `rel:${relationshipType}:${Math.abs(hash).toString(36)}`;
}

/**
 * Normalize file path for consistent identity
 */
export function normalizeFilePath(path: string): string {
  // Remove leading/trailing whitespace
  let normalized = path.trim();
  
  // Handle URL-encoded paths
  try {
    normalized = decodeURIComponent(normalized);
  } catch {
    // Ignore decoding errors
  }
  
  // Normalize separators
  normalized = normalized.replace(/\\/g, '/');
  
  // Remove leading ./ or /
  normalized = normalized.replace(/^(\.\/)+/, '').replace(/^\/+/, '');
  
  // Resolve . and .. segments
  const segments = normalized.split('/');
  const resolved: string[] = [];
  for (const segment of segments) {
    if (segment === '' || segment === '.') continue;
    if (segment === '..') {
      if (resolved.length > 0) resolved.pop();
    } else {
      resolved.push(segment);
    }
  }
  
  return resolved.join('/');
}

/**
 * Zod schemas for validation
 */

export const GraphEntityBaseSchema = z.object({
  entityId: z.string().min(1),
  entityType: z.nativeEnum(GraphEntityType),
  projectId: z.string().min(1),
  repositoryId: z.string().optional(),
  createdAt: z.string().datetime({ offset: true }),
  updatedAt: z.string().datetime({ offset: true }),
  schemaVersion: z.string().default("1"),
  metadata: z.record(z.unknown()).optional(),
});

export const RepositoryEntitySchema = GraphEntityBaseSchema.extend({
  entityType: z.literal(GraphEntityType.REPOSITORY),
  provider: z.string().min(1),
  owner: z.string().min(1),
  name: z.string().min(1),
  canonicalUrl: z.string().url().optional(),
  defaultBranch: z.string().optional(),
});

export const CommitEntitySchema = GraphEntityBaseSchema.extend({
  entityType: z.literal(GraphEntityType.COMMIT),
  sha: z.string().regex(/^[0-9a-f]{40}$/i),
  author: z.string().min(1),
  committer: z.string().min(1),
  message: z.string().min(1),
  committedAt: z.string().datetime({ offset: true }),
  parentShas: z.array(z.string().regex(/^[0-9a-f]{40}$/i)),
  source: z.string().min(1),
});

export const PullRequestEntitySchema = GraphEntityBaseSchema.extend({
  entityType: z.literal(GraphEntityType.PULL_REQUEST),
  number: z.number().int().positive(),
  title: z.string().min(1),
  description: z.string().optional(),
  state: z.enum(["open", "closed", "merged"]),
  author: z.string().min(1),
  sourceBranch: z.string().min(1),
  targetBranch: z.string().min(1),
  createdAt: z.string().datetime({ offset: true }),
  updatedAt: z.string().datetime({ offset: true }),
  mergedAt: z.string().datetime({ offset: true }).optional(),
  closedAt: z.string().datetime({ offset: true }).optional(),
  url: z.string().url().optional(),
  source: z.string().min(1),
});

export const FileEntitySchema = GraphEntityBaseSchema.extend({
  entityType: z.literal(GraphEntityType.FILE),
  path: z.string().min(1).refine((v) => !v.includes(".."), "Directory traversal not allowed"),
  language: z.string().optional(),
  lastObservedAt: z.string().datetime({ offset: true }),
});

export const EngineeringDecisionEntitySchema = GraphEntityBaseSchema.extend({
  entityType: z.literal(GraphEntityType.ENGINEERING_DECISION),
  title: z.string().min(1),
  summary: z.string().min(1),
  decision: z.string().min(1),
  rationale: z.string().optional(),
  status: z.string().optional(),
  sourceType: z.string().min(1),
  sourceId: z.string().min(1),
  sourceUrl: z.string().url().optional(),
});

export const DeveloperEntitySchema = GraphEntityBaseSchema.extend({
  entityType: z.literal(GraphEntityType.DEVELOPER),
  name: z.string().min(1),
  email: z.string().email().optional(),
  avatarUrl: z.string().url().optional(),
  source: z.string().min(1),
});

export const AnyGraphEntitySchema = z.discriminatedUnion("entityType", [
  RepositoryEntitySchema,
  CommitEntitySchema,
  PullRequestEntitySchema,
  FileEntitySchema,
  EngineeringDecisionEntitySchema,
  DeveloperEntitySchema,
]);

export const GraphRelationshipSchema = z.object({
  relationshipId: z.string().min(1),
  relationshipType: z.nativeEnum(GraphRelationshipType),
  sourceEntityId: z.string().min(1),
  sourceEntityType: z.nativeEnum(GraphEntityType),
  targetEntityId: z.string().min(1),
  targetEntityType: z.nativeEnum(GraphEntityType),
  projectId: z.string().min(1),
  repositoryId: z.string().optional(),
  provenance: z.nativeEnum(RelationshipProvenance),
  confidence: z.number().min(0).max(1).optional(),
  evidence: z.string().optional(),
  createdAt: z.string().datetime({ offset: true }),
  updatedAt: z.string().datetime({ offset: true }),
  schemaVersion: z.string().default("1"),
  metadata: z.record(z.unknown()).optional(),
});

/**
 * Validates a graph entity
 */
export function validateGraphEntity(entity: unknown): { success: boolean; data?: AnyGraphEntity; errors?: Array<{ field: string; message: string; code: string }> } {
  const result = AnyGraphEntitySchema.safeParse(entity);
  if (result.success) {
    return { success: true, data: result.data };
  }
  const errors = result.error.issues.map((issue) => ({
    field: issue.path.join("."),
    message: issue.message,
    code: issue.code,
  }));
  return { success: false, errors };
}

/**
 * Validates a graph relationship
 */
export function validateGraphRelationship(relationship: unknown): { success: boolean; data?: GraphRelationship; errors?: Array<{ field: string; message: string; code: string }> } {
  const result = GraphRelationshipSchema.safeParse(relationship);
  if (result.success) {
    return { success: true, data: result.data };
  }
  const errors = result.error.issues.map((issue) => ({
    field: issue.path.join("."),
    message: issue.message,
    code: issue.code,
  }));
  return { success: false, errors };
}

/**
 * Validates that a relationship type is compatible with source/target entity types
 */
export function validateRelationshipCompatibility(
  relationshipType: GraphRelationshipType,
  sourceType: GraphEntityType,
  targetType: GraphEntityType
): { valid: boolean; error?: string } {
  const validCombinations: Record<GraphRelationshipType, Array<[GraphEntityType, GraphEntityType]>> = {
    [GraphRelationshipType.CONTAINS]: [
      [GraphEntityType.REPOSITORY, GraphEntityType.FILE],
    ],
    [GraphRelationshipType.AUTHORED_IN]: [
      [GraphEntityType.COMMIT, GraphEntityType.REPOSITORY],
    ],
    [GraphRelationshipType.MODIFIES]: [
      [GraphEntityType.COMMIT, GraphEntityType.FILE],
    ],
    [GraphRelationshipType.HAS_PARENT]: [
      [GraphEntityType.COMMIT, GraphEntityType.COMMIT],
    ],
    [GraphRelationshipType.INCLUDES_COMMIT]: [
      [GraphEntityType.PULL_REQUEST, GraphEntityType.COMMIT],
    ],
    [GraphRelationshipType.TARGETS_FILE]: [
      [GraphEntityType.PULL_REQUEST, GraphEntityType.FILE],
    ],
    [GraphRelationshipType.HAS_DECISION]: [
      [GraphEntityType.REPOSITORY, GraphEntityType.ENGINEERING_DECISION],
    ],
    [GraphRelationshipType.RELATES_TO]: [
      [GraphEntityType.ENGINEERING_DECISION, GraphEntityType.COMMIT],
      [GraphEntityType.ENGINEERING_DECISION, GraphEntityType.PULL_REQUEST],
      [GraphEntityType.ENGINEERING_DECISION, GraphEntityType.FILE],
      [GraphEntityType.ENGINEERING_DECISION, GraphEntityType.REPOSITORY],
    ],
    [GraphRelationshipType.DERIVED_FROM]: [
      // Any entity can be derived from an event/context
      [GraphEntityType.REPOSITORY, GraphEntityType.ENGINEERING_DECISION],
      [GraphEntityType.COMMIT, GraphEntityType.ENGINEERING_DECISION],
      [GraphEntityType.PULL_REQUEST, GraphEntityType.ENGINEERING_DECISION],
      [GraphEntityType.FILE, GraphEntityType.ENGINEERING_DECISION],
      [GraphEntityType.ENGINEERING_DECISION, GraphEntityType.ENGINEERING_DECISION],
    ],
    [GraphRelationshipType.ASSOCIATED_WITH]: [
      // Generic association - allow any combination but warn
    ],
  };

  const allowed = validCombinations[relationshipType];
  if (!allowed) {
    return { valid: false, error: `Unknown relationship type: ${relationshipType}` };
  }

  // For ASSOCIATED_WITH, allow any combination
  if (relationshipType === GraphRelationshipType.ASSOCIATED_WITH) {
    return { valid: true };
  }

  const isValid = allowed.some(([source, target]) => source === sourceType && target === targetType);
  if (!isValid) {
    return {
      valid: false,
      error: `Relationship type ${relationshipType} not valid for ${sourceType} -> ${targetType}`,
    };
  }

  return { valid: true };
}