// Setup file for Jest - mocks AWS SDK modules before any tests run

jest.mock('@aws-sdk/client-dynamodb', () => ({
  DynamoDBClient: jest.fn().mockImplementation(() => ({
    send: jest.fn(),
  })),
}));

jest.mock('@aws-sdk/lib-dynamodb', () => {
  const mockSend = jest.fn().mockImplementation((command) => {
    // The command object from @aws-sdk/lib-dynamodb has the input as a property
    // Check for GetCommand on test-projects table
    const tableName = command?.input?.TableName;
    if (tableName === 'test-projects') {
      return Promise.resolve({
        Item: {
          projectId: 'test-project',
          apiTokenHash: 'salt:hash',
        },
      });
    }
    // Mock PutCommand for events table
    if (tableName === 'test-events') {
      return Promise.resolve({});
    }
    // Mock PutCommand for audit table
    if (tableName === 'test-audit') {
      return Promise.resolve({});
    }
    return Promise.resolve({});
  });

  return {
    DynamoDBDocumentClient: {
      from: jest.fn().mockReturnValue({
        send: mockSend,
      }),
    },
    PutCommand: jest.fn(),
    GetCommand: jest.fn(),
    UpdateCommand: jest.fn(),
    QueryCommand: jest.fn(),
    ScanCommand: jest.fn(),
  };
});

jest.mock('@aws-sdk/client-s3', () => ({
  S3Client: jest.fn().mockImplementation(() => ({
    send: jest.fn().mockResolvedValue({}),
  })),
  PutObjectCommand: jest.fn(),
  GetObjectCommand: jest.fn(),
  DeleteObjectCommand: jest.fn(),
}));

jest.mock('@aws-sdk/client-sqs', () => ({
  SQSClient: jest.fn().mockImplementation(() => ({
    send: jest.fn().mockResolvedValue({}),
  })),
  SendMessageCommand: jest.fn(),
  ReceiveMessageCommand: jest.fn(),
  DeleteMessageCommand: jest.fn(),
}));

jest.mock('@flowsync/shared', () => {
  const actual = jest.requireActual('@flowsync/shared');
  return {
    ...actual,
    validateEvent: jest.fn(),
    createEvent: jest.fn(),
  };
});