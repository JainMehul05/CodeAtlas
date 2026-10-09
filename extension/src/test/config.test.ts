import * as fs from 'fs';
import * as path from 'path';
import { readConfig, writeConfig, getWorkspaceRoot, FlowSyncConfig, BASE_PORT } from '../config';
import * as vscode from 'vscode';

jest.mock('fs');
jest.mock('path');
jest.mock('vscode');

describe('Config Module', () => {
  const mockWorkspaceRoot = '/test/workspace';
  const mockConfigPath = '/test/workspace/.flowsync.json';
  
  const validConfig: FlowSyncConfig = {
    projectId: 'test-project-id',
    backendUrl: 'https://api.example.com/prod',
    defaultBranch: 'main',
    port: 38475,
  };

  beforeEach(() => {
    jest.clearAllMocks();
    (vscode.workspace.workspaceFolders as any) = [{
      uri: { fsPath: mockWorkspaceRoot },
    }];
    (fs.existsSync as jest.Mock).mockReturnValue(true);
    (fs.readFileSync as jest.Mock).mockReturnValue(JSON.stringify(validConfig));
    (path.dirname as jest.Mock).mockImplementation((p: string) => {
      const parsed = path.parse(p);
      return parsed.dir || '/';
    });
    (path.join as jest.Mock).mockImplementation((...args: string[]) => args.join('/'));
    (path.parse as jest.Mock).mockImplementation((p: string) => {
      const parts = p.split('/');
      return { dir: parts.slice(0, -1).join('/') || '/', base: parts[parts.length - 1] };
    });
    (path.join as jest.Mock).mockImplementation((...args: string[]) => {
      if (args[0] === mockWorkspaceRoot && args[1] === '.flowsync.json') {
        return mockConfigPath;
      }
      return args.join('/');
    });
  });

  describe('readConfig', () => {
    it('returns null when no workspace folder is open', () => {
      (vscode.workspace.workspaceFolders as any) = undefined;
      expect(readConfig()).toBeNull();
    });

    it('returns null when .flowsync.json does not exist', () => {
      (fs.existsSync as jest.Mock).mockReturnValue(false);
      expect(readConfig()).toBeNull();
    });

    it('returns null when .flowsync.json is malformed JSON', () => {
      (fs.readFileSync as jest.Mock).mockReturnValue('invalid json');
      expect(readConfig()).toBeNull();
    });

    it('returns null when required fields are missing', () => {
      (fs.readFileSync as jest.Mock).mockReturnValue(JSON.stringify({ projectId: 'test' }));
      expect(readConfig()).toBeNull();
    });

    it('returns config with default port when port is missing', () => {
      const configWithoutPort = { ...validConfig, port: undefined };
      (fs.readFileSync as jest.Mock).mockReturnValue(JSON.stringify(configWithoutPort));
      const result = readConfig();
      expect(result).not.toBeNull();
      expect(result?.port).toBe(BASE_PORT);
    });

    it('returns valid config when all fields present', () => {
      const result = readConfig();
      expect(result).toEqual(validConfig);
    });
  });

  describe('writeConfig', () => {
    it('writes config to .flowsync.json', () => {
      writeConfig(validConfig);
      expect(fs.writeFileSync).toHaveBeenCalledWith(
        mockConfigPath,
        JSON.stringify(validConfig, null, 2) + '\n',
        'utf-8'
      );
    });

    it('throws when no workspace folder is open', () => {
      (vscode.workspace.workspaceFolders as any) = undefined;
      expect(() => writeConfig(validConfig)).toThrow('No workspace folder open');
    });
  });

  describe('getWorkspaceRoot', () => {
    it('returns workspace root when .git exists', () => {
      (fs.existsSync as jest.Mock).mockImplementation((p: string) => p.includes('.git'));
      const result = getWorkspaceRoot();
      expect(result).toBe(mockWorkspaceRoot);
    });

    it('returns workspace root when .flowsync.json exists', () => {
      (fs.existsSync as jest.Mock).mockImplementation((p: string) => p.includes('.flowsync.json'));
      const result = getWorkspaceRoot();
      expect(result).toBe(mockWorkspaceRoot);
    });

    it('falls back to workspace folder when neither .git nor .flowsync.json found', () => {
      (fs.existsSync as jest.Mock).mockReturnValue(false);
      const result = getWorkspaceRoot();
      expect(result).toBe(mockWorkspaceRoot);
    });

    it('returns null when no workspace folders', () => {
      (vscode.workspace.workspaceFolders as any) = [];
      expect(getWorkspaceRoot()).toBeNull();
    });
  });

  describe('BASE_PORT', () => {
    it('exports the correct base port', () => {
      expect(BASE_PORT).toBe(38475);
    });
  });
});