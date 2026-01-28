# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Lenny's Podcast MCP Server** is a Model Context Protocol (MCP) server that enables semantic search and content retrieval over Lenny Rachitsky's podcast transcript database. The system exposes podcast transcripts as searchable tools through MCP, allowing Claude and other MCP-compatible clients to query podcast discussions on specific topics, retrieve full episode content, and browse available episodes.

## Architecture & Data Flow

### High-Level Architecture

The system follows a three-component architecture:

1. **Data Ingestion Pipeline** (`ingest_transcripts.py` or `ingest_transcripts_v2.py`)
   - Parses transcript files from the `transcripts/` directory
   - Splits transcripts into overlapping chunks (800 tokens target, 100 token overlap)
   - Generates embeddings using OpenAI's `text-embedding-3-small` model
   - Stores chunks and embeddings in a ChromaDB vector database

2. **Vector Database** (ChromaDB persistent client in `database/`)
   - Stores embeddings and metadata for all transcript chunks
   - Single collection: `lenny_transcripts`
   - Persisted SQLite database for durability
   - Supports semantic (vector) search and metadata filtering

3. **MCP Server** (`mcp_server.py`)
   - Implements the MCP protocol using stdio transport
   - Exposes three tools for clients:
     - `search_transcripts`: Semantic search with max 20 results
     - `get_episode_content`: Full episode retrieval by guest name
     - `list_all_episodes`: Browse all indexed episodes
   - Handles query embedding generation and result formatting

### Data Format & Parsing

Transcript files are plain text stored in `transcripts/` with structured speaker/timestamp headers. The ingest script supports two formats:

- **Format 1**: `Speaker Name (HH:MM:SS):` or `Speaker Name (MM:SS):`
- **Format 2**: `[HH:MM:SS] Speaker Name:` or `[MM:SS] Speaker Name:`

The parser normalizes timestamps to HH:MM:SS format and groups contiguous speaker segments.

### Chunking Strategy

Transcripts are split into overlapping chunks to preserve context:
- **Target size**: 800 tokens (using tiktoken's cl100k_base encoding)
- **Overlap**: 100 tokens between consecutive chunks
- Segments larger than 800 tokens are split word-by-word to maintain boundaries
- Each chunk stores: text content, guest_name, timestamp, token count

## Development Workflow

### Common Commands

**Data Ingestion:**
```bash
# Run the ingestion script (v2 supports more formats)
python3 ingest_transcripts_v2.py

# Or use v1 if needed
python3 ingest_transcripts.py

# Re-ingest only previously failed files
python3 ingest_transcripts_v2.py --only-failed
```

**Running the MCP Server:**
```bash
python3 mcp_server.py
```

**Managing Dependencies:**
```bash
# Install dependencies
pip install -r requirements.txt

# View current dependencies
cat requirements.txt
```

### Environment Setup

The project requires these environment variables (configure in `.env`):
- `OPENAI_API_KEY`: OpenAI API key for embeddings and completions
- `DROPBOX_ACCESS_TOKEN`: Dropbox token (for transcript sync, not currently used in core server)

The database path is hardcoded in both ingestion scripts. Update if needed:
- Ingestion v1: `./database`
- Ingestion v2: `/Users/aryan/Documents/lenny-mcp-server/database` (absolute path)
- MCP Server: `/Users/aryan/Documents/lenny-mcp-server/database`

### Key Implementation Details

**Search Implementation** (mcp_server.py:111-152):
- Generates query embedding using OpenAI embeddings API
- Performs semantic search in ChromaDB with configurable max results (capped at 20)
- Returns top-N results ranked by relevance (1 - distance score)
- Formats results with guest name, timestamp, and relevance score

**Episode Retrieval** (mcp_server.py:154-198):
- Filters database by exact guest_name metadata match
- Supports partial name matching if exact match not found
- Returns all chunks for that episode sorted by timestamp
- Useful for comprehensive episode review

**Chunking Algorithm** (ingest_transcripts_v2.py:128+):
- Incremental chunk building: accumulates segments until token threshold
- Large segment splitting: when a single segment exceeds CHUNK_SIZE, it's split word-by-word
- Context preservation: 100 token overlap between chunks ensures topic continuity
- Token counting: tiktoken's cl100k_base for accurate estimation

## Key Files & Responsibilities

- **mcp_server.py**: MCP protocol handler, tool definitions, query execution
- **ingest_transcripts_v2.py**: Multi-format transcript parser, vectorization pipeline (recommended)
- **ingest_transcripts.py**: Original ingestion script (legacy, limited format support)
- **database/**: ChromaDB persistent storage (SQLite + vector indexes)
- **transcripts/**: Source transcript text files (300+ episodes)
- **.env**: Environment configuration (contains API keys)

## Important Implementation Notes

### ChromaDB Collection Structure

The collection stores embeddings with the following metadata per chunk:
- `guest_name`: Name of the podcast guest/episode
- `file_name`: Original transcript filename
- `timestamp`: Timestamp of the chunk's start
- `tokens`: Number of tokens in the chunk
- Document: The actual transcript text

### Error Handling

- If the ChromaDB collection doesn't exist, the MCP server exits with an error message
- Partial name matching for episode retrieval helps with formatting variations
- Search gracefully handles no results (returns message instead of crashing)

### Performance Considerations

- ChromaDB queries are fast for semantic search (embedding-based indexing)
- Large transcript chunks (800 tokens) reduce query latency vs. smaller chunks
- Vector database grows with transcript volume (~300 episodes indexed)

## Testing & Validation

To verify the system is working:

```bash
# Check database exists and has collection
python3 -c "
import chromadb
client = chromadb.PersistentClient(path='/Users/aryan/Documents/lenny-mcp-server/database')
collection = client.get_collection(name='lenny_transcripts')
print(f'Collection has {collection.count()} documents')
"

# Test MCP server (requires MCP client)
python3 mcp_server.py
```

## Extending the System

**Adding New Tools:**
1. Define tool in `list_tools()` with inputSchema
2. Implement handler in `call_tool()` with tool name check
3. Return TextContent with formatted results

**Re-ingesting Transcripts:**
1. Update transcript files in `transcripts/`
2. Delete or archive old database directory
3. Run `ingest_transcripts_v2.py` to rebuild
4. Restart MCP server

**Changing Chunk Strategy:**
Modify these constants in ingest script:
- `CHUNK_SIZE`: Target tokens per chunk
- `OVERLAP`: Token overlap between chunks
