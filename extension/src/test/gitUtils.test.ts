import { execSync } from 'child_process';
import { getDiff, getLastCommitInfo, getGitUserName, getMergeInfo, CommitInfo } from '../gitUtils';
import { getWorkspaceRoot } from '../config';
import { log } from '../logger';

jest.mock('child_process');
jest.mock('../config');
jest.mock('../logger');

describe('Git Utils Module', () => {
  const mockWorkspaceRoot = '/test/workspace';
  
  beforeEach(() => {
    jest.clearAllMocks();
    (getWorkspaceRoot as jest.Mock).mockReturnValue(mockWorkspaceRoot);
    (log.step as jest.Mock).mockImplementation(() => {});
    (log.ok as jest.Mock).mockImplementation(() => {});
    (log.warn as jest.Mock).mockImplementation(() => {});
    (log.error as jest.Mock).mockImplementation(() => {});
    (log.info as jest.Mock).mockImplementation(() => {});
  });

  describe('getDiff', () => {
    it('returns push range diff when remoteRef provided and valid', () => {
      const remoteRef = 'abc123abc123abc123abc123abc123abc123abc1';
      (execSync as jest.Mock).mockReturnValue('diff content');
      
      const result = getDiff(remoteRef);
      
      expect(execSync).toHaveBeenCalledWith(
        `git diff ${remoteRef} HEAD`,
        { cwd: mockWorkspaceRoot, encoding: 'utf-8' }
      );
      expect(result).toBe('diff content');
    });

    it('skips remoteRef when it is all zeros', () => {
      const remoteRef = '0000000000000000000000000000000000000000';
      (execSync as jest.Mock)
        .mockReturnValueOnce('commit-hash parent1 parent2') // rev-list --parents
        .mockReturnValueOnce('merge-base-hash') // merge-base
        .mockReturnValueOnce('merge diff content'); // diff from merge-base
      
      const result = getDiff(remoteRef);
      
      expect(result).toBe('merge diff content');
    });

    it('detects merge commit and uses merge-base diff', () => {
      (execSync as jest.Mock)
        .mockReturnValueOnce('commit-hash parent1 parent2') // rev-list --parents
        .mockReturnValueOnce('merge-base-hash') // merge-base
        .mockReturnValueOnce('merge diff content'); // diff from merge-base
      
      const result = getDiff(undefined);
      
      expect(result).toBe('merge diff content');
    });

    it('falls back to HEAD~1 HEAD diff for non-merge commits', () => {
      (execSync as jest.Mock)
        .mockReturnValueOnce('commit-hash parent1') // rev-list --parents (1 parent)
        .mockReturnValueOnce('regular diff content'); // diff HEAD~1 HEAD
      
      const result = getDiff(undefined);
      
      expect(result).toBe('regular diff content');
    });

    it('falls back to git show when HEAD~1 not available', () => {
      (execSync as jest.Mock)
        .mockReturnValueOnce('commit-hash parent1') // rev-list
        .mockReturnValueOnce(null) // diff HEAD~1 HEAD fails
        .mockReturnValueOnce('show diff content'); // git show
      
      const result = getDiff(undefined);
      
      expect(result).toBe('show diff content');
    });

    it('returns null when all diff strategies fail', () => {
      (execSync as jest.Mock)
        .mockReturnValueOnce('commit-hash parent1') // rev-list
        .mockReturnValueOnce(null) // diff HEAD~1 HEAD fails
        .mockReturnValueOnce(null); // git show fails
      
      const result = getDiff(undefined);
      
      expect(result).toBeNull();
    });

    it('truncates diff to 50000 characters', () => {
      const longDiff = 'x'.repeat(60000);
      (execSync as jest.Mock)
        .mockReturnValueOnce('commit-hash parent1') // rev-list
        .mockReturnValueOnce(longDiff); // diff HEAD~1 HEAD
      
      const result = getDiff(undefined);
      
      expect(result!.length).toBe(50000);
    });

    it('returns null when no workspace root', () => {
      (getWorkspaceRoot as jest.Mock).mockReturnValue(null);
      
      const result = getDiff(undefined);
      
      expect(result).toBeNull();
    });
  });

  describe('getLastCommitInfo', () => {
    it('parses commit info correctly', () => {
      const rawOutput = 'abc123def456\nCommit message\nAuthor Name\n2026-01-01T12:00:00+00:00';
      (execSync as jest.Mock).mockReturnValue(rawOutput);
      
      const result = getLastCommitInfo();
      
      expect(result).toEqual({
        commitHash: 'abc123def456',
        message: 'Commit message',
        author: 'Author Name',
        timestamp: '2026-01-01T12:00:00+00:00',
      });
    });

    it('returns null when git log fails', () => {
      (execSync as jest.Mock).mockReturnValue(null);
      
      const result = getLastCommitInfo();
      
      expect(result).toBeNull();
    });

    it('returns null when parse fails', () => {
      (execSync as jest.Mock).mockReturnValue('incomplete output');
      
      const result = getLastCommitInfo();
      
      expect(result).toBeNull();
    });
  });

  describe('getGitUserName', () => {
    it('returns git user name', () => {
      (execSync as jest.Mock).mockReturnValue('Test User');
      
      const result = getGitUserName();
      
      expect(result).toBe('Test User');
    });

    it('returns null when git config fails', () => {
      (execSync as jest.Mock).mockReturnValue(null);
      
      const result = getGitUserName();
      
      expect(result).toBeNull();
    });
  });

  describe('getMergeInfo', () => {
    it('returns isMerge: false for non-merge commits', () => {
      (execSync as jest.Mock).mockReturnValue('commit-hash parent1');
      
      const result = getMergeInfo();
      
      expect(result).toEqual({ isMerge: false, sourceBranch: null });
    });

    it('detects merge commit and extracts source branch', () => {
      (execSync as jest.Mock)
        .mockReturnValueOnce('commit-hash parent1 parent2') // rev-list --parents
        .mockReturnValueOnce('feature-branch'); // name-rev
      
      const result = getMergeInfo();
      
      expect(result.isMerge).toBe(true);
      expect(result.sourceBranch).toBe('feature-branch');
    });

    it('normalizes source branch name', () => {
      (execSync as jest.Mock)
        .mockReturnValueOnce('commit-hash parent1 parent2')
        .mockReturnValueOnce('remotes/origin/feature-branch~1');
      
      const result = getMergeInfo();
      
      expect(result.sourceBranch).toBe('feature-branch');
    });

    it('handles undefined name-rev output', () => {
      (execSync as jest.Mock)
        .mockReturnValueOnce('commit-hash parent1 parent2')
        .mockReturnValueOnce('undefined');
      
      const result = getMergeInfo();
      
      expect(result.isMerge).toBe(true);
      expect(result.sourceBranch).toBeNull();
    });
  });
});