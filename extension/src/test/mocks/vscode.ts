// VS Code API mock for testing
const mockVscode = {
  workspace: {
    getConfiguration: jest.fn(() => ({
      get: jest.fn(),
      update: jest.fn().mockResolvedValue(undefined),
    })),
    onDidChangeConfiguration: jest.fn(),
    workspaceFolders: [
      {
        uri: { fsPath: '/test/workspace' },
        name: 'test-workspace',
        index: 0,
      },
    ],
    fsPath: '/test/workspace',
  },
  window: {
    createStatusBarItem: jest.fn(() => ({
      show: jest.fn(),
      hide: jest.fn(),
      dispose: jest.fn(),
      text: '',
      tooltip: '',
      command: '',
      backgroundColor: undefined,
    })),
    showInformationMessage: jest.fn().mockResolvedValue(undefined),
    showWarningMessage: jest.fn().mockResolvedValue(undefined),
    showErrorMessage: jest.fn().mockResolvedValue(undefined),
    activeTextEditor: undefined,
    setStatusBarMessage: jest.fn(),
  },
  commands: {
    executeCommand: jest.fn().mockResolvedValue(undefined),
    registerCommand: jest.fn(() => ({ dispose: jest.fn() })),
  },
  ExtensionContext: jest.fn().mockImplementation(() => ({
    subscriptions: [],
    secrets: {
      get: jest.fn().mockResolvedValue(undefined),
      store: jest.fn().mockResolvedValue(undefined),
      delete: jest.fn().mockResolvedValue(undefined),
    },
    extensionUri: { fsPath: '/test/extension' },
    extensionPath: '/test/extension',
  })),
  StatusBarAlignment: { Left: 1, Right: 2 },
  ThemeColor: jest.fn(),
  Uri: {
    joinPath: jest.fn((...args: string[]) => ({ fsPath: args.join('/') })),
    file: jest.fn((path: string) => ({ fsPath: path })),
  },
  ViewColumn: { One: 1, Two: 2, Three: 3 },
  WebviewPanel: {},
  WebviewView: {},
  Disposable: { dispose: jest.fn() },
  env: {
    clipboard: {
      writeText: jest.fn().mockResolvedValue(undefined),
    },
    openExternal: jest.fn().mockResolvedValue(undefined),
  },
};

export = mockVscode;