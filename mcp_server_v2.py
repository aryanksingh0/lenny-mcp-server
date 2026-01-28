#!/usr/bin/env python3
"""
Lenny's Podcast MCP Server - V2 (Enhanced Accuracy)
Adds speaker filtering, hybrid search, and improved result ranking
"""

import os
import asyncio
from typing import Any, Sequence
import chromadb
from openai import OpenAI
from dotenv import load_dotenv
from mcp.server import Server
from mcp.types import Tool, TextContent, ImageContent, EmbeddedResource
import mcp.server.stdio
import re

# Load environment variables
load_dotenv()

# Initialize clients
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
chroma_client = chromadb.PersistentClient(path="/Users/aryan/Documents/lenny-mcp-server/database")

# Get collection (try v3 first, fall back to original)
try:
    collection = chroma_client.get_collection(name="lenny_transcripts_v3")
    print("Using enhanced collection: lenny_transcripts_v3")
except:
    try:
        collection = chroma_client.get_collection(name="lenny_transcripts")
        print("Using original collection: lenny_transcripts")
    except:
        print("ERROR: No database collection found. Please run an ingestion script first.")
        exit(1)

# Initialize MCP server
app = Server("lenny-podcasts-enhanced")

def generate_query_embedding(query: str) -> list[float]:
    """Generate embedding for a search query."""
    response = openai_client.embeddings.create(
        model="text-embedding-3-small",
        input=query
    )
    return response.data[0].embedding

def extract_keywords(query: str) -> list[str]:
    """Extract potential keywords from query for hybrid search."""
    # Remove common stop words and extract meaningful terms
    stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'from', 'about', 'as', 'into', 'through', 'during', 'what', 'how', 'when', 'where', 'why', 'who'}
    words = re.findall(r'\b\w+\b', query.lower())
    keywords = [w for w in words if w not in stop_words and len(w) > 2]
    return keywords

def keyword_score(text: str, keywords: list[str]) -> float:
    """Calculate keyword match score for hybrid search."""
    if not keywords:
        return 0.0

    text_lower = text.lower()
    matches = sum(1 for keyword in keywords if keyword in text_lower)
    return matches / len(keywords)

def hybrid_search_score(semantic_score: float, keyword_score: float, alpha: float = 0.7) -> float:
    """
    Combine semantic and keyword scores.
    alpha: weight for semantic score (0.7 = 70% semantic, 30% keyword)
    """
    return alpha * semantic_score + (1 - alpha) * keyword_score

@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="search_transcripts",
            description=(
                "Search through Lenny's podcast transcripts using semantic + keyword hybrid search. "
                "Returns relevant segments with guest name, timestamp, speaker info, and content. "
                "Supports filtering by speaker (guest-only, host-only, or mixed). "
                "Use this to find discussions on specific topics across all episodes."
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
                    },
                    "speaker_filter": {
                        "type": "string",
                        "description": "Filter by speaker: 'guest_only' (70%+ guest), 'host_only' (70%+ host), 'mixed', or 'all' (default: 'all')",
                        "enum": ["all", "guest_only", "host_only", "mixed"],
                        "default": "all"
                    },
                    "min_relevance": {
                        "type": "number",
                        "description": "Minimum relevance score (0.0-1.0, default: 0.0). Higher values return only highly relevant results.",
                        "default": 0.0
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="get_episode_content",
            description=(
                "Retrieve all content from a specific episode by guest name. "
                "Returns all transcript chunks for that episode with speaker information. "
                "Use this when you want to read everything from a specific episode."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "guest_name": {
                        "type": "string",
                        "description": "The name of the guest (e.g., 'Rahul Vohra', 'Elena Verna', 'Julie Zhuo')"
                    },
                    "speaker_filter": {
                        "type": "string",
                        "description": "Filter by speaker: 'guest_only', 'host_only', 'mixed', or 'all' (default: 'all')",
                        "enum": ["all", "guest_only", "host_only", "mixed"],
                        "default": "all"
                    }
                },
                "required": ["guest_name"]
            }
        ),
        Tool(
            name="list_all_episodes",
            description=(
                "Get a list of all available podcast episodes in the database. "
                "Returns guest names and chunk statistics. "
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
        speaker_filter = arguments.get("speaker_filter", "all")
        min_relevance = arguments.get("min_relevance", 0.0)

        # Generate embedding for semantic search
        query_embedding = generate_query_embedding(query)

        # Extract keywords for hybrid search
        keywords = extract_keywords(query)

        # Build metadata filter for speaker
        where_filter = None
        if speaker_filter == "guest_only":
            where_filter = {"speaker_category": "guest_heavy"}
        elif speaker_filter == "host_only":
            where_filter = {"speaker_category": "host_heavy"}
        elif speaker_filter == "mixed":
            where_filter = {"speaker_category": "mixed"}

        # Search in Chroma with increased results for re-ranking
        search_results = max_results * 3  # Get 3x results for hybrid re-ranking
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=search_results,
            where=where_filter if where_filter else None,
            include=["documents", "metadatas", "distances"]
        )

        if not results['documents'][0]:
            filter_msg = f" with speaker filter '{speaker_filter}'" if speaker_filter != "all" else ""
            return [TextContent(
                type="text",
                text=f"No results found for query: '{query}'{filter_msg}"
            )]

        # Hybrid search: re-rank with keyword scores
        ranked_results = []
        for doc, metadata, distance in zip(
            results['documents'][0],
            results['metadatas'][0],
            results['distances'][0]
        ):
            semantic_score = 1 - distance
            kw_score = keyword_score(doc, keywords)
            final_score = hybrid_search_score(semantic_score, kw_score)

            ranked_results.append({
                'doc': doc,
                'metadata': metadata,
                'semantic_score': semantic_score,
                'keyword_score': kw_score,
                'final_score': final_score
            })

        # Sort by final score and apply relevance threshold
        ranked_results.sort(key=lambda x: x['final_score'], reverse=True)
        ranked_results = [r for r in ranked_results if r['final_score'] >= min_relevance]
        ranked_results = ranked_results[:max_results]

        if not ranked_results:
            return [TextContent(
                type="text",
                text=f"No results found above relevance threshold {min_relevance} for query: '{query}'"
            )]

        # Format results
        formatted_results = []
        filter_msg = f" (filtered: {speaker_filter})" if speaker_filter != "all" else ""
        formatted_results.append(f"Found {len(ranked_results)} results for: '{query}'{filter_msg}\n")
        formatted_results.append("=" * 80 + "\n")

        for idx, result in enumerate(ranked_results, 1):
            doc = result['doc']
            metadata = result['metadata']

            formatted_results.append(f"\n**Result {idx}:**")
            formatted_results.append(f"**Guest:** {metadata['guest_name']}")
            formatted_results.append(f"**Timestamp:** {metadata['timestamp']}")
            formatted_results.append(f"**Relevance:** {result['final_score']:.3f} (semantic: {result['semantic_score']:.3f}, keyword: {result['keyword_score']:.3f})")

            # Add speaker info if available
            if 'speaker_category' in metadata:
                formatted_results.append(f"**Speaker Mix:** {metadata['speaker_category']} (guest: {metadata['guest_percentage']}%, host: {metadata['host_percentage']}%)")

            formatted_results.append(f"\n**Content:**")
            formatted_results.append(doc)
            formatted_results.append("\n" + "-" * 80 + "\n")

        return [TextContent(
            type="text",
            text="\n".join(formatted_results)
        )]

    elif name == "get_episode_content":
        guest_name = arguments.get("guest_name")
        speaker_filter = arguments.get("speaker_filter", "all")

        # Build where clause with speaker filter
        where_clause = {"guest_name": guest_name}

        if speaker_filter == "guest_only":
            where_clause["speaker_category"] = "guest_heavy"
        elif speaker_filter == "host_only":
            where_clause["speaker_category"] = "host_heavy"
        elif speaker_filter == "mixed":
            where_clause["speaker_category"] = "mixed"

        # Query all chunks for this guest
        results = collection.get(
            where=where_clause,
            include=["documents", "metadatas"]
        )

        if not results['documents']:
            # Try partial match without speaker filter
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
        filter_msg = f" (filtered: {speaker_filter})" if speaker_filter != "all" else ""
        formatted_results.append(f"Episode with {guest_name}{filter_msg}")
        formatted_results.append(f"Total segments: {len(chunks_with_meta)}")
        formatted_results.append("=" * 80 + "\n")

        for doc, metadata in chunks_with_meta:
            formatted_results.append(f"\n**[{metadata['timestamp']}]**")
            if 'speaker_category' in metadata:
                formatted_results.append(f"*({metadata['speaker_category']}: {metadata['guest_percentage']}% guest, {metadata['host_percentage']}% host)*")
            formatted_results.append(doc)
            formatted_results.append("")

        return [TextContent(
            type="text",
            text="\n".join(formatted_results)
        )]

    elif name == "list_all_episodes":
        # Get all unique guests with statistics
        all_results = collection.get(include=["metadatas"])
        guests = {}

        for metadata in all_results['metadatas']:
            guest_name = metadata['guest_name']
            if guest_name not in guests:
                guests[guest_name] = {
                    'file_name': metadata['file_name'],
                    'chunk_count': 0,
                    'guest_heavy': 0,
                    'host_heavy': 0,
                    'mixed': 0
                }
            guests[guest_name]['chunk_count'] += 1

            # Track speaker distribution if available
            if 'speaker_category' in metadata:
                category = metadata['speaker_category']
                guests[guest_name][category] = guests[guest_name].get(category, 0) + 1

        # Sort alphabetically
        sorted_guests = sorted(guests.items())

        formatted_results = []
        formatted_results.append(f"Available Episodes: {len(sorted_guests)} episodes")
        formatted_results.append("=" * 80 + "\n")

        for guest_name, info in sorted_guests:
            chunk_info = f"{info['chunk_count']} segments"
            if 'speaker_category' in all_results['metadatas'][0]:
                chunk_info += f" (guest-heavy: {info.get('guest_heavy', 0)}, mixed: {info.get('mixed', 0)}, host-heavy: {info.get('host_heavy', 0)})"
            formatted_results.append(f"- {guest_name} ({chunk_info})")

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
