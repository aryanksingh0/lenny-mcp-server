# Lenny's Podcast MCP Server

Search through 300+ episodes of Lenny's Podcast using AI.

This is an MCP (Model Context Protocol) server that lets Claude (or any MCP-compatible AI) search podcast transcripts and find relevant discussions.

---

## What Can It Do?

- **Search transcripts** — Ask questions like "What did guests say about product-market fit?"
- **Get full episodes** — Retrieve the complete transcript of any episode by guest name
- **Browse episodes** — See all available episodes in the database

---

## Setup (Step by Step)

### 1. Clone this repo

```bash
git clone https://github.com/aryanksingh0/lenny-mcp-server.git
cd lenny-mcp-server
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Add your OpenAI API key

Create a file called `.env` in the project folder:

```
OPENAI_API_KEY=your-api-key-here
```

(Get your API key from [platform.openai.com](https://platform.openai.com/api-keys))

### 4. Build the database

This creates searchable embeddings from all the transcripts:

```bash
python3 ingest_transcripts.py
```

This takes a few minutes and only needs to be done once.

### 5. Run the server

```bash
python3 mcp_server.py
```

---

## Using with Claude Desktop

Add this to your Claude Desktop config file:

**Mac:** `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "lenny-podcast": {
      "command": "python3",
      "args": ["/path/to/lenny-mcp-server/mcp_server.py"]
    }
  }
}
```

Replace `/path/to/` with the actual path where you cloned the repo.

Restart Claude Desktop, and you can now ask Claude questions about Lenny's Podcast!

---

## Example Questions

Once set up, try asking Claude:

- "What have guests said about hiring?"
- "Find discussions about growth strategies"
- "Get the full transcript of the Brian Chesky episode"

---

## Requirements

- Python 3.8+
- OpenAI API key (for generating embeddings)

---

Made with Claude
