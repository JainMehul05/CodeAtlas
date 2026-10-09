module.exports = {
  preset: "ts-jest",
  testEnvironment: "node",
  roots: ["<rootDir>/test"],
  testMatch: ["**/*.test.ts"],
  moduleFileExtensions: ["ts", "js", "json"],
  collectCoverageFrom: [
    "index.ts",
    "!**/test/**/*.test.ts",
  ],
  coverageDirectory: "coverage",
  verbose: true,
  setupFilesAfterEnv: ["<rootDir>/test/setup.js"],
  moduleNameMapper: {
    "^\\.\\./index$": "<rootDir>/index.ts",
  },
  globals: {
    "ts-jest": {
      diagnostics: {
        ignoreCodes: [151002, 18048],
      },
    },
  },
};