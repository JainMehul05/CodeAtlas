import {
  DynamoDBClient,
} from '@aws-sdk/client-dynamodb';
import {
  DynamoDBDocumentClient,
  GetCommand,
} from '@aws-sdk/lib-dynamodb';
import * as crypto from 'crypto';

// Config from Lambda environment variables
const PROJECTS_TABLE = process.env.PROJECTS_TABLE;

const dynamo = DynamoDBDocumentClient.from(
  new DynamoDBClient({ region: 'us-east-1' }),
  { marshallOptions: { removeUndefinedValues: true } },
);

export function hashToken(plaintext: string): string {
  const salt = crypto.randomBytes(16).toString('hex');
  const hash = crypto.scryptSync(plaintext, salt, 64).toString('hex');
  return `${salt}:${hash}`;
}

export function verifyToken(plaintext: string, stored: string): boolean {
  const [salt, hash] = (stored ?? '').split(':');
  if (!salt || !hash) return false;
  const candidate = crypto.scryptSync(plaintext, salt, 64).toString('hex');
  return crypto.timingSafeEqual(Buffer.from(hash, 'hex'), Buffer.from(candidate, 'hex'));
}

function extractToken(headers: Record<string, string | undefined>): string | null {
  const raw = headers?.Authorization ?? headers?.authorization;
  if (!raw) return null;
  const [scheme, token] = raw.split(' ');
  return scheme === 'Bearer' && token ? token : null;
}

export interface AuthResult {
  project?: Record<string, unknown>;
  error?: { error: string; message?: string };
  statusCode?: number;
}

export async function authenticate(headers: Record<string, string | undefined>, projectId: string): Promise<AuthResult> {
  const token = extractToken(headers);
  if (!token) return { error: { error: 'invalid_token', message: 'Missing Authorization header' }, statusCode: 401 };

  const { Item } = await dynamo.send(new GetCommand({ TableName: PROJECTS_TABLE, Key: { projectId } }));

  if (!Item?.apiTokenHash) return { error: { error: 'invalid_token' }, statusCode: 401 };
  if (!verifyToken(token, Item.apiTokenHash)) return { error: { error: 'invalid_token' }, statusCode: 401 };

  return { project: Item };
}