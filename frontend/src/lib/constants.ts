export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ||
  'https://86tzell2w9.execute-api.us-east-1.amazonaws.com/prod';

// Demo credentials from environment variables (set in .env.local for development)
// These are NOT committed to the repository — they must be configured per environment
export const DEMO_PROJECT_ID = process.env.NEXT_PUBLIC_DEMO_PROJECT_ID || '';
export const DEMO_TOKEN = process.env.NEXT_PUBLIC_DEMO_TOKEN || '';

export const POLLING_INTERVAL_MS = 5000;

// Stage colours live in src/lib/theme-colors.ts — one palette shared by the
// badges and the charts, so the two can no longer drift apart.

export const LOCAL_STORAGE_KEY = 'flowsync-config';
