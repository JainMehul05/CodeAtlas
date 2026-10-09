module.exports = {
  preset: "ts-jest",
  testEnvironment: "node",
  roots: ["<rootDir>/src/test"],
  testMatch: ["**/*.test.ts"],
  moduleFileExtensions: ["ts", "js", "json"],
  collectCoverageFrom: [
    "src/**/*.ts",
    "!src/test/**/*.test.ts",
  ],
  coverageDirectory: "coverage",
  verbose: true,
  setupFilesAfterEnv: ["<rootDir>/src/test/setup.ts"],
};