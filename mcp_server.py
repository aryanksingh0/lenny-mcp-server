#!/usr/bin/env python3
"""
Lenny's Podcast MCP Server
Exposes tools for searching and querying podcast transcripts
"""

import os
import asyncio
from typing import Any, Sequence
import chromadb
from openai import OpenAI
from dotenv import load_dotenv
from mcp.server import Server
from mcp.types import (
    Tool,
    TextContent,
    ImageContent,
    EmbeddedResource,
)
import mcp.server.stdio

# Load environment variables
load_dotenv()

# Initialize clients
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
chroma_client = chromadb.PersistentClient(path="/Users/aryan/Documents/lenny-mcp-server/database")

# Get collection
try:
    collection = chroma_client.get_collection(name="lenny_transcripts")
except:
    print("ERROR: Database collection not found. Please run ingest_transcripts.py first.")
    exit(1)

# Initialize MCP server
app = Server("lenny-podcasts")

def generate_query_embedding(query: str) -> list[float]:
    """Generate embedding for a search query."""
    response = openai_client.embeddings.create(
        model="text-embedding-3-small",
        input=query
    )
    return response.data[0].embedding

@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="search_transcripts",
            description=(
                "Search through all of Lenny's podcast transcripts using semantic search. "
                "Returns the most relevant segments with guest name, timestamp, and content. "
                "Use this to find discussions on specific topics, concepts, or questions across all episodes."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query (e.g., 'product-market fit', 'growth loops', 'pricing strategies')"
                    },
                    "max_results": {
                        "type": "number",
                        "description": "Number of results to return (default: 5, max: 20)",
                        "default": 5
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="get_episode_content",
            description=(
                "Retrieve all content from a specific episode by guest name. "
                "Returns all transcript chunks for that episode. "
                "Use this when you want to read everything a specific guest discussed."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "guest_name": {
                        "type": "string",
                        "description": "The name of the guest (e.g., 'Rahul Vohra', 'Elena Verna', 'Julie Zhuo')"
                    }
                },
                "required": ["guest_name"]
            }
        ),
        Tool(
            name="list_all_episodes",
            description=(
                "Get a list of all available podcast episodes in the database. "
                "Returns guest names and file information. "
                "Use this to browse what's available or find the exact guest name."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        )
    ]

@app.call_tool()
async def call_tool(name: str, arguments: Any) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
    """Handle tool calls."""
    
    if name == "search_transcripts":
        query = arguments.get("query")
        max_results = min(arguments.get("max_results", 5), 20)
        
        # Generate embedding for query
        query_embedding = generate_query_embedding(query)
        
        # Search in Chroma
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=max_results,
            include=["documents", "metadatas", "distances"]
        )
        
        # Format results
        if not results['documents'][0]:
            return [TextContent(
                type="text",
                text=f"No results found for query: '{query}'"
            )]
        
        formatted_results = []
        formatted_results.append(f"Found {len(results['documents'][0])} results for: '{query}'\n")
        formatted_results.append("=" * 80 + "\n")
        
        for idx, (doc, metadata, distance) in enumerate(zip(
            results['documents'][0],
            results['metadatas'][0],
            results['distances'][0]
        ), 1):
            formatted_results.append(f"\n**Result {idx}:**")
            formatted_results.append(f"**Guest:** {metadata['guest_name']}")
            formatted_results.append(f"**Timestamp:** {metadata['timestamp']}")
            formatted_results.append(f"**Relevance Score:** {1 - distance:.3f}")
            formatted_results.append(f"\n**Content:**")
            formatted_results.append(doc)
            formatted_results.append("\n" + "-" * 80 + "\n")
        
        return [TextContent(
            type="text",
            text="\n".join(formatted_results)
        )]
    
    elif name == "get_episode_content":
        guest_name = arguments.get("guest_name")
        
        # Query all chunks for this guest
        results = collection.get(
            where={"guest_name": guest_name},
            include=["documents", "metadatas"]
        )
        
        if not results['documents']:
            # Try partial match
            all_results = collection.get(include=["metadatas"])
            all_guests = set([m['guest_name'] for m in all_results['metadatas']])
            matching = [g for g in all_guests if guest_name.lower() in g.lower()]
            
            if matching:
                return [TextContent(
                    type="text",
                    text=f"No exact match found for '{guest_name}'. Did you mean one of these?\n" + 
                         "\n".join(f"- {g}" for g in matching[:10])
                )]
            else:
                return [TextContent(
                    type="text",
                    text=f"No episode found for guest: '{guest_name}'. Use list_all_episodes to see available guests."
                )]
        
        # Sort by timestamp
        chunks_with_meta = list(zip(results['documents'], results['metadatas']))
        chunks_with_meta.sort(key=lambda x: x[1]['timestamp'])
        
        formatted_results = []
        formatted_results.append(f"Episode with {guest_name}")
        formatted_results.append(f"Total segments: {len(chunks_with_meta)}")
        formatted_results.append("=" * 80 + "\n")
        
        for doc, metadata in chunks_with_meta:
            formatted_results.append(f"\n**[{metadata['timestamp']}]**")
            formatted_results.append(doc)
            formatted_results.append("")
        
        return [TextContent(
            type="text",
            text="\n".join(formatted_results)
        )]
    
    elif name == "list_all_episodes":
        # Get all unique guests
        all_results = collection.get(include=["metadatas"])
        guests = {}
        
        for metadata in all_results['metadatas']:
            guest_name = metadata['guest_name']
            if guest_name not in guests:
                guests[guest_name] = {
                    'file_name': metadata['file_name'],
                    'chunk_count': 0
                }
            guests[guest_name]['chunk_count'] += 1
        
        # Sort alphabetically
        sorted_guests = sorted(guests.items())
        
        formatted_results = []
        formatted_results.append(f"Available Episodes: {len(sorted_guests)} episodes")
        formatted_results.append("=" * 80 + "\n")
        
        for guest_name, info in sorted_guests:
            formatted_results.append(f"- {guest_name} ({info['chunk_count']} segments)")
        
        return [TextContent(
            type="text",
            text="\n".join(formatted_results)
        )]
    
    else:
        return [TextContent(
            type="text",
            text=f"Unknown tool: {name}"
        )]

async def main():
    """Run the MCP server."""
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options()
        )

if __name__ == "__main__":
    asyncio.run(main())
