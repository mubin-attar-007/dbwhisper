---
title: DBWhisper
emoji: 🗄️
colorFrom: indigo
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
---

# DBWhisper — Natural-Language-to-SQL Agent

> FastAPI service that converts plain English into **validated, read-only SQL** across
> MSSQL / MySQL / PostgreSQL, with multi-provider LLM fallback (Gemini, Groq, OpenAI,
> DeepSeek, Anthropic, OpenRouter) and schema-aware **PGVector** retrieval.
>
> **Run it:** copy `.env.example` → `.env`, fill in a provider key + `POSTGRES_CONNECTION_STRING`,
> then `uv sync && uv run uvicorn app.main:app --port 8000` (or `docker compose up`).
> **Quality gates:** `uv run ruff check app db tests` · `uv run pytest`.
> **Embeddings:** default hosted Google provider (lean image); set `EMBEDDING_PROVIDER=huggingface`
> with `uv sync --extra local-embeddings` for fully local/offline embeddings (re-embed when switching).
> **Deploy:** Hugging Face Spaces (Docker, port 7860) + Neon (Postgres/pgvector); `render.yaml`
> provided as an alternative. See [SECURITY.md](SECURITY.md) for the read-only safety model.

<!-- ───────────────────────── Original documentation below ───────────────────────── -->

# SQL Insight Agent (NL2SQL)

This repository contains SQL Insight Agent — a production-focused FastAPI app that converts natural language queries into SQL using LLM-based agents, validates SQL safety, executes queries against target databases (MSSQL, MySQL, PostgreSQL), and presents results via REST endpoints. The project features schema extraction, documentation, and PGVector-based embeddings for semantic search. Prioritize review of the `app/` folder for runtime behavior and `tests/` for validation.

---

## Table of Contents
- [Overview](#overview)
- [Getting Started](#getting-started)
- [Install & Run](#install--run)
- [Configuring Databases & PGVector](#configuring-databases--pgvector)
- [Environment Variables](#environment-variables)
- [API Reference (Quick)](#api-reference-quick)
- [Testing and Quality](#testing-and-quality)
- [Developer Notes - Stability & Security](#developer-notes---stability--security)
- [Troubleshooting & FAQ](#troubleshooting--faq)
- [Project Structure](#project-structure)
- [Contributing & CI](#contributing--ci)

---

## Overview

SQL Insight Agent is built to help non-technical users query relational databases using plain English. It uses a combination of frameworks and patterns:

- FastAPI for the web server and REST API
- LangChain and LangGraph patterns for agent orchestration
- PostgreSQL for persistence, conversation memory, and PGVector for embeddings
- SQLAlchemy for database access
- Several LLM providers with fallback (OpenAI, OpenRouter, DeepSeek, Groq, Anthropic, Gemini)

The core goals for this repository are security, reliability, and maintainability — not adding new features in this phase. The current work focuses on ensuring the app runs smoothly and safely in production environments.

---

## Getting Started

Prerequisites (minimum):
- Python 3.11+ (3.13+ recommended where supported)
- PostgreSQL 14+ with PGVector extension
- A running SQL database for schema extraction and query execution (SQL Server, MySQL, or PostgreSQL)
- An LLM API key for at least one provider (e.g., OpenAI)

Recommended: use a dedicated virtual environment per project.

Steps to run locally (Windows PowerShell shown):

1. Clone the repository
```bash
git clone <repo-url> && cd SQL_SERVER_AGENT
```

2. Install dependencies and create virtual env (use `uv` if provided or pipenv/venv)
```powershell
# Using uv (recommended if installed):
uv sync

# If not using uv, create a venv and install
python -m venv .venv
.\.venv\Scripts\Activate.ps1  # Windows PowerShell
python -m pip install -r requirements.txt
```

3. Copy the example env file and update values
```bash
cp .env.example .env
# Edit .env to include your database creds and LLM keys
```

4. Start the API server
```bash
uv run run.py
# or directly
python -m uvicorn app.main:app --reload --port 8000
```

5. Open the interactive docs
```bash
http://127.0.0.1:8000/docs
```

---

## Install & Run Details

1. Activate virtual environment

Windows (PowerShell)
```powershell
.\.venv\Scripts\Activate.ps1
```

Linux/macOS
```bash
source .venv/bin/activate
```

2. Export environment variables (via `.env`) before running. Critical variables:
```
POSTGRES_CONNECTION_STRING=postgresql://sql_agent_user:password@localhost:5432/SQL_AGENT
HOST=127.0.0.1
PORT=8000
```

3. Start the server using `uv` or uvicorn (see previous step).

4. Query the service
Use the /docs UI to try `POST /query` and `POST /schemas/enroll`.

---

## Configuring Databases & PGVector

The app uses PostgreSQL for the LangChain store (embeddings and session memory). The `POSTGRES_CONNECTION_STRING` must reference a database accessible to the app. Ensure you have installed PGVector and enabled the extension:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

Use the `psql` CLI or your DB GUI to create `SQL_AGENT` and `sql_agent_user` for a dedicated login.

For schema extraction, the agent also needs read-only connections to your target databases (ex: SQL Server). The program validates queries and should avoid DML/DDL.

---

## Environment Variables (Minimum)

Add at least the following to `.env` or your environment:

- `POSTGRES_CONNECTION_STRING` — Connection for embedding & memory DB
- `PROJECT_DB_CONNECTION_STRING` — (optional) A separate project DB for metadata
- LLM keys for providers (e.g., `OPENAI_API_KEY` or `GROQ_API_KEY`)

Optional:
- `HOST` and `PORT` — Server address
- `LOG_LEVEL` — INFO/DEBUG is helpful during development
- `HF_DTYPE` or `HF_TORCH_DTYPE` to set dtype for HF embeddings if required

---

## API Reference (Quick)

- `GET /health` — Health check
- `GET /chat` — Simple dev UI (static HTML)
- `POST /query` — NL2SQL query with body `QueryRequest` → returns `QueryResponse`
- `POST /schemas/enroll` — Enroll a DB and extract/schema doc; request is `SchemaPipelineRequest`
- `POST /schemas/embeddings` — Build schema embeddings (PGVector route)

Use the interactive docs at `/docs` for full schema and example payloads.

---

## Testing and Quality

Run unit tests with pytest (recommended):

```bash
# Activate virtual environment then run:
python -m pytest -q
```

Key tests are under `tests/` covering:
- Query executor and pagination logic
- Sanitization and log masking
- PGVector index logic
- LLM summarization trimming and token stripping
- Schema pipelines (extraction/documentation/embeddings)

---

## Developer Notes - Stability & Security

Stability and careful operation were prioritized. Key points:

- `sanitize_for_log` ensures we don't leak secrets in logs — all user inputs are sanitized before logging.
- Query execution validates SQL (no DML/DDL) and enforces read-only behavior heuristically.
- PGVector index creation checks the UDT type of the `embedding` column before attempting ivfflat index creation.
- LLM-generated summaries are truncated to at most 3 sentences; any internal role tags like `<think>` are removed.
- Pagination only applies when both `page` and `page_size` are supplied by the client. Aggregations skip pagination.

---

## Troubleshooting & FAQ

Q: `uv run run.py` fails with SyntaxError
A: Ensure your Python version is supported and that a previous edit didn't place runtime code in an import block. A `SyntaxError` can result from a stray assignment or comment inside import parentheses; check `app/main.py`.

Q: PGVector ivfflat index creation failing with `column does not have dimensions`?
A: This indicates your `langchain_pg_embedding` table's `embedding` column isn't the `vector` type. The app logs a warning and continues; you can create a proper `vector` column to enable `ivfflat`.

Q: I get `Unable to determine MSSQL role membership` when enrolling a database
A: This is a best-effort check for read-only accounts. If your connection user lacks permissions for these checks, the function logs the uncertainty. The pipeline continues but warns about possibly writable accounts.

---

## Project Structure (High-level)

- `app/` — FastAPI server and business logic
  - `main.py` — FastAPI entrypoint and orchestrates work
  - `agent/` — Agent builder and tooling for LLM interaction
  - `core/` — Query execution, validation, retrieval
  - `schema_pipeline/` — Schema extraction, documentation and embeddings
  - `static/` — Chat UI (development convenience)
  - `utils/` — Logger and helper utilities
- `db/` — DB state store and memory helpers
- `tests/` — Unit and integration tests

---

## Contributing & CI

Contributors should follow a minimal workflow:

1. Create a feature/bugfix branch
2. Add tests for new or changed behavior
3. Run `pytest -q` and `python -m py_compile` locally
4. Open a PR; CI checks run tests and linters (if present)

CI (recommended): Add a GitHub Action to run tests + py_compile + black/ruff on each PR to enforce style and prevent syntax errors.

---

If you'd like, I can:
- Add CI configuration to run tests and lint automatically
- Centralize LLM sanitization into `app/utils/llm_utils.py` for consistency
- Add a `CONTRIBUTING.md` for developer onboarding

Thanks for keeping this project focused on stability — let me know which of the optional suggestions you'd like me to implement next.
# SQL Insight Agent

A production-ready, LangGraph-based agentic SQL query system that transforms natural language into validated SQL queries. Built for SQL Server and MySQL databases with advanced features including schema intelligence, conversation memory, multi-LLM support, and vector-based semantic search.

---

## Table of Contents

1. [Overview](#overview)
2. [Key Features](#key-features)
3. [Architecture](#architecture)
4. [Installation & Setup](#installation--setup)
5. [Database Setup (PostgreSQL + PGVector)](#database-setup-postgresql--pgvector)
6. [Environment Configuration](#environment-configuration)
7. [API Endpoints - Complete Reference](#api-endpoints---complete-reference)
8. [How It Works](#how-it-works)
9. [Schema Pipeline Explained](#schema-pipeline-explained)
10. [Conversation Memory](#conversation-memory)
11. [Project Structure](#project-structure)
12. [Security & Validation](#security--validation)
13. [Troubleshooting](#troubleshooting)
14. [Advanced Usage](#advanced-usage)

---

## Overview

SQL Insight Agent is an intelligent Natural Language to SQL (NL2SQL) system that enables non-technical users to query databases using plain English. The system leverages multiple LLM providers with automatic fallback, maintains conversation context, and uses vector embeddings for intelligent schema discovery.

### What Makes It Special?

- **Conversational Context**: Remembers previous queries and maintains session state
- **Intelligent Schema Discovery**: Uses vector embeddings to find relevant tables
- **Multi-LLM Support**: Automatic fallback across 6 LLM providers
- **Secure by Design**: Built-in SQL validation prevents unsafe operations
- **Self-Documenting**: Automatically extracts and documents database schemas
- **Production Ready**: Structured logging, error handling, and API standardization

---

## Key Features

### Core Capabilities
- ✅ Natural language to SQL conversion with LLM-based agents
- ✅ Multi-database support (SQL Server, MySQL, PostgreSQL)
- ✅ Secure read-only SQL validation (prevents DML/DDL operations)
- ✅ FastAPI REST API with auto-generated OpenAPI documentation
- ✅ Conversation memory with context-aware responses
- ✅ Follow-up question generation
- ✅ Natural language result summaries

### LLM Providers Supported
1. **OpenAI** (GPT-4, GPT-3.5)
2. **OpenRouter** (Multi-model gateway)
3. **DeepSeek** (Cost-effective alternative)
4. **Groq** (Fast inference)
5. **Anthropic** (Claude models)
6. **Google Gemini** (Gemini Pro)

### Schema Intelligence
- Automatic schema extraction from live databases
- AI-powered documentation generation
- Vector embeddings for semantic table/column search
- Relationship mapping and join path discovery

### Output Formats
- **JSON**: Structured data with metadata
- **CSV**: Ready for download or Excel import
- **Table**: Formatted text representation
- **Natural Language Summary**: LLM-generated insights

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         User Interface                          │
│                  (Chat UI / API Client)                         │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                      FastAPI Application                        │
│                         (app/main.py)                           │
└──────────────────────────┬──────────────────────────────────────┘
                           │
         ┌─────────────────┼─────────────────┐
         │                 │                 │
         ▼                 ▼                 ▼
┌────────────────┐ ┌──────────────┐ ┌──────────────────┐
│  Query Agent   │ │   Schema     │ │  Conversation    │
│   (LangGraph)  │ │   Pipeline   │ │     Memory       │
└────────┬───────┘ └──────┬───────┘ └────────┬─────────┘
         │                │                   │
         ▼                ▼                   ▼
┌─────────────────────────────────────────────────────────────────┐
│                    PostgreSQL Database                          │
│  ┌──────────────┐ ┌──────────────┐ ┌────────────────────────┐  │
│  │  PGVector    │ │  Checkpoint  │ │  Conversation History  │  │
│  │  Embeddings  │ │    Store     │ │      & Summaries       │  │
│  └──────────────┘ └──────────────┘ └────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Target Databases                              │
│         (SQL Server / MySQL / PostgreSQL)                       │
└─────────────────────────────────────────────────────────────────┘
```

---

## Installation & Setup

### Prerequisites
- **Python 3.13+** (required)
- **PostgreSQL 14+** with PGVector extension
- **uv** package manager (recommended)
- Database access credentials (SQL Server, MySQL, or PostgreSQL)
- At least one LLM API key

### Step 1: Install UV Package Manager

**Windows (PowerShell):**
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**Linux/macOS:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Step 2: Clone and Setup Project

```bash
# Clone the repository
git clone <your-repo-url>
cd sql-insight-agent

# Install dependencies using uv
uv sync

# This creates a virtual environment and installs all dependencies
```

### Step 3: Verify Installation

```bash
# Activate the virtual environment
# Windows PowerShell:
.venv\Scripts\Activate.ps1

# Linux/macOS:
source .venv/bin/activate

# Verify Python version
python --version  # Should be 3.13+

# List installed packages
uv pip list
```

---

## Database Setup (PostgreSQL + PGVector)

The SQL Insight Agent requires a PostgreSQL database with the PGVector extension for storing schema embeddings, conversation history, and agent checkpoints.

### PostgreSQL Installation

**Windows:**
1. Download PostgreSQL from [postgresql.org](https://www.postgresql.org/download/windows/)
2. Run the installer and follow the setup wizard
3. Note your postgres password and port (default: 5432)

**Linux (Ubuntu/Debian):**
```bash
sudo apt update
sudo apt install postgresql postgresql-contrib
sudo systemctl start postgresql
sudo systemctl enable postgresql
```

**macOS (Homebrew):**
```bash
brew install postgresql@14
brew services start postgresql@14
```

### PGVector Extension Installation

PGVector enables vector similarity search for schema embeddings.

**Method 1: Using pgvector from packages**

```bash
# Ubuntu/Debian
sudo apt install postgresql-14-pgvector

# macOS
brew install pgvector
```

**Method 2: Build from source**

```bash
# Clone pgvector repository
git clone https://github.com/pgvector/pgvector.git
cd pgvector

# Build and install
make
sudo make install

# Restart PostgreSQL
sudo systemctl restart postgresql
```

### Database Creation and Setup

```bash
# Connect to PostgreSQL as superuser
sudo -u postgres psql

# Or on Windows:
psql -U postgres
```

**Execute the following SQL commands:**

```sql
-- Create the database for SQL Insight Agent
CREATE DATABASE SQL_AGENT;

-- Connect to the new database
\c SQL_AGENT

-- Enable the PGVector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Verify the extension is installed
SELECT * FROM pg_extension WHERE extname = 'vector';

-- Create a user for the application (recommended for production)
CREATE USER sql_agent_user WITH PASSWORD 'your_secure_password';

-- Grant privileges
GRANT ALL PRIVILEGES ON DATABASE SQL_AGENT TO sql_agent_user;
GRANT ALL ON SCHEMA public TO sql_agent_user;

-- Allow the user to create tables
ALTER USER sql_agent_user CREATEDB;

-- Exit psql
\q
```

### Verify PGVector Installation

```sql
-- Connect to your database
\c SQL_AGENT

-- Test vector operations
SELECT '[1,2,3]'::vector;

-- Should return: [1,2,3]
```

### Connection String Format

After setup, your PostgreSQL connection string will be:

```
postgresql://sql_agent_user:your_secure_password@localhost:5432/SQL_AGENT
```

**Important Notes:**
- Replace `sql_agent_user` with your username
- Replace `your_secure_password` with your chosen password
- Replace `localhost` with your PostgreSQL host if remote
- Replace `5432` with your PostgreSQL port if different

### Database Tables (Auto-Created)

The application automatically creates the following tables on first run:

1. **database_config** - Stores registered database configurations
2. **langchain_pg_collection** - PGVector collection metadata
3. **langchain_pg_embedding** - Schema embeddings for semantic search
4. **checkpoints** - LangGraph agent state checkpoints
5. **checkpoint_writes** - Agent checkpoint write log
6. **store** - Conversation history and session summaries

---

## Environment Configuration

Create a `.env` file in the project root directory:

```bash
# .env file

# ============================================================================
# PostgreSQL Database (REQUIRED)
# ============================================================================
# Main database for embeddings, checkpoints, and conversation memory
POSTGRES_CONNECTION_STRING=postgresql://sql_agent_user:your_password@localhost:5432/SQL_AGENT

# Alternative: Use this if you want a separate project metadata database
# PROJECT_DB_CONNECTION_STRING=postgresql://user:pass@localhost:5432/metadata_db

# ============================================================================
# LLM Provider API Keys (Configure at least ONE)
# ============================================================================

# OpenAI (GPT-4, GPT-3.5-turbo, etc.)
OPENAI_API_KEY=sk-proj-xxxxxxxxxxxxxxxxxxxxx

# OpenRouter (Multi-model gateway)
OPENROUTER_API_KEY=sk-or-xxxxxxxxxxxxxxxxxxxxx

# DeepSeek (Cost-effective alternative)
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxx

# Groq (Fast inference with Llama, Mixtral)
GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxxx

# Anthropic (Claude models)
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxxx

# Google Gemini (Gemini Pro, Ultra)
GOOGLE_API_KEY=AIzaSyxxxxxxxxxxxxxxxxxxxxx

# ============================================================================
# Application Settings (Optional)
# ============================================================================

# Server host and port
HOST=127.0.0.1
PORT=8000

# Logging level (DEBUG, INFO, WARNING, ERROR)
LOG_LEVEL=INFO

# LangSmith Tracing (Optional - for debugging LLM chains)
# LANGCHAIN_TRACING_V2=true
# LANGCHAIN_API_KEY=ls__xxxxxxxxxxxxxxxxxxxxx
# LANGCHAIN_PROJECT=sql-insight-agent
```

### LLM Provider Priority

The system automatically selects providers based on this priority order:
1. **OpenAI** - Most reliable, broad model support
2. **OpenRouter** - Multi-model access
3. **DeepSeek** - Cost-effective
4. **Groq** - Fastest inference
5. **Anthropic** - Claude models
6. **Gemini** - Google's models

If the primary provider fails, the system automatically falls back to the next available provider.

---

## API Endpoints - Complete Reference

### Base URL
```
http://127.0.0.1:8000
```

### Interactive Documentation
- **Swagger UI**: http://127.0.0.1:8000/docs
- **ReDoc**: http://127.0.0.1:8000/redoc

---

## 1. Health Check

**Endpoint:** `GET /health`

**Description:** Verify that the API service is running and healthy.

**Request:** No parameters required

**Response:**
```json
{
  "status": "healthy",
  "message": "SQL Insight Agent is running",
  "version": "1.0.0"
}
```

**Use Case:** Health monitoring, load balancer checks, smoke tests

---

## 2. Natural Language Query Execution

**Endpoint:** `POST /query`

**Description:** Convert natural language to SQL, execute against the target database, and return formatted results with optional conversation context.

### Request Parameters

```json
{
  "query": "string (required)",
  "db_flag": "string (required)",
  "output_format": "json | csv | table (optional, default: json)",
  "user_id": "string (optional)",
  "session_id": "string (optional)"
}
```

**Parameter Details:**

- **query** *(required)*
  - Type: `string`
  - Min length: 1 character
  - Description: Natural language question or instruction
  - Examples:
    - "Show me all customers from California"
    - "What were the top 10 products by revenue last month?"
    - "List employees hired after January 2023"

- **db_flag** *(required)*
  - Type: `string`
  - Min length: 1 character
  - Description: Identifier for the target database (must be enrolled first)
  - Examples:
    - "crm_db"
    - "sales_prod"
    - "inventory_warehouse"

- **output_format** *(optional)*
  - Type: `string`
  - Allowed values: `"json"`, `"csv"`, `"table"`
  - Default: `"json"`
  - Description: Format for query results
    - **json**: Structured JSON array of objects
    - **csv**: Comma-separated values (ready for download)
    - **table**: Human-readable text table

- **user_id** *(optional)*
  - Type: `string`
  - Description: Unique identifier for the user (enables conversation memory)
  - Example: "john.doe@company.com"
  - Note: Both `user_id` and `session_id` required for conversation tracking

- **session_id** *(optional)*
  - Type: `string`
  - Description: Unique identifier for the conversation session
  - Example: "2024-11-25-session-001"
  - Note: Both `user_id` and `session_id` required for conversation tracking

### Example Request

```json
POST /query
Content-Type: application/json

{
  "query": "Show me the top 5 customers by total order value in the last 30 days",
  "db_flag": "sales_prod",
  "output_format": "json",
  "user_id": "analyst@company.com",
  "session_id": "session-2024-11-25-001"
}
```

### Response Schema

```json
{
  "status": "success | error",
  "sql": "string | null",
  "validation_passed": "boolean | null",
  "data": {
    "results": "any",
    "sql": "string",
    "row_count": "integer",
    "execution_time_ms": "float | null",
    "csv": "string",
    "raw_json": "string",
    "describe": "object",
    "describe_text": "string"
  } | null,
  "error": "string | null",
  "selected_tables": ["string"] | null,
  "keyword_matches": ["string"] | null,
  "follow_up_questions": ["string"] | null,
  "metadata": {
    "execution_time_ms": "float | null",
    "total_rows": "integer | null",
    "retry_count": "integer"
  },
  "token_usage": "object | null",
  "natural_summary": "string | null"
}
```

### Response Field Details

**Top-Level Fields:**

- **status**: `"success"` or `"error"`
- **sql**: The generated SQL query string (may be null if generation failed)
- **validation_passed**: Whether the SQL passed security validation
- **data**: Object containing query results (null if error)
- **error**: Error message if status is "error"
- **selected_tables**: List of database tables used by the query
- **keyword_matches**: Search keywords used for table discovery (may be null)
- **follow_up_questions**: AI-generated suggested follow-up questions
- **metadata**: Execution statistics
- **token_usage**: LLM token consumption (if available)
- **natural_summary**: Natural language explanation of the results

**Data Object Fields:**

- **results**: Primary result payload in the requested format
  - For JSON: Array of row objects
  - For CSV: String with CSV data
  - For table: Formatted text table
- **sql**: The SQL query that generated these results
- **row_count**: Number of rows returned
- **execution_time_ms**: Time taken to execute the query (milliseconds)
- **csv**: Complete results in CSV format
- **raw_json**: Complete results in JSON format
- **describe**: Pandas `.describe()` statistics per column
- **describe_text**: Human-readable description of statistics

### Success Response Example

```json
{
  "status": "success",
  "sql": "SELECT TOP 5 CustomerID, CustomerName, SUM(OrderTotal) as TotalValue FROM Orders WHERE OrderDate >= DATEADD(day, -30, GETDATE()) GROUP BY CustomerID, CustomerName ORDER BY TotalValue DESC",
  "validation_passed": true,
  "data": {
    "results": [
      {
        "CustomerID": 1001,
        "CustomerName": "Acme Corp",
        "TotalValue": 125000.50
      },
      {
        "CustomerID": 1005,
        "CustomerName": "Tech Solutions Inc",
        "TotalValue": 98450.25
      }
    ],
    "sql": "SELECT TOP 5...",
    "row_count": 5,
    "execution_time_ms": 245.67,
    "csv": "CustomerID,CustomerName,TotalValue\n1001,Acme Corp,125000.50\n...",
    "raw_json": "[{\"CustomerID\":1001,...}]",
    "describe": {
      "TotalValue": {
        "count": 5,
        "mean": 87500.30,
        "std": 18234.56,
        "min": 45000.00,
        "max": 125000.50
      }
    },
    "describe_text": "TotalValue: mean=87500.30, min=45000.00, max=125000.50"
  },
  "error": null,
  "selected_tables": ["Orders", "Customers"],
  "keyword_matches": null,
  "follow_up_questions": [
    "Would you like to see the breakdown by product category?",
    "Should I compare this to the previous 30-day period?",
    "Do you want to see the individual orders for these customers?"
  ],
  "metadata": {
    "execution_time_ms": 245.67,
    "total_rows": 5,
    "retry_count": 0
  },
  "token_usage": null,
  "natural_summary": "The top 5 customers by order value in the last 30 days are led by Acme Corp with $125,000.50 in total orders. The average order value among these top customers is $87,500.30, ranging from $45,000 to $125,000.50."
}
```

### Error Response Example

```json
{
  "status": "error",
  "sql": "SELECT * FROM NonExistentTable",
  "validation_passed": true,
  "data": null,
  "error": "Table 'NonExistentTable' does not exist in the database schema",
  "selected_tables": null,
  "keyword_matches": null,
  "follow_up_questions": null,
  "metadata": {
    "execution_time_ms": 123.45,
    "total_rows": null,
    "retry_count": 0
  },
  "token_usage": null,
  "natural_summary": null
}
```

---

## 3. Database Schema Enrollment

**Endpoint:** `POST /schemas/enroll`

**Description:** Enroll a new database into the system by extracting its schema, generating AI-powered documentation, and creating vector embeddings for semantic search. This is a one-time setup process for each database.

### Request Parameters

```json
{
  "db_flag": "string (required)",
  "db_type": "string (required)",
  "connection_string": "string (required)",
  "description": "string (optional)",
  "intro_template": "string (optional)",
  "exclude_column_matches": "boolean (optional, default: false)",
  "include_schemas": ["string"] (optional),
  "exclude_schemas": ["string"] (optional),
  "run_documentation": "boolean (optional, default: true)",
  "incremental_documentation": "boolean (optional, default: true)",
  "run_embeddings": "boolean (optional, default: true)"
}
```

**Parameter Details:**

- **db_flag** *(required)*
  - Type: `string`
  - Description: Unique identifier for this database
  - Example: "sales_prod", "inventory_2024"
  - Note: Used in API calls to reference this database

- **db_type** *(required)*
  - Type: `string`
  - Allowed values: `"mssql"`, `"mysql"`, `"postgresql"`
  - Description: Database system type

- **connection_string** *(required)*
  - Type: `string`
  - Description: Database connection string
  - **⚠️ CRITICAL SECURITY REQUIREMENT**: You MUST provide a READ-ONLY database user in the connection string
  - **Why**: The SQL Insight Agent only needs SELECT permissions. Using read-only credentials prevents any accidental or malicious data modifications
  - **How to create read-only users**: See the "Security Configuration" section below for SQL Server, MySQL, and PostgreSQL examples
  Pagination behavior
  --------------
  By default, the server will not apply pagination to SQL queries unless both `page` and `page_size` parameters are provided explicitly by the client. This avoids accidental pagination when the client supplies only one parameter.

  SQL Server (MSSQL) pagination
  -----------------------------
  SQL Server requires an `ORDER BY` for correct `OFFSET`/`FETCH` pagination. If a client requests pagination for a SQL Server connection but the query lacks `ORDER BY`, the API will return an error prompting the user to add an explicit `ORDER BY` clause.

  If you'd like the server to automatically add a non-invasive fallback ORDER BY for older queries (not recommended unless you understand the implications on ordering), set the environment variable `ALLOW_MSSQL_AUTO_ORDER_BY=true` to enable `ORDER BY (SELECT NULL)` fallback for paginated queries.

  - Formats:
    - **SQL Server**: `"DRIVER={ODBC Driver 17 for SQL Server};SERVER=host;DATABASE=dbname;UID=readonly_user;PWD=pass"`
    - **MySQL**: `"mysql+pymysql://readonly_user:pass@host:3306/dbname"`
    - **PostgreSQL**: `"postgresql://readonly_user:pass@host:5432/dbname"`
  - **Best Practice**: Create a dedicated `sql_insight_readonly` user with only SELECT privileges

- **description** *(optional)*
  - Type: `string`
  - Description: Human-readable description of the database
  - Example: "Production sales database containing customer orders and inventory"

- **intro_template** *(optional)*
  - Type: `string`
  - Description: Path to a text file containing business context for documentation
  - Example: "database_schemas/sales_prod/db_intro/sales_context.txt"
  - Note: This text is used by the LLM to add business context to table descriptions

- **exclude_column_matches** *(optional)*
  - Type: `boolean`
  - Default: `false`
  - Description: If true, skip column name/keyword matching during table search
  - Use case: Performance optimization for large schemas

- **include_schemas** *(optional)*
  - Type: `array of strings`
  - Description: Whitelist of database schemas to extract
  - Example: `["dbo", "sales", "inventory"]`
  - Note: If omitted, extracts all schemas

- **exclude_schemas** *(optional)*
  - Type: `array of strings`
  - Description: Blacklist of schemas to skip
  - Example: `["sys", "information_schema", "temp"]`

- **run_documentation** *(optional)*
  - Type: `boolean`
  - Default: `true`
  - Description: Whether to generate AI-powered table/column documentation

- **incremental_documentation** *(optional)*
  - Type: `boolean`
  - Default: `true`
  - Description: Skip tables that already have documentation
  - Use case: Re-running enrollment on updated schemas

- **run_embeddings** *(optional)*
  - Type: `boolean`
  - Default: `true`
  - Description: Whether to generate vector embeddings for semantic search

### Example Request

**⚠️ SECURITY NOTE**: The example below uses a read-only database user (`sql_readonly`). Always use read-only credentials when enrolling databases.

```json
POST /schemas/enroll
Content-Type: application/json

{
  "db_flag": "sales_prod",
  "db_type": "mssql",
  "connection_string": "DRIVER={ODBC Driver 17 for SQL Server};SERVER=sql-prod-01.company.com;DATABASE=SalesDB;UID=sql_readonly;PWD=SecurePass123",
  "description": "Production sales database with customer orders, inventory, and shipping data",
  "intro_template": "database_schemas/sales_prod/db_intro/sales_business_context.txt",
  "exclude_column_matches": false,
  "exclude_schemas": ["sys", "tempdb", "model", "msdb"],
  "run_documentation": true,
  "incremental_documentation": true,
  "run_embeddings": true
}
```

### Response Schema

```json
{
  "db_flag": "string",
  "extraction": {
    "status": "success | failed",
    "output_directory": "string",
    "tables_exported": "integer",
    "message": "string | null"
  },
  "documentation": {
    "status": "success | failed | skipped",
    "tables_total": "integer",
    "documented": "integer",
    "failed": "integer",
    "message": "string | null"
  },
  "embeddings": {
    "status": "success | failed | skipped",
    "minimal_files": "integer",
    "document_chunks": "integer",
    "output_directory": "string",
    "message": "string | null"
  },
  "report": {
    "extracted_files": "integer",
    "documentation_tables_total": "integer",
    "documentation_documented": "integer",
    "documentation_failed": "integer",
    "documentation_skipped": "integer",
    "embeddings_minimal_files": "integer",
    "embeddings_document_chunks": "integer"
  }
}
```

### Success Response Example

```json
{
  "db_flag": "sales_prod",
  "extraction": {
    "status": "success",
    "output_directory": "d:\\sql-insight-agent\\database_schemas\\sales_prod\\schema",
    "tables_exported": 45,
    "message": "Schema extraction completed"
  },
  "documentation": {
    "status": "success",
    "tables_total": 45,
    "documented": 45,
    "failed": 0,
    "message": "Documentation completed"
  },
  "embeddings": {
    "status": "success",
    "minimal_files": 45,
    "document_chunks": 312,
    "output_directory": "d:\\sql-insight-agent\\config\\minimal_schemas\\sales_prod",
    "message": "Embedding stage completed"
  },
  "report": {
    "extracted_files": 45,
    "documentation_tables_total": 45,
    "documentation_documented": 45,
    "documentation_failed": 0,
    "documentation_skipped": 0,
    "embeddings_minimal_files": 45,
    "embeddings_document_chunks": 312
  }
}
```

### What Happens During Enrollment?

1. **Schema Extraction** (1-5 minutes)
   - Connects to target database
   - Extracts metadata: tables, columns, data types, keys, indexes, constraints
  - Saves YAML files to `database_schemas/<db_flag>/schema/`

2. **Documentation Generation** (5-30 minutes depending on table count)
   - Uses LLM to generate business-friendly descriptions
   - Creates searchable keywords for each column
   - Enriches YAML files with documentation

3. **Vector Embedding** (2-10 minutes)
   - Chunks schema documentation into semantic units
   - Generates embeddings using Jina AI model
   - Stores in PostgreSQL with PGVector for similarity search

---

## 4. Refresh Schema Embeddings

**Endpoint:** `POST /schemas/embeddings`

**Description:** Regenerate vector embeddings for an already-enrolled database. Use this after manually updating schema YAML files or changing the business context.

### Request Parameters

```json
{
  "db_flag": "string (required)",
  "collection_name": "string (optional, default: '<db_flag>_docs')"
}
```

**Parameter Details:**

- **db_flag** *(required)*
  - Type: `string`
  - Description: Database identifier (must already be enrolled)

- **collection_name** *(optional)*
  - Type: `string`
  - Default: `"{db_flag}_docs"`
  - Description: PGVector collection name for embeddings

### Example Request

```json
POST /schemas/embeddings
Content-Type: application/json

{
  "db_flag": "sales_prod",
  "collection_name": "sales_prod_docs"
}
```

### Response Schema

```json
{
  "db_flag": "string",
  "output_directory": "string",
  "processed_files": ["string"],
  "message": "string"
}
```

### Success Response Example

```json
{
  "db_flag": "sales_prod",
  "output_directory": "d:\\sql-insight-agent\\config\\minimal_schemas\\sales_prod",
  "processed_files": [
    "Orders_minimal.txt",
    "Customers_minimal.txt",
    "Products_minimal.txt",
    "Inventory_minimal.txt"
  ],
  "message": "Embeddings stored successfully"
}
```

---

## 5. Root Endpoint

**Endpoint:** `GET /`

**Description:** API information and endpoint directory

**Response:**
```json
{
  "message": "SQL Insight Agent API",
  "docs": "/docs",
  "health": "/health",
  "endpoints": {
    "POST /query": "Execute natural language SQL query",
    "POST /schemas/embeddings": "Convert schema YAML definitions to embeddings",
    "POST /schemas/enroll": "Enroll a database, extract schema, document, and embed",
    "GET /health": "Health check"
  }
}
```

---

## 6. Developer Chat UI

**Endpoint:** `GET /chat`

**Description:** Browser-based test interface for manual query testing

**Access:** http://127.0.0.1:8000/chat

**Features:**
- Natural language query input
- Database selection dropdown
- Output format selection
- User ID and Session ID for conversation testing
- Real-time SQL display
- Formatted result tables
- CSV/JSON download

**⚠️ WARNING:** This UI is for development/testing only. Do not expose in production without authentication.

---

## How It Works

### Query Flow (Step-by-Step)

```
1. User submits natural language query
         ↓
2. System selects LLM provider (with fallback)
         ↓
3. If conversation context exists:
   - Retrieve query history
   - Build conversation summary
   - Include in prompt
         ↓
4. Semantic search for relevant tables (PGVector)
   - Embed user query
   - Find similar schema chunks
   - Extract table names
         ↓
5. Load full table schemas (YAML files)
   - Columns, types, descriptions, keywords
   - Foreign keys and relationships
   - Business context
         ↓
6. Agent generates SQL using:
   - User query
   - Conversation context
   - Relevant table schemas
   - Business introduction template
         ↓
7. SQL Security Validation
   - Check for DML/DDL operations
   - Verify only SELECT allowed
   - Validate syntax
         ↓
8. Execute SQL against target database
   - Apply row limit (default: 1000)
   - Set query timeout (default: 30s)
         ↓
9. Format results (JSON/CSV/Table)
   - Generate statistics (describe)
   - Create natural language summary
         ↓
10. Store conversation context
   - Save query, SQL, tables used
   - Update session summary
   - Generate follow-up questions
         ↓
11. Return response to user
```

### Agent Architecture (LangGraph)

The system uses **LangGraph** to orchestrate a multi-agent workflow:

1. **Schema Retrieval Agent**
   - Embeds user query
   - Searches PGVector for relevant tables
   - Returns top-K most relevant schemas

2. **SQL Generation Agent**
   - Receives conversation context
   - Gets relevant table schemas
   - Generates structured SQL with LLM
   - Outputs:
     - `sql_query`: The SQL string
     - `query_context`: How it relates to previous queries
     - `follow_up_questions`: Suggested next questions

3. **Validation Agent**
   - Parses generated SQL
   - Enforces security rules:
     - Only SELECT statements
     - No DROP, DELETE, INSERT, UPDATE
     - Maximum one trailing semicolon
   - Returns validation result

4. **Execution Agent**
   - Connects to target database
   - Executes validated SQL
   - Captures results and metadata

5. **Summary Agent**
   - Analyzes result statistics
   - Generates natural language summary
   - Example: "The query returned 15 customers with an average order value of $542.30"

---

## Schema Pipeline Explained

### Pipeline Stages

#### Stage 1: Schema Extraction

**Purpose:** Extract raw metadata from the target database

**Process:**
1. Connect to database using provided credentials
2. Query system tables/views:
   - `INFORMATION_SCHEMA.TABLES`
   - `INFORMATION_SCHEMA.COLUMNS`
   - Primary keys, foreign keys, indexes
3. Build structured representation
4. Export to YAML files

**Output:** `database_schemas/<db_flag>/schema/`
```
database_schemas/sales_prod/schema/
├── schema_index.yaml         # Master index
├── metadata.yaml             # Extraction metadata
├── dbo/
│   ├── Customers.yaml
│   ├── Orders.yaml
│   ├── Products.yaml
│   └── ...
```

**YAML Structure (before documentation):**
```yaml
table_name: Customers
schema: dbo
description: ""
keywords: []
columns:
  - name: CustomerID
    data_type: int
    is_primary_key: true
    is_nullable: false
    description: ""
    keywords: []
  - name: CustomerName
    data_type: nvarchar(100)
    is_nullable: false
    description: ""
    keywords: []
foreign_keys:
  - name: FK_Orders_Customers
    columns: [CustomerID]
    referenced_table: Orders
    referenced_columns: [CustomerID]
    relationship_type: one_to_many
```

#### Stage 2: AI Documentation

**Purpose:** Generate business-friendly descriptions and keywords

**Process:**
1. Read business context from `intro_template` file
2. For each table:
   - Load current YAML
   - Build prompt with:
     - Table structure
     - Column names and types
     - Business context
   - Call LLM (GPT-4, Claude, etc.)
   - Parse structured response
   - Update YAML with:
     - Table description
     - Column descriptions (2-3 sentences each)
     - 3 keywords per column
3. Skip already-documented tables (if `incremental=true`)

**Output:** Enhanced YAML files

**YAML Structure (after documentation):**
```yaml
table_name: Customers
schema: dbo
description: "Stores information about customers including contact details, addresses, and account status. This is the primary customer master table referenced by orders and invoices."
keywords: [customer, client, account]
columns:
  - name: CustomerID
    data_type: int
    is_primary_key: true
    is_nullable: false
    description: "Unique identifier for each customer record. This is the primary key auto-generated on customer creation."
    keywords: [id, identifier, customer_number]
  - name: CustomerName
    data_type: nvarchar(100)
    is_nullable: false
    description: "Full legal name of the customer or company. Used for invoicing and official correspondence."
    keywords: [name, company, business_name]
```

#### Stage 3: Vector Embeddings

**Purpose:** Enable semantic search for schema discovery

**Process:**
1. Create "minimal" text files from YAML:
   - Table description
   - Column list with descriptions
   - Keywords highlighted
2. Chunk text (default: 2000 chars, 100 char overlap)
3. Generate embeddings using Jina AI model
4. Store in PostgreSQL PGVector table:
   - Collection name: `<db_flag>_docs`
   - Embedding dimension: 1024
   - Metadata: table name, schema, chunk index

**Output:** PGVector collection with searchable embeddings

**Query Process:**
```python
# User query: "Show me customer orders"
user_embedding = embed("Show me customer orders")

# Semantic search
similar_chunks = vector_db.similarity_search(
    user_embedding,
    k=10,  # Top 10 chunks
    collection="sales_prod_docs"
)

# Extract table names from chunks
tables = extract_tables(similar_chunks)
# Result: ["Customers", "Orders", "OrderDetails"]
```

---

## Conversation Memory

### How It Works

Conversation memory is stored in PostgreSQL using LangGraph's **Store** abstraction.

### Storage Structure

**Namespace Hierarchy:**
```
queries/
  └── <user_id>/
      └── <session_id>/
          └── <db_flag>/
              ├── 2024-11-25T10:30:00-abc123  # Query 1
              ├── 2024-11-25T10:32:15-def456  # Query 2
              └── 2024-11-25T10:35:42-ghi789  # Query 3

conversation_summary/
  └── <user_id>/
      └── <session_id>/
          └── <db_flag>/
              └── meta  # Session summary
```

### Query Record Structure

Each query turn is stored as:
```json
{
  "query_text": "Show me top customers by revenue",
  "sql_generated": "SELECT TOP 10 CustomerID, SUM(Revenue) ...",
  "tables_used": ["Customers", "Orders", "OrderDetails"],
  "follow_up_questions": [
    "Would you like to see this by product category?",
    "Should I break this down by month?"
  ],
  "contextual_insights": "This query builds on the previous revenue analysis by focusing on customer segmentation",
  "execution_time": 0.245,
  "timestamp": "2024-11-25T10:30:00Z",
  "db_flag": "sales_prod"
}
```

### Session Summary Structure

```json
{
  "summary": "User analyzed revenue data: 1. Top products by revenue, 2. Customer segmentation, 3. Monthly trends",
  "accessed_tables": ["Customers", "Orders", "Products", "OrderDetails"],
  "total_queries": 3,
  "updated_at": "2024-11-25T10:35:42Z"
}
```

### Context-Aware Prompts

When `user_id` and `session_id` are provided:

1. **Retrieve History**:
   ```python
   history = get_query_history(user_id, session_id, db_flag, limit=5)
   ```

2. **Format Context**:
   ```
   Previous conversation:
   1. Query: "Show me top products by revenue" | SQL: SELECT TOP 10 ProductID, SUM(Revenue) ... | Tables: Products, OrderDetails
   2. Query: "Break this down by month" | SQL: SELECT MONTH(OrderDate), ProductID, SUM(Revenue) ... | Tables: Products, OrderDetails, Orders
   ```

3. **Build System Prompt**:
   ```
   You are a SQL generation assistant.
   
   CONVERSATION HISTORY:
   [formatted history]
   
   ACCESSED TABLES: Products, Orders, OrderDetails, Customers
   
   CURRENT QUERY:
   User: "Now show me the customers who bought these products"
   
   INSTRUCTIONS:
   - Use context from previous queries
   - Reference tables already accessed when relevant
   - Build on previous SQL patterns
   ```

### Benefits

✅ **Follow-up queries work naturally**:
- "Now break this down by region" → Agent knows "this" refers to previous query
- "Show me the same for last year" → Agent reuses previous logic with date filter

✅ **Table awareness**:
- System knows which tables were recently accessed
- Reduces semantic search time

✅ **Intelligent follow-ups**:
- AI suggests relevant next questions based on context

---

## Project Structure

```
sql-insight-agent/
│
├── app/                          # Main application code
│   ├── __init__.py
│   ├── main.py                   # FastAPI app and endpoints
│   ├── models.py                 # Pydantic request/response models
│   ├── user_db_config_loader.py  # Database configuration loader
│   │
│   ├── agent/                    # LLM agent logic
│   │   ├── __init__.py
│   │   ├── chain.py              # LangGraph agent construction
│   │   ├── prompt.py             # System prompts and templates
│   │   └── tools.py              # Agent tools (schema search, validation, etc.)
│   │
│   ├── core/                     # Core business logic
│   │   ├── __init__.py
│   │   ├── query_executor.py    # SQL execution against target DB
│   │   ├── result_formatter.py  # Format results (JSON/CSV/table)
│   │   ├── retriever.py         # Vector-based schema retrieval
│   │   └── sql_validator.py     # SQL security validation
│   │
│   ├── schema_pipeline/          # Schema extraction and processing
│   │   ├── __init__.py
│   │   ├── orchestrator.py      # Pipeline coordinator
│   │   ├── pipeline.py          # Extraction pipeline
│   │   ├── introspector.py      # Database metadata queries
│   │   ├── builder.py           # YAML file generation
│   │   ├── schema_documenting.py # AI-powered documentation
│   │   ├── embedding_pipeline.py # Vector embedding generation
│   │   ├── structured_docs.py   # Structured documentation parser
│   │   ├── minimal_text.py      # Minimal text file generation
│   │   └── writer.py            # YAML writer utilities
│   │
│   ├── static/                   # Frontend assets (dev UI)
│   │   ├── chat.html            # Chat interface
│   │   ├── chat.js              # Chat JavaScript
│   │   └── styles.css           # Styling
│   │
│   └── utils/                    # Utility modules
│       ├── __init__.py
│       ├── logger.py            # Logging configuration
│       └── token_tracker.py     # LLM token usage tracking
│
├── db/                           # Database models and helpers
│   ├── __init__.py
│   ├── model.py                 # SQLAlchemy models (DatabaseConfig)
│   ├── database_manager.py      # Connection pooling and session management
│   ├── conversation_memory.py   # Conversation persistence
│   └── langchain_memory.py      # LangGraph checkpoint and store setup
│
├── database_schemas/                       # Configuration files
│   ├── schemas/                 # Extracted database schemas (YAML)
│   │   └── <db_flag>crm_db/
                           db_intro
│   │                             └── crm_db.txt
│   │       ├── metadata.yaml
│   │       └── <schema>/<table>.yaml
│   │
│   ├── minimal_schemas/         # Minimal text files for embeddings
│   │   └── <db_flag>/
│   │       └── <table>_minimal.txt
│   │
│   └── db_intro/                # Business context templates
│       └── <db_flag>_intro.txt
│
├── Log/                          # Application logs
│   └── app_YYYY-MM-DD.log
│
├── tests/                        # Unit and integration tests
│   ├── __init__.py
│   └── test_*.py
│
├── .env                          # Environment variables (not in git)
├── .gitignore
├── pyproject.toml               # UV project configuration
├── uv.lock                      # Locked dependencies
├── run.py                       # Application entry point
└── README.md                    # This file
```

### Key Components

**app/main.py**
- FastAPI application
- API endpoint definitions
- Request/response handling
- Error handling and logging

**app/agent/chain.py**
- LLM selection and fallback
- Agent construction with LangGraph
- Conversation context integration
- Structured output parsing

**app/agent/tools.py**
- `search_tables_tool`: Vector-based schema search
- `get_table_details_tool`: Load full table metadata
- `validate_sql_tool`: Security validation
- `find_join_path_tool`: Automatic join discovery

**app/core/retriever.py**
- PGVector integration
- Embedding generation
- Semantic similarity search
- Table extraction from chunks

**app/schema_pipeline/orchestrator.py**
- Coordinates extraction → documentation → embedding
- Error handling and rollback
- Progress reporting

**db/conversation_memory.py**
- Store query turns
- Retrieve conversation history
- Update session summaries
- Format context for prompts

---

## Security & Validation

### SQL Injection Prevention

✅ **Whitelist Approach**: Only SELECT queries allowed

❌ **Blocked Operations**:
- `INSERT`, `UPDATE`, `DELETE`
- `DROP`, `CREATE`, `ALTER`
- `EXEC`, `EXECUTE`
- Multiple statements (more than one semicolon)
- Inline comments (`--`, `/* */`)

### Validation Process

```python
def validate_sql(sql: str) -> dict:
    """Validate SQL for safety and correctness."""
    
    # 1. Remove whitespace and normalize
    cleaned = sql.strip().upper()
    
    # 2. Check for DML/DDL keywords
    forbidden_keywords = [
        'INSERT', 'UPDATE', 'DELETE', 'DROP', 'CREATE', 
        'ALTER', 'TRUNCATE', 'EXEC', 'EXECUTE'
    ]
    for keyword in forbidden_keywords:
        if keyword in cleaned:
            return {
                "valid": False,
                "reason": f"Forbidden keyword: {keyword}"
            }
    
    # 3. Check for multiple statements
    semicolon_count = sql.count(';')
    if semicolon_count > 1:
        return {
            "valid": False,
            "reason": "Multiple statements not allowed"
        }
    
    # 4. Check for inline comments
    if '--' in sql or '/*' in sql:
        return {
            "valid": False,
            "reason": "Comments not allowed in SQL"
        }
    
    # 5. Verify it starts with SELECT
    if not cleaned.startswith('SELECT'):
        return {
            "valid": False,
            "reason": "Only SELECT queries allowed"
        }
    
    return {"valid": True, "reason": None}
```

### Database Connection Security

✅ **Read-Only Users**: Recommend creating dedicated read-only database users

✅ **Connection Pooling**: Prevents connection exhaustion attacks

✅ **Query Timeout**: Default 30s prevents long-running queries

✅ **Row Limit**: Default 1000 rows prevents excessive memory usage

✅ **No Dynamic Connection Strings**: All connections pre-configured via enrollment

### Recommended Database User Setup

**SQL Server:**
```sql
-- Create read-only user
CREATE LOGIN SQL_AGENT WITH PASSWORD = 'SecurePassword123';
CREATE USER SQL_AGENT FOR LOGIN SQL_AGENT;

-- Grant read-only access
ALTER ROLE db_datareader ADD MEMBER SQL_AGENT;

-- Deny write permissions (extra safety)
DENY INSERT, UPDATE, DELETE, EXECUTE TO SQL_AGENT;
```

**PostgreSQL:**
```sql
-- Create read-only user
CREATE USER SQL_AGENT WITH PASSWORD 'SecurePassword123';

-- Grant connect and usage
GRANT CONNECT ON DATABASE sales_db TO SQL_AGENT;
GRANT USAGE ON SCHEMA public TO SQL_AGENT;

-- Grant SELECT only
GRANT SELECT ON ALL TABLES IN SCHEMA public TO SQL_AGENT;
ALTER DEFAULT PRIVILEGES IN SCHEMA public 
    GRANT SELECT ON TABLES TO SQL_AGENT;
```

**MySQL:**
```sql
-- Create read-only user
CREATE USER 'SQL_AGENT'@'%' IDENTIFIED BY 'SecurePassword123';

-- Grant SELECT only
GRANT SELECT ON sales_db.* TO 'SQL_AGENT'@'%';
FLUSH PRIVILEGES;
```

---

## Troubleshooting

### Common Issues

#### 1. "All LLM providers failed"

**Cause:** No API keys configured or invalid keys

**Solution:**
```bash
# Check your .env file
cat .env | grep API_KEY

# Verify at least one key is set
echo $OPENAI_API_KEY  # Should output your key
```

**Test individual providers:**
```python
from app.agent.chain import get_available_providers

providers = get_available_providers()
print(providers)  # Should list at least one provider
```

#### 2. "POSTGRES_CONNECTION_STRING is required"

**Cause:** PostgreSQL connection string not configured

**Solution:**
1. Ensure PostgreSQL is running
2. Verify PGVector extension is installed
3. Add to `.env`:
   ```
   POSTGRES_CONNECTION_STRING=postgresql://user:pass@localhost:5432/SQL_AGENT
   ```

**Test connection:**
```bash
psql "postgresql://user:pass@localhost:5432/SQL_AGENT" -c "SELECT 1;"
```

#### 3. "Table 'X' not found in schema"

**Cause:** Schema not enrolled or embeddings not generated

**Solution:**
```bash
# Check if schema exists
ls database_schemas/<db_flag>/schema/

# If missing, enroll the database
curl -X POST http://localhost:8000/schemas/enroll \
  -H "Content-Type: application/json" \
  -d '{"db_flag":"<db_flag>","db_type":"mssql","connection_string":"..."}'
```

#### 4. Database Connection Errors

**SQL Server:**
- Install ODBC Driver: [Download](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server)
- Verify driver name: `odbcinst -q -d`
- Check server/port accessibility: `telnet <host> 1433`

**MySQL:**
- Ensure `pymysql` is installed: `uv pip list | grep pymysql`
- Test connection: `mysql -h <host> -u <user> -p <database>`

**PostgreSQL:**
- Check `psycopg2-binary` installed: `uv pip list | grep psycopg2`
- Test connection: `psql -h <host> -U <user> -d <database>`

#### 5. "PGVector extension not found"

**Cause:** PGVector not installed in PostgreSQL

**Solution:**
```sql
-- Connect as superuser
sudo -u postgres psql

-- Install extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Verify
\dx vector
```

#### 6. Slow Query Performance

**Cause:** Large result sets or missing indexes

**Solutions:**
- Reduce `max_rows` in database config
- Add indexes to frequently queried columns
- Use `LIMIT` clauses in natural language queries
- Example: "Show me the first 10 customers"

#### 7. Documentation Takes Too Long

**Cause:** LLM rate limits or large schema

**Solutions:**
- Use `incremental_documentation: true` (default)
- Process in batches: enroll subsets of tables using `include_schemas`
- Use faster LLM provider (Groq, DeepSeek)
- Increase timeout in LLM client configuration

#### 8. Memory Errors During Embedding

**Cause:** Processing very large schemas

**Solutions:**
- Reduce `chunk_size` in embedding settings:
  ```python
  settings = SchemaEmbeddingSettings(
      chunk_size=1000,  # Default: 2000
      chunk_overlap=50   # Default: 100
  )
  ```
- Process tables in batches
- Increase system RAM or use swap space

---

## Advanced Usage

### Custom Business Context

Create domain-specific context files to improve documentation quality:

**Example: database_schemas/sales_prod/db_intro/sales_context.txt**
```
This is a B2B sales management database for a medical equipment distributor.

Key concepts:
- Customers are healthcare facilities (hospitals, clinics)
- Products are durable medical equipment (DME) with FDA tracking
- Orders follow a multi-step approval process
- Inventory is tracked across multiple warehouse locations
- Pricing includes insurance reimbursement codes

Business rules:
- All orders require physician authorization
- Insurance pre-authorization affects payment terms
- Products have serial numbers for regulatory compliance
```

**Usage during enrollment:**
```json
{
  "db_flag": "sales_prod",
  "intro_template": "database_schemas/sales_prod/db_intro/sales_context.txt",
  ...
}
```

### Monitoring LLM Costs

Enable LangSmith tracing to monitor token usage and costs:

**.env:**
```bash
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=ls__your_langsmith_key
LANGCHAIN_PROJECT=sql-insight-agent-prod
```

**View traces:**
https://smith.langchain.com

### Conversation Management

**Start a new conversation:**
```python
import requests

response = requests.post('http://localhost:8000/query', json={
    "query": "Show me recent orders",
    "db_flag": "sales_prod",
    "user_id": "analyst@company.com",
    "session_id": "2024-11-25-session-001"
})
```

**Continue the conversation:**
```python
response = requests.post('http://localhost:8000/query', json={
    "query": "Now show me the customers for those orders",
    "db_flag": "sales_prod",
    "user_id": "analyst@company.com",
    "session_id": "2024-11-25-session-001"  # Same session ID
})
```

**Clear conversation history:**
```python
from db.conversation_memory import clear_conversation_history

clear_conversation_history(
    user_id="analyst@company.com",
    session_id="2024-11-25-session-001",
    db_flag="sales_prod"
)
```

### Custom LLM Configuration

Modify `app/agent/chain.py` to use custom models:

```python
def get_llm(provider: str = None):
    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model="gpt-4-turbo",  # Change model here
            temperature=0,
            max_tokens=2000
        )
    # ... other providers
```

### Batch Query Processing

Process multiple queries programmatically:

```python
import requests

queries = [
    "Show me top customers by revenue",
    "List products with low inventory",
    "Find orders pending approval"
]

results = []
for query in queries:
    response = requests.post('http://localhost:8000/query', json={
        "query": query,
        "db_flag": "sales_prod",
        "output_format": "json"
    })
    results.append(response.json())

# Export to CSV
import pandas as pd
for i, result in enumerate(results):
    if result['status'] == 'success':
        df = pd.DataFrame(result['data']['results'])
        df.to_csv(f'query_{i+1}_results.csv', index=False)
```

### Database Configuration Management

**List all enrolled databases:**
```python
from db.database_manager import get_session, get_project_db_connection_string
from db.model import DatabaseConfig

session = get_session(get_project_db_connection_string())
databases = session.query(DatabaseConfig).all()

for db in databases:
    print(f"{db.db_flag}: {db.description}")
    print(f"  Type: {db.db_type}")
    print(f"  Schema Extracted: {db.schema_extracted}")
    print(f"  Extraction Date: {db.schema_extraction_date}")
```

**Update database configuration:**
```python
session = get_session(get_project_db_connection_string())
db_config = session.query(DatabaseConfig).filter_by(db_flag="sales_prod").first()

db_config.max_rows = 5000
db_config.query_timeout = 60
session.commit()
```

---

## Performance Optimization

### Indexing Strategies

**PostgreSQL PGVector:**
```sql
-- Create IVFFlat index for faster similarity search
CREATE INDEX ON langchain_pg_embedding 
USING ivfflat (embedding vector_cosine_ops) 
WITH (lists = 100);

-- Or HNSW for better accuracy (PostgreSQL 16+)
CREATE INDEX ON langchain_pg_embedding 
USING hnsw (embedding vector_cosine_ops);
```

### Caching

The system uses `@lru_cache` for:
- Database connections
- LLM instances
- Agent instances

**Clear cache if needed:**
```python
from app.agent.chain import get_cached_agent
get_cached_agent.cache_clear()
```

### Connection Pooling

Adjust pool size for high concurrency:

```python
# db/database_manager.py
def get_engine(connection_string: str) -> Engine:
    normalized = _normalize_connection_string(connection_string)
    engine = create_engine(
        normalized,
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_size=20,        # Increase from default 5
        max_overflow=40      # Increase from default 10
    )
    return engine
```

---

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## License

MIT License - see LICENSE file for details

---

## Support

For issues, questions, or feature requests:
- Open a GitHub Issue
- Check existing documentation
- Review logs in `Log/app_YYYY-MM-DD.log`

---

## Changelog

### Version 1.0.0 (2024-11-25)
- Initial release
- Multi-LLM support with automatic fallback
- Conversation memory and context awareness
- Schema extraction and documentation pipeline
- PGVector-based semantic search
- FastAPI REST API
- Security validation
- Natural language result summaries
- Developer chat UI

---

**Built with ❤️ using LangChain, LangGraph, FastAPI, and PostgreSQL**
