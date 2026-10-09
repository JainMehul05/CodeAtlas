# CodeAtlas

## AI-Powered Engineering Memory and Repository Intelligence

> **CodeAtlas helps developers and AI coding assistants recover the
> context behind a codebase---what changed, why it changed, and how that
> change relates to the rest of the engineering workflow.**

CodeAtlas is an engineering-intelligence platform I am developing to
make project knowledge easier to capture, search, and use. It brings
together development activity, persistent engineering context,
AI-assisted retrieval, a VS Code extension, an MCP server, and a
cloud-based backend.

The long-term goal is to give developers and AI assistants a dependable
knowledge layer for a repository: one that can answer questions using
recorded project context instead of relying only on the current
conversation or the files currently open in an editor.

> **Project status:** CodeAtlas is under active development. This README
> distinguishes the current repository foundation from capabilities
> planned for later phases. A feature should be considered complete only
> when its implementation and tests have been verified.

------------------------------------------------------------------------

## Table of Contents

-   [The Problem](#the-problem)
-   [The CodeAtlas Approach](#the-codeatlas-approach)
-   [What the Platform Is Designed to
    Do](#what-the-platform-is-designed-to-do)
-   [How It Works](#how-it-works)
-   [System Architecture](#system-architecture)
-   [AI and Retrieval Design](#ai-and-retrieval-design)
-   [MCP and AI Coding Assistants](#mcp-and-ai-coding-assistants)
-   [Technology Stack](#technology-stack)
-   [Current Foundation and Planned
    Work](#current-foundation-and-planned-work)
-   [Repository Layout](#repository-layout)
-   [Local Development](#local-development)
-   [Configuration and Security](#configuration-and-security)
-   [Testing and Quality Strategy](#testing-and-quality-strategy)
-   [Roadmap](#roadmap)
-   [Engineering Principles](#engineering-principles)
-   [Project Ownership and
    Contributions](#project-ownership-and-contributions)

------------------------------------------------------------------------

## The Problem

Modern software projects produce more engineering knowledge than can
comfortably fit in a developer's memory. Important context ends up
distributed across:

-   Git commits and code diffs
-   Pull requests and review discussions
-   Architecture notes and technical documentation
-   Bug investigations and implementation decisions
-   CI/CD workflow results and deployment history
-   Conversations with AI coding assistants

This fragmentation creates several recurring problems.

### 1. Code explains what exists, but not always why

A function or configuration may show the current implementation without
explaining the decision that led to it. When a developer returns to the
code weeks later, they may have to reconstruct the reasoning from
scratch.

### 2. Project context disappears between sessions

AI coding assistants can be helpful within a single session, but
important decisions and constraints may not be available in a later
conversation. Repeating the same explanations wastes time and can lead
to inconsistent suggestions.

### 3. Engineering investigations require manual correlation

When a bug appears after a change, a developer may need to search
commits, inspect files, read documentation, and compare workflow runs
separately. Connecting these sources manually makes investigations
slower.

### 4. More context does not automatically mean better AI answers

Sending a large amount of unrelated repository content to a model can
increase latency and cost while making answers less focused. A useful
engineering assistant needs to retrieve relevant evidence, preserve its
source, and avoid presenting unsupported guesses as facts.

## The CodeAtlas Approach

CodeAtlas is being built around a persistent engineering-context layer.

Instead of treating every AI interaction as an isolated conversation,
the platform aims to capture useful engineering events and context,
store them in a searchable form, and make relevant information available
to developers and compatible AI tools.

The intended workflow is:

1.  **Capture** useful project activity and engineering context.
2.  **Normalize** incoming information into consistent event and context
    structures.
3.  **Store** the information with project and source metadata.
4.  **Retrieve** the most relevant context for a developer's question.
5.  **Explain** the result with supporting records where available.
6.  **Connect** code changes to related engineering events as repository
    and CI/CD integrations mature.

The objective is not to replace Git, code review, documentation, or a
developer's judgment. It is to make those sources easier to connect and
query.

## What the Platform Is Designed to Do

### Persistent engineering memory

Keep useful project context available beyond a single coding session.
Context can include decisions, changes, tasks, risks, and other
structured engineering records supported by the ingestion pipeline.

### Natural-language context search

Allow a developer or an AI assistant to ask a question in ordinary
language and retrieve relevant stored project information rather than
manually searching every source.

### VS Code workflow integration

Provide an IDE entry point for working with project context. The
extension is intended to reduce friction by connecting development
activity to the backend instead of requiring developers to switch tools
for every operation.

### MCP tools for coding assistants

Expose selected CodeAtlas operations through the Model Context Protocol
(MCP), allowing compatible clients to retrieve or record engineering
context through structured tool calls.

### Browser-based project dashboard

Provide a web interface for viewing project activity and context and for
interacting with supported search or chat capabilities.

### Source-aware AI responses

Where retrieval results provide source metadata, responses should make
it possible to inspect the records behind an answer. This is important
for debugging and engineering work, where an answer needs to be
verifiable---not merely plausible.

### Future: repository and delivery intelligence

The roadmap extends the context layer to GitHub pull requests, workflow
runs, deployments, and relationships among engineering entities. These
integrations are planned and should not be considered available until
implemented and tested.

------------------------------------------------------------------------

## How It Works

At a conceptual level, CodeAtlas is divided into five parts.

### 1. Developer interfaces

The VS Code extension and web dashboard provide ways to interact with
project context. The MCP server provides a tool-based interface for
compatible AI clients.

### 2. API and ingestion

An API Gateway and ingestion Lambda form the current cloud entry point
for supported events and requests. Ingestion logic validates and routes
incoming data to the appropriate storage or processing path.

### 3. Persistent storage

DynamoDB stores structured project context and related records. S3 is
used for raw-event archival in the existing architecture. Keeping raw
inputs separate from processed context supports inspection and future
reprocessing workflows.

### 4. AI processing and retrieval

Python-based Lambda components integrate with Amazon Bedrock for AI
processing. The retrieval path uses stored context and embeddings to
find information relevant to a query. The exact retrieval behavior is
defined by the current implementation and may evolve as evaluation and
ranking improve.

### 5. Query and response delivery

Query, chat, and MCP components expose available context to the
dashboard and supported AI clients. Responses should preserve relevant
source information whenever the underlying records provide it.

------------------------------------------------------------------------

## System Architecture

### Current architectural foundation

The following diagram summarizes the repository's intended high-level
component relationships. It is not a substitute for the CDK definitions
or a claim that every end-to-end path has been deployed and verified.

``` text
┌───────────────────────┐      ┌────────────────────────┐
│ VS Code Extension     │      │ MCP-Compatible Client  │
│ Developer Workflow    │      │ AI Coding Assistant    │
└───────────┬───────────┘      └────────────┬───────────┘
            │                               │
            │                               │ MCP tools
            ▼                               ▼
┌────────────────────────────────────────────────────────┐
│                 CodeAtlas Interfaces                   │
│              Extension / MCP / Web UI                  │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
                  ┌───────────────────┐
                  │ Amazon API Gateway│
                  └─────────┬─────────┘
                            │
                            ▼
                  ┌───────────────────┐
                  │ Ingestion Lambda  │
                  └──────┬────────┬───┘
                         │        │
                         ▼        ▼
                 ┌────────────┐ ┌─────────────┐
                 │ DynamoDB   │ │ Amazon S3   │
                 │ Context /  │ │ Raw Events  │
                 │ Event Data │ │ Archive     │
                 └─────┬──────┘ └─────────────┘
                       │
                       ▼
                ┌─────────────────┐
                │ AI Processing   │
                │ Lambda          │
                └────────┬────────┘
                         │
                         ▼
                ┌─────────────────┐
                │ Amazon Bedrock  │
                │ Model Inference │
                └────────┬────────┘
                         │
                         ▼
                ┌─────────────────┐
                │ Context and     │
                │ Embedding Store │
                └────────┬────────┘
                         │
                         ▼
                ┌─────────────────┐
                │ Query / Chat    │
                │ / MCP Responses │
                └─────────────────┘
```

### Planned reliability architecture

The next major backend improvement is to decouple ingestion from
longer-running AI processing. The intended evolution introduces a
durable queue, bounded retries, and a dead-letter queue for events that
cannot be processed successfully.

``` text
GitHub Webhooks / VS Code Events
                 │
                 ▼
          API Gateway
                 │
                 ▼
          Ingestion Lambda
                 │
                 ▼
             Amazon SQS
                 │
                 ▼
        AI Processing Lambda
                 │
                 ▼
      Validate → Enrich → Store
                 │
                 ▼
        Search / MCP / Dashboard

Repeated processing failures
                 │
                 ▼
        Dead-Letter Queue (DLQ)
```

**Planned, not yet assumed to exist:** SQS decoupling, DLQ handling,
GitHub App/webhook ingestion, and full CI/CD event correlation.

------------------------------------------------------------------------

## AI and Retrieval Design

CodeAtlas combines stored engineering context with AI-assisted
retrieval. The intended design emphasizes relevance, traceability, and
practical engineering usefulness.

### Retrieval-augmented generation

Retrieval-augmented generation (RAG) uses relevant records retrieved
from a knowledge store as context for a model response.

A typical query path is:

1.  Receive a developer's question.
2.  Convert or interpret the question for retrieval.
3.  Search the available context and embedding data.
4.  Select relevant records.
5.  Provide the retrieved context to the model.
6.  Return the response with supporting source information when
    available.

This approach is designed to reduce dependence on model-only recall. It
does not guarantee correctness; answer quality depends on the
completeness of stored information, retrieval quality, and the model's
use of the retrieved evidence.

### Embeddings and semantic similarity

Embeddings represent text as numeric vectors that can be compared for
semantic similarity. The existing stack includes Amazon Titan Text
Embeddings. The current retrieval implementation and its limitations
should be verified in code before making performance claims.

### Model integration

The existing project foundation references Amazon Bedrock models,
including Nova Pro and Nova Lite, for AI processing. Model selection
should eventually be measured against task quality, response latency,
and cost rather than assumed to be optimal.

### Planned retrieval improvements

-   Hybrid lexical and semantic retrieval
-   Stronger filtering by project, repository, branch, event type, and
    time
-   Reranking to improve the order of retrieved records
-   Relationship-aware retrieval across commits, files, pull requests,
    and workflow runs
-   Evaluation datasets for relevance, groundedness, and citation
    quality
-   Regression tests for prompts and retrieval behavior

These are development goals, not a statement that all of them are
implemented.

------------------------------------------------------------------------

## MCP and AI Coding Assistants

The Model Context Protocol provides a standardized way for compatible AI
clients to call tools exposed by an MCP server.

The CodeAtlas MCP server is intended to expose engineering context
operations without requiring each AI client to implement a separate
integration. The current project describes tools for recording and
searching context, retrieving project context, viewing recent changes,
and accessing events. Tool availability and behavior should be checked
against the current MCP server implementation.

The intended interaction looks like this:

``` text
Developer asks a project question
              │
              ▼
       AI Coding Assistant
              │
              ▼
          MCP Client
              │
              ▼
       CodeAtlas MCP Server
              │
              ▼
     CodeAtlas Query / Context
              │
              ▼
   Relevant records returned to AI
              │
              ▼
  Answer grounded in project evidence
```

The MCP server is a tool interface, not a source of truth by itself.
Access control, validation, error handling, and the relevance of
returned context remain essential.

------------------------------------------------------------------------

## Technology Stack

  -----------------------------------------------------------------------
  Component               Technology              Responsibility
  ----------------------- ----------------------- -----------------------
  IDE extension           TypeScript, Node.js, VS Developer workflow
                          Code Extension API      integration

  Web dashboard           Next.js 14, React 18,   Browser-based project
                          Tailwind CSS, shadcn/ui interface

  MCP server              TypeScript, Model       Structured tools for AI
                          Context Protocol SDK    clients

  API layer               Amazon API Gateway      HTTP entry point

  Event ingestion         AWS Lambda, Node.js     Validate and route
                                                  supported incoming
                                                  events

  AI processing           AWS Lambda, Python      Processing and model
                                                  integration

  Model inference         Amazon Bedrock, Nova    AI-assisted processing
                          Pro / Nova Lite         

  Embeddings              Amazon Titan Text       Semantic representation
                          Embeddings              for retrieval

  Structured storage      Amazon DynamoDB         Events, project
                                                  context, and related
                                                  records

  Raw event archive       Amazon S3               Archive supported raw
                                                  event payloads

  Infrastructure as code  AWS CDK                 Define and manage AWS
                                                  resources

  Shared contracts        TypeScript, Zod         Common validation and
                                                  utility definitions

  Planned queueing        Amazon SQS, DLQ         Durable asynchronous
                                                  processing and failure
                                                  isolation

  Planned repository      GitHub App, webhooks    Repository and
  integration                                     pull-request event
                                                  ingestion

  Planned delivery        GitHub Actions events   Correlate workflow
  intelligence                                    outcomes with
                                                  engineering changes
  -----------------------------------------------------------------------

Technology listed as planned is not represented as already deployed.

------------------------------------------------------------------------

## Current Foundation and Planned Work

### Present in the repository foundation

The repository contains these main areas:

-   A VS Code extension
-   A Next.js dashboard
-   AWS CDK infrastructure and Lambda components
-   A TypeScript MCP server
-   A shared TypeScript package for contracts and utilities
-   Design assets and technical documentation

The existing architecture references API Gateway, Lambda, DynamoDB, S3,
Bedrock, and Titan embeddings. Their precise working state, environment
configuration, and end-to-end integration must be verified in the
repository and target AWS account.

### Planned engineering improvements

The development effort is focused on turning the current foundation into
a more reliable and distinctive engineering-intelligence system:

1.  Secure and standardize configuration, contracts, and tests.
2.  Make event processing durable with SQS, retries, idempotency, and a
    DLQ.
3.  Integrate GitHub events through a properly authenticated GitHub App.
4.  Model relationships among repositories, commits, files, pull
    requests, and workflow runs.
5.  Improve retrieval quality and measure it with repeatable evaluation.
6.  Strengthen authorization, tenant isolation, auditing, and
    operational observability.
7.  Add integration tests, failure-recovery tests, deployment
    automation, and a demonstrable end-to-end workflow.

The distinction matters: a roadmap item is an engineering objective, not
a completed feature.

------------------------------------------------------------------------

## Repository Layout

``` text
CodeAtlas/
├── extension/              # VS Code extension
├── frontend/               # Next.js web dashboard
├── infra/                  # AWS CDK and Lambda components
├── mcp-server/             # MCP server and tool definitions
├── packages/
│   └── flowsync-shared/    # Shared TypeScript contracts and utilities
├── design/                 # Design assets
├── DOCUMENTATION/          # Architecture and technical documentation
├── .gitignore
└── README.md
```

Some internal paths and resource identifiers still use the earlier
`flowsync` name. These names can be migrated incrementally after
checking package imports, infrastructure references, configuration, and
deployment compatibility.

------------------------------------------------------------------------

## Local Development

### Prerequisites

-   Git
-   Node.js and npm compatible with the repository's package manifests
-   Python 3.12 for Python-based Lambda components
-   Visual Studio Code for extension development
-   AWS credentials with appropriately scoped permissions for AWS
    integration work
-   Access to the required Bedrock models in the target AWS region for
    AI integration tests

### Clone the repository

``` bash
git clone https://github.com/JainMehul05/CodeAtlas.git
cd CodeAtlas
```

### Explore the components

Before installing dependencies, inspect the root and component-level
`package.json` files, Python dependency files, and documentation. This
repository contains multiple components, so commands should be run from
the relevant directory rather than assuming a single universal start
command.

Suggested inspection commands:

``` bash
# Show the repository's top-level structure
git status
git ls-files

# Find package manifests
find . -name package.json -not -path "*/node_modules/*"
```

### Configure local settings

Use the environment variables required by each component. If example
configuration files are available, copy them to local, ignored files and
replace placeholders with your own values.

Do not commit real credentials. Never put private keys, API secrets, AWS
credentials, or server-side tokens in `NEXT_PUBLIC_*` variables, because
those variables can be bundled into browser-side code.

### Build and test

Use the scripts declared by each package and the test instructions in
`DOCUMENTATION/`. Validate components independently, then verify
integration paths. For infrastructure changes, synthesize and review the
CDK output before deployment. Run cloud integration tests only in a
deliberately configured environment.

> The exact start, build, test, and deployment commands can vary by
> component. This section intentionally avoids inventing commands that
> may not match the current package scripts.

------------------------------------------------------------------------

## Configuration and Security

Security is a core engineering requirement because the platform can
process repository and project information.

The implementation should follow these rules:

-   Store credentials in environment configuration or a secrets manager,
    not in source files.
-   Rotate any token that has been committed or otherwise exposed.
-   Use least-privilege IAM policies for Lambda functions and
    developers.
-   Validate incoming payloads at system boundaries.
-   Authenticate GitHub webhooks and verify event signatures before
    trusting payloads.
-   Make ingestion idempotent so retries do not silently create
    duplicate effects.
-   Enforce project and organization boundaries in every relevant read
    and write path.
-   Avoid logging secrets or unnecessary sensitive repository content.
-   Retain audit records for security-relevant operations.
-   Use separate development and production configuration.
-   Treat model output as untrusted input and validate it before using
    it in downstream operations.

Some of these are planned hardening goals and should not be interpreted
as confirmation that the complete control is already present.

------------------------------------------------------------------------

## Testing and Quality Strategy

CodeAtlas should be evaluated as an integrated system, not only as a
collection of builds.

### Unit tests

Test payload validation, shared schemas, error normalization,
correlation IDs, logging helpers, retrieval utilities, and individual
tool handlers.

### Integration tests

Verify that ingestion, persistence, AI processing, retrieval, and
response delivery work together with representative events and
questions.

### Reliability tests

When queue-based processing is implemented, test retries, duplicate
delivery, poison messages, timeouts, and DLQ recovery. Confirm that a
failed AI-processing step does not silently lose an event.

### Retrieval evaluation

Create a small, versioned set of realistic engineering questions with
expected relevant records. Track metrics such as retrieval relevance,
answer groundedness, citation correctness, latency, and estimated model
cost.

### Security tests

Test unauthorized access, invalid project identifiers, cross-project
data access, webhook signature verification, secret leakage in logs, and
overly broad IAM permissions.

### End-to-end acceptance criteria

A meaningful demonstration should show a real project event being
captured, persisted, retrieved through a supported interface, and used
to answer a question with traceable evidence. Claims about performance
or reliability should be supported by measured results.

------------------------------------------------------------------------

## Roadmap

  -----------------------------------------------------------------------
  Phase                   Focus                   Intended outcome
  ----------------------- ----------------------- -----------------------
  1                       Foundation and cleanup  Secure configuration,
                                                  consistent contracts,
                                                  working tests, and
                                                  clear project
                                                  documentation

  2                       Reliable event          SQS, DLQ, retry policy,
                          ingestion               idempotency, and GitHub
                                                  webhook ingestion

  3                       Engineering knowledge   Relationships among
                          graph                   repositories, commits,
                                                  files, pull requests,
                                                  and context records

  4                       CI/CD intelligence      Connect workflow
                                                  failures and deployment
                                                  events to code changes

  5                       Advanced retrieval      Hybrid search,
                                                  filtering, reranking,
                                                  source grounding, and
                                                  evaluation

  6                       MCP workflow expansion  Useful, validated
                                                  context tools for AI
                                                  coding assistants

  7                       Multi-tenant security   Authentication,
                                                  authorization,
                                                  organization isolation,
                                                  and audit controls

  8                       Observability           Structured logs,
                                                  correlation IDs,
                                                  tracing, metrics,
                                                  alarms, and dashboards

  9                       Testing and performance Integration coverage,
                                                  failure recovery,
                                                  retrieval evaluation,
                                                  and cost/latency
                                                  baselines

  10                      Production readiness    Repeatable deployment,
                                                  environment separation,
                                                  documentation, and a
                                                  reproducible demo
  -----------------------------------------------------------------------

**Immediate priority:** finish the foundation and security checks before
introducing the queue-based ingestion architecture.

------------------------------------------------------------------------

## Engineering Principles

-   **Evidence over guesswork:** answers should be grounded in available
    project records.
-   **Reliability before complexity:** introduce infrastructure when it
    addresses a demonstrated need.
-   **Clear boundaries:** separate ingestion, processing, persistence,
    retrieval, and client interfaces.
-   **Secure by default:** validate inputs, restrict permissions,
    protect secrets, and enforce project boundaries.
-   **Measurable quality:** use tests and evaluation data to guide
    retrieval and architecture changes.
-   **Incremental delivery:** make small, reviewable changes and verify
    them before moving to the next phase.
-   **Honest status reporting:** distinguish implemented behavior from
    planned design and unverified assumptions.

## Project Ownership and Contributions

I maintain the CodeAtlas repository and am developing its architecture,
implementation, integrations, and roadmap as an ongoing engineering
project. The focus of this work is to turn the current codebase into a
reliable engineering-intelligence platform through substantial
implementation, testing, and design improvements.

Repository:
[JainMehul05/CodeAtlas](https://github.com/JainMehul05/CodeAtlas)

------------------------------------------------------------------------

*CodeAtlas is an evolving engineering project. Architecture,
integrations, and feature status may change as implementation and
validation progress.*
