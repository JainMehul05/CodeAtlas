# CodeAtlas --- Engineering Intelligence Platform

> Connect engineering history, technical decisions, and development
> workflows into searchable context for developers and AI coding
> assistants.

CodeAtlas is an AI-powered engineering intelligence platform designed to
help developers understand not only **what changed** in a codebase, but
also **why it changed**. It combines developer tooling, cloud-based
event processing, retrieval-augmented generation (RAG), and Model
Context Protocol (MCP) integrations.

The repository currently contains a foundation for persistent project
context, an IDE extension, an MCP server, a web dashboard, and AWS
serverless infrastructure. The roadmap describes planned improvements
separately so implemented features are not confused with future work.

## Why CodeAtlas?

Engineering knowledge is often scattered across commits, pull requests,
conversations, documentation, and individual developers' memories. This
makes it difficult to recover decisions, investigate regressions, and
provide AI assistants with reliable project context.

CodeAtlas aims to make that knowledge easier to capture, search, and
connect.

## Core Capabilities

-   **Persistent project context** --- Store structured information
    about engineering decisions, risks, tasks, and code changes.
-   **AI-assisted retrieval** --- Ask natural-language questions about
    available project context and retrieve relevant records.
-   **Developer workflow integration** --- Use a VS Code extension to
    connect the development workflow with the platform.
-   **MCP integration** --- Expose project-context operations through
    structured tools for compatible AI coding assistants.
-   **Web dashboard** --- Explore project activity and context through a
    browser-based interface.
-   **Traceable answers** --- Link answers to available source records
    where supported by the retrieval pipeline.

> **Implementation status:** The current repository is an evolving
> foundation. GitHub webhook ingestion, SQS-based processing, an
> engineering knowledge graph, and CI/CD intelligence are roadmap items
> until implemented and verified.

## Architecture

### Current foundation

``` text
Developer / AI Coding Assistant
        |             |
   VS Code         MCP Client
   Extension       / AI Agent
        |             |
        +------+------+
               |
        Amazon API Gateway
               |
        Ingestion Lambda
          |          |
      DynamoDB       S3
          |
   AI Processing Lambda
          |
     Amazon Bedrock
          |
  Context + Embeddings
          |
       DynamoDB
          |
   Query / Chat Lambdas
          |
     MCP + Dashboard
```

This is a high-level overview of the existing design; consult the
infrastructure code for exact resource configuration and execution
paths.

### Planned event-driven evolution

``` text
GitHub Webhooks + VS Code
             |
        API Gateway
             |
       Ingestion Lambda
             |
          SQS Queue
             |
     AI Processing Lambda
             |
  Engineering Context + Retrieval
             |
       MCP + Dashboard

Failures after retries --> Dead-Letter Queue
```

The queue, dead-letter handling, GitHub webhook ingestion, and related
processing changes are planned milestones, not claims of a deployed
implementation.

## Technology Stack

  Layer                            Technology
  -------------------------------- -----------------------------------------------
  IDE integration                  TypeScript, Node.js, VS Code Extension API
  Frontend                         Next.js 14, React 18, Tailwind CSS, shadcn/ui
  MCP server                       TypeScript, `@modelcontextprotocol/sdk`
  API                              Amazon API Gateway
  Serverless compute               AWS Lambda (Node.js and Python)
  AI models                        Amazon Bedrock Nova Pro and Nova Lite
  Embeddings                       Amazon Titan Text Embeddings
  Data storage                     Amazon DynamoDB
  Raw event archive                Amazon S3
  Infrastructure as code           AWS CDK
  Planned event processing         Amazon SQS and Dead-Letter Queue
  Planned repository integration   GitHub App and webhooks
  Planned CI/CD intelligence       GitHub Actions workflow events

The planned technologies are included to communicate direction and do
not imply that those integrations are already configured.

## Development Roadmap

  -----------------------------------------------------------------------
  Phase                   Focus                   Goal
  ----------------------- ----------------------- -----------------------
  1                       Foundation and cleanup  Secure credentials,
                                                  establish a reliable
                                                  Git baseline, integrate
                                                  shared contracts, align
                                                  branding, and
                                                  strengthen tests

  2                       Reliable event          Add SQS, a DLQ,
                          ingestion               retries, idempotency,
                                                  and GitHub webhook
                                                  ingestion

  3                       Engineering knowledge   Connect repositories,
                          graph                   commits, files, pull
                                                  requests, and related
                                                  engineering entities

  4                       CI/CD intelligence      Associate workflow
                                                  failures and deployment
                                                  events with code
                                                  changes

  5                       Advanced AI and RAG     Improve retrieval,
                                                  ranking, grounding, and
                                                  source attribution

  6                       MCP integration         Expand tools for
                                                  AI-assisted engineering
                                                  workflows

  7                       Multi-tenancy and       Add organization
                          security                isolation,
                                                  authorization,
                                                  role-based access, and
                                                  audit controls

  8                       Observability           Add tracing, metrics,
                                                  alarms, and operational
                                                  dashboards

  9                       Testing and performance Validate integrations,
                                                  recovery, retrieval
                                                  quality, latency, and
                                                  cost

  10                      Deployment and          Improve CI/CD,
                          portfolio polish        deployment
                                                  documentation, demos,
                                                  and project
                                                  presentation
  -----------------------------------------------------------------------

**Current priority:** complete Phase 1 remediation before starting the
SQS and DLQ work in Phase 2.

## Repository Structure

``` text
CodeAtlas/
├── extension/              # VS Code extension
├── frontend/               # Next.js dashboard
├── infra/                  # AWS CDK and Lambda infrastructure
├── mcp-server/             # MCP server
├── packages/
│   └── flowsync-shared/    # Shared TypeScript contracts and utilities
├── design/                 # Design assets
├── DOCUMENTATION/          # Architecture and technical documentation
├── .gitignore
└── README.md
```

Some internal package and infrastructure identifiers retain the original
`flowsync` naming to avoid unnecessary compatibility-breaking changes.

## Getting Started

### Prerequisites

-   Node.js and npm versions compatible with the package manifests
-   Python 3.12 for Python-based Lambda components
-   Visual Studio Code for extension development
-   AWS credentials and permissions for backend integration tests
-   Access to the required Amazon Bedrock models for AI functionality

### 1. Clone the repository

``` bash
git clone https://github.com/JainMehul05/CodeAtlas.git
cd CodeAtlas
```

### 2. Review component setup

The repository contains multiple independently configured components.
Read the relevant package manifests and component documentation before
installing dependencies or running commands. Install dependencies from
the appropriate project directory.

### 3. Configure environment variables

Review available `.env.example` files and create local configuration
files as required by each component.

-   Never commit real API tokens, AWS credentials, private keys, or
    production secrets.
-   Do not put confidential values in `NEXT_PUBLIC_*` variables: these
    are exposed to browser-side code.
-   Use placeholders in example environment files.
-   Rotate any credential that has previously been exposed.

### 4. Build and test

Use the build and test scripts defined by each component's package
configuration. Verify the shared package and its consumers after
integration changes. Run CDK synthesis to validate infrastructure
definitions before considering deployment.

> These are setup guidelines, not a claim that a clean installation or
> end-to-end workflow has been verified on every machine.

## Engineering Principles

-   **Reliability before complexity:** strengthen the existing
    serverless architecture before introducing infrastructure without a
    demonstrated need.
-   **Traceable AI:** ground answers in available project records and
    provide source attribution where supported.
-   **Asynchronous processing:** avoid blocking ingestion on potentially
    slow AI processing; add durable queues and controlled retries as
    part of the reliability roadmap.
-   **Security by design:** keep secrets out of source control and
    enforce appropriate access boundaries.
-   **Measured improvements:** justify architectural changes through
    tests and evidence about reliability, retrieval quality, latency,
    and cost.

## Project Direction

CodeAtlas is intended to evolve from persistent project context into a
broader engineering-intelligence layer. The longer-term goal is to
connect code changes, technical decisions, CI/CD outcomes, and
deployments so developers and AI assistants can investigate engineering
questions using evidence from the project.

The roadmap is incremental. Features will be described as implemented
only after the corresponding code, tests, and integration have been
verified.

## Maintainer

**Mehul Jain**

Repository:
[JainMehul05/CodeAtlas](https://github.com/JainMehul05/CodeAtlas)
