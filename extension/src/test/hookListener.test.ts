import { log } from '../logger';

jest.mock('http');
jest.mock('net');
jest.mock('../logger');

type HookCallback = (branch: string, remoteRef?: string) => void;

describe('Hook Listener Module - findAvailablePort', () => {
  const mockLog = log as jest.Mocked<typeof log>;
  
  beforeEach(() => {
    jest.resetModules();
    jest.clearAllMocks();
    mockLog.step.mockImplementation(() => {});
    mockLog.ok.mockImplementation(() => {});
    mockLog.info.mockImplementation(() => {});
    mockLog.warn.mockImplementation(() => {});
    mockLog.error.mockImplementation(() => {});
  });

  it('returns the preferred port when it is free', async () => {
    const { findAvailablePort } = await import('../hookListener');
    const net = await import('net');
    
    const mockProbe = {
      once: jest.fn((event, cb) => {
        if (event === 'listening') cb();
      }),
      listen: jest.fn(),
      close: jest.fn((cb) => cb()),
    };
    (net.createServer as jest.Mock).mockReturnValue(mockProbe);

    const port = await findAvailablePort(38475);

    expect(port).toBe(38475);
    expect(net.createServer).toHaveBeenCalled();
  });

  it('finds the next available port when preferred is taken', async () => {
    const { findAvailablePort } = await import('../hookListener');
    const net = await import('net');
    
    let createServerCallCount = 0;
    const mockProbes = [
      // First probe - port 38475 taken (error)
      {
        once: jest.fn((event: string, cb: () => void) => {
          if (event === 'error') cb();
        }),
        listen: jest.fn(),
        close: jest.fn((cb: () => void) => cb()),
      },
      // Second probe - port 38476 free (listening)
      {
        once: jest.fn((event: string, cb: () => void) => {
          if (event === 'listening') cb();
        }),
        listen: jest.fn(),
        close: jest.fn((cb: () => void) => cb()),
      },
    ];
    (net.createServer as jest.Mock).mockImplementation(() => {
      return mockProbes[createServerCallCount++];
    });

    const port = await findAvailablePort(38475);

    expect(port).toBe(38476);
    expect(net.createServer).toHaveBeenCalledTimes(2);
  }, 15000);

  it('throws when no port available in range', async () => {
    const { findAvailablePort } = await import('../hookListener');
    const net = await import('net');
    
    const mockProbe = {
      once: jest.fn((event, cb) => {
        if (event === 'error') cb();
      }),
      listen: jest.fn(),
      close: jest.fn(),
    };
    (net.createServer as jest.Mock).mockReturnValue(mockProbe);

    await expect(findAvailablePort(38475)).rejects.toThrow(
      'FlowSync: no available port found in range 38475–38574'
    );
    expect(net.createServer).toHaveBeenCalledTimes(100);
  }, 15000);
});

describe('Hook Listener Module - startHookListener', () => {
  let mockServer: any;
  let mockListen: jest.Mock;
  let mockOn: jest.Mock;
  let requestHandler: (req: any, res: any) => void;
  let http: any;
  let net: any;
  let startHookListener: any;
  let stopHookListener: any;
  let getActivePort: any;

  beforeEach(async () => {
    jest.resetModules();
    jest.clearAllMocks();
    
    http = await import('http');
    net = await import('net');
    const hookListener = await import('../hookListener');
    startHookListener = hookListener.startHookListener;
    stopHookListener = hookListener.stopHookListener;
    getActivePort = hookListener.getActivePort;
    
    requestHandler = undefined as any;
    
    mockListen = jest.fn((port: number, host: string, cb: () => void) => cb());
    mockOn = jest.fn();
    mockServer = {
      listen: mockListen,
      on: mockOn,
      close: jest.fn(),
    };
    (http.createServer as jest.Mock).mockImplementation((handler: any) => {
      requestHandler = handler;
      return mockServer;
    });
    
    // Mock net.createServer to always return a "free" port for findAvailablePort
    const mockProbe = {
      once: jest.fn((event: string, cb: () => void) => {
        if (event === 'listening') cb();
      }),
      listen: jest.fn(),
      close: jest.fn((cb: () => void) => cb()),
    };
    (net.createServer as jest.Mock).mockReturnValue(mockProbe);
    
    const logger = await import('../logger');
    const mockLog = logger.log as jest.Mocked<typeof logger.log>;
    mockLog.step.mockImplementation(() => {});
    mockLog.ok.mockImplementation(() => {});
    mockLog.info.mockImplementation(() => {});
    mockLog.warn.mockImplementation(() => {});
    mockLog.error.mockImplementation(() => {});
  });

  it('creates HTTP server and starts listening', async () => {
    const callback = jest.fn();
    
    const port = await startHookListener(callback, 38475);

    expect(http.createServer).toHaveBeenCalled();
    expect(mockListen).toHaveBeenCalledWith(38475, '127.0.0.1', expect.any(Function));
    expect(port).toBe(38475);
  });

it('handles POST /flowsync-hook with valid push event', async () => {
      const callback = jest.fn();
      
      await startHookListener(callback, 38475);

      expect(requestHandler).toBeDefined();

      const mockReq = {
        method: 'POST',
        url: '/flowsync-hook',
        on: jest.fn((event: string, cb: (data: Buffer) => void) => {
          if (event === 'data') cb(Buffer.from('{"event": "push", "branch": "main"}'));
          if (event === 'end') cb(Buffer.from(''));
        }),
      };
      const mockRes = {
        writeHead: jest.fn(),
        end: jest.fn(),
      };

      requestHandler!(mockReq, mockRes);

      expect(callback).toHaveBeenCalledWith('main');
      expect(mockRes.writeHead).toHaveBeenCalledWith(200, { 'Content-Type': 'application/json' });
      expect(mockRes.end).toHaveBeenCalledWith(JSON.stringify({ status: 'received' }));
    });

    it('handles post-push event', async () => {
      const callback = jest.fn();
      
      await startHookListener(callback, 38475);

      const mockReq = {
        method: 'POST',
        url: '/flowsync-hook',
        on: jest.fn((event: string, cb: (data: Buffer) => void) => {
          if (event === 'data') cb(Buffer.from('{"event": "post-push", "branch": "feature/test"}'));
          if (event === 'end') cb(Buffer.from(''));
        }),
      };
      const mockRes = {
        writeHead: jest.fn(),
        end: jest.fn(),
      };

      requestHandler!(mockReq, mockRes);

      expect(callback).toHaveBeenCalledWith('feature/test');
    });

  it('rejects invalid payload', async () => {
    const callback = jest.fn();
    
    await startHookListener(callback, 38475);

    const mockReq = {
      method: 'POST',
      url: '/flowsync-hook',
      on: jest.fn((event: string, cb: (data: Buffer) => void) => {
        if (event === 'data') cb(Buffer.from('{"event": "invalid"}'));
        if (event === 'end') cb(Buffer.from(''));
      }),
    };
    const mockRes = {
      writeHead: jest.fn(),
      end: jest.fn(),
    };

    requestHandler!(mockReq, mockRes);

    expect(callback).not.toHaveBeenCalled();
    expect(mockRes.writeHead).toHaveBeenCalledWith(400, { 'Content-Type': 'application/json' });
    expect(mockRes.end).toHaveBeenCalledWith(JSON.stringify({ error: 'invalid payload' }));
  });

  it('handles malformed JSON', async () => {
    const callback = jest.fn();
    
    await startHookListener(callback, 38475);

    const mockReq = {
      method: 'POST',
      url: '/flowsync-hook',
      on: jest.fn((event: string, cb: (data: Buffer) => void) => {
        if (event === 'data') cb(Buffer.from('invalid json'));
        if (event === 'end') cb(Buffer.from(''));
      }),
    };
    const mockRes = {
      writeHead: jest.fn(),
      end: jest.fn(),
    };

    requestHandler!(mockReq, mockRes);

    expect(mockRes.writeHead).toHaveBeenCalledWith(400, { 'Content-Type': 'application/json' });
    expect(mockRes.end).toHaveBeenCalledWith(JSON.stringify({ error: 'invalid json' }));
  });

  it('returns 404 for unknown routes', async () => {
    await startHookListener(jest.fn(), 38475);

    const mockReq = { method: 'GET', url: '/unknown' };
    const mockRes = { writeHead: jest.fn(), end: jest.fn() };

    requestHandler!(mockReq, mockRes);

    expect(mockRes.writeHead).toHaveBeenCalledWith(404);
    expect(mockRes.end).toHaveBeenCalled();
  });

  it('returns existing server if already running', async () => {
    const callback = jest.fn();
    
    await startHookListener(callback, 38475);
    const port1 = await startHookListener(callback, 38475);

    expect(port1).toBe(38475);
    expect(http.createServer).toHaveBeenCalledTimes(1);
  });
});

describe('Hook Listener Module - stopHookListener and getActivePort', () => {
  let mockServer: any;
  let mockListen: jest.Mock;
  let mockOn: jest.Mock;
  let requestHandler: (req: any, res: any) => void;
  let http: any;
  let net: any;
  let startHookListener: any;
  let stopHookListener: any;
  let getActivePort: any;

  beforeEach(async () => {
    jest.resetModules();
    jest.clearAllMocks();
    
    http = await import('http');
    net = await import('net');
    const hookListener = await import('../hookListener');
    startHookListener = hookListener.startHookListener;
    stopHookListener = hookListener.stopHookListener;
    getActivePort = hookListener.getActivePort;
    
    requestHandler = undefined as any;
    
    mockListen = jest.fn((port: number, host: string, cb: () => void) => cb());
    mockOn = jest.fn();
    mockServer = {
      listen: mockListen,
      on: mockOn,
      close: jest.fn(),
    };
    (http.createServer as jest.Mock).mockImplementation((handler: any) => {
      requestHandler = handler;
      return mockServer;
    });
    
    // Mock net.createServer to always return a "free" port for findAvailablePort
    const mockProbe = {
      once: jest.fn((event: string, cb: () => void) => {
        if (event === 'listening') cb();
      }),
      listen: jest.fn(),
      close: jest.fn((cb: () => void) => cb()),
    };
    (net.createServer as jest.Mock).mockReturnValue(mockProbe);
    
    const logger = await import('../logger');
    const mockLog = logger.log as jest.Mocked<typeof logger.log>;
    mockLog.step.mockImplementation(() => {});
    mockLog.ok.mockImplementation(() => {});
    mockLog.info.mockImplementation(() => {});
    mockLog.warn.mockImplementation(() => {});
    mockLog.error.mockImplementation(() => {});
  });

  it('closes the server when running', async () => {
    await startHookListener(jest.fn(), 38475);
    stopHookListener();

    expect(mockServer.close).toHaveBeenCalled();
  });

  it('does nothing when server not running', async () => {
    // Need to reset the module state - stopHookListener checks the global server variable
    // Since we can't easily reset it, we test that it doesn't throw
    expect(() => stopHookListener()).not.toThrow();
  });

  it('returns active port when server running', async () => {
    await startHookListener(jest.fn(), 38475);
    const port = getActivePort();

    expect(port).toBe(38475);
  });

  it('returns null when server not running', async () => {
    // The global state from previous tests may have a port set
    // We can't easily reset it, so we just verify the function exists
    const port = getActivePort();
    expect(port).toBeNull();
  });
});