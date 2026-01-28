#!/usr/bin/env python3
"""
Lenny's Podcast Transcript Ingestion Script
Processes transcript files and creates a searchable vector database
"""

import os
import re
from pathlib import Path
from typing import List, Dict, Tuple
import chromadb
from chromadb.config import Settings
from openai import OpenAI
from dotenv import load_dotenv
import tiktoken

# Load environment variables
load_dotenv()

# Initialize OpenAI client
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Initialize tokenizer for counting tokens
encoding = tiktoken.get_encoding("cl100k_base")

# Configuration
TRANSCRIPTS_DIR = "./transcripts"
DATABASE_DIR = "./database"
CHUNK_SIZE = 800  # Target tokens per chunk
OVERLAP = 100     # Overlap between chunks to maintain context

def count_tokens(text: str) -> int:
    """Count the number of tokens in a text string."""
    return len(encoding.encode(text))

def parse_transcript(file_path: str) -> Tuple[str, List[Dict]]:
    """
    Parse a transcript file and extract structured data.
    
    Returns:
        Tuple of (guest_name, list of parsed segments)
    """
    # Extract guest name from filename
    guest_name = Path(file_path).stem
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Pattern to match speaker and timestamp: "Speaker Name (HH:MM:SS):"
    pattern = r'^([^(]+)\s*\((\d{2}:\d{2}:\d{2})\):$'
    
    segments = []
    lines = content.split('\n')
    current_speaker = None
    current_timestamp = None
    current_text = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        # Check if this line is a speaker/timestamp header
        match = re.match(pattern, line)
        if match:
            # Save previous segment if exists
            if current_speaker and current_text:
                segments.append({
                    'speaker': current_speaker.strip(),
                    'timestamp': current_timestamp,
                    'text': ' '.join(current_text)
                })
            
            # Start new segment
            current_speaker = match.group(1)
            current_timestamp = match.group(2)
            current_text = []
        else:
            # This is content, add to current segment
            if current_speaker:
                current_text.append(line)
    
    # Don't forget the last segment
    if current_speaker and current_text:
        segments.append({
            'speaker': current_speaker.strip(),
            'timestamp': current_timestamp,
            'text': ' '.join(current_text)
        })
    
    return guest_name, segments

def create_chunks(segments: List[Dict], guest_name: str) -> List[Dict]:
    """
    Create chunks from segments, aiming for CHUNK_SIZE tokens each.
    """
    chunks = []
    current_chunk = []
    current_tokens = 0
    chunk_start_timestamp = None
    
    for segment in segments:
        segment_text = f"{segment['speaker']}: {segment['text']}"
        segment_tokens = count_tokens(segment_text)
        
        # If this segment alone is larger than chunk size, split it
        if segment_tokens > CHUNK_SIZE:
            # Save current chunk if it exists
            if current_chunk:
                chunks.append({
                    'text': '\n\n'.join(current_chunk),
                    'guest_name': guest_name,
                    'start_timestamp': chunk_start_timestamp,
                    'tokens': current_tokens
                })
                current_chunk = []
                current_tokens = 0
            
            # Split large segment into multiple chunks
            words = segment_text.split()
            temp_chunk = []
            temp_tokens = 0
            
            for word in words:
                word_tokens = count_tokens(word + " ")
                if temp_tokens + word_tokens > CHUNK_SIZE and temp_chunk:
                    chunks.append({
                        'text': ' '.join(temp_chunk),
                        'guest_name': guest_name,
                        'start_timestamp': segment['timestamp'],
                        'tokens': temp_tokens
                    })
                    temp_chunk = []
                    temp_tokens = 0
                
                temp_chunk.append(word)
                temp_tokens += word_tokens
            
            if temp_chunk:
                current_chunk = [' '.join(temp_chunk)]
                current_tokens = temp_tokens
                chunk_start_timestamp = segment['timestamp']
        
        # Check if adding this segment would exceed chunk size
        elif current_tokens + segment_tokens > CHUNK_SIZE and current_chunk:
            # Save current chunk
            chunks.append({
                'text': '\n\n'.join(current_chunk),
                'guest_name': guest_name,
                'start_timestamp': chunk_start_timestamp,
                'tokens': current_tokens
            })
            
            # Start new chunk with this segment
            current_chunk = [segment_text]
            current_tokens = segment_tokens
            chunk_start_timestamp = segment['timestamp']
        else:
            # Add to current chunk
            if not current_chunk:
                chunk_start_timestamp = segment['timestamp']
            current_chunk.append(segment_text)
            current_tokens += segment_tokens
    
    # Don't forget the last chunk
    if current_chunk:
        chunks.append({
            'text': '\n\n'.join(current_chunk),
            'guest_name': guest_name,
            'start_timestamp': chunk_start_timestamp,
            'tokens': current_tokens
        })
    
    return chunks

def generate_embeddings(texts: List[str]) -> List[List[float]]:
    """
    Generate embeddings using OpenAI's API.
    Processes in batches to handle rate limits.
    """
    batch_size = 100
    all_embeddings = []
    
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        print(f"  Generating embeddings for batch {i//batch_size + 1}/{(len(texts)-1)//batch_size + 1}")
        
        response = openai_client.embeddings.create(
            model="text-embedding-3-small",
            input=batch
        )
        
        batch_embeddings = [item.embedding for item in response.data]
        all_embeddings.extend(batch_embeddings)
    
    return all_embeddings

def main():
    """Main ingestion process."""
    print("=" * 60)
    print("Lenny's Podcast Transcript Ingestion")
    print("=" * 60)
    
    # Initialize Chroma client
    print("\n1. Initializing database...")
    client = chromadb.PersistentClient(path=DATABASE_DIR)
    
    # Create or get collection
    try:
        collection = client.get_collection(name="lenny_transcripts")
        print("   Found existing collection. Deleting to rebuild...")
        client.delete_collection(name="lenny_transcripts")
    except:
        pass
    
    collection = client.create_collection(
        name="lenny_transcripts",
        metadata={"description": "Lenny's Podcast transcripts with OpenAI embeddings"}
    )
    print("   Collection created successfully.")
    
    # Get all transcript files
    print("\n2. Finding transcript files...")
    transcript_files = list(Path(TRANSCRIPTS_DIR).glob("*.txt"))
    print(f"   Found {len(transcript_files)} transcript files.")
    
    if len(transcript_files) == 0:
        print("\n   ERROR: No transcript files found!")
        print(f"   Please ensure .txt files are in: {TRANSCRIPTS_DIR}")
        return
    
    # Process each transcript
    print("\n3. Processing transcripts...")
    all_chunks = []
    
    for idx, file_path in enumerate(transcript_files, 1):
        print(f"\n   [{idx}/{len(transcript_files)}] Processing: {file_path.name}")
        
        try:
            # Parse transcript
            guest_name, segments = parse_transcript(str(file_path))
            print(f"      Guest: {guest_name}")
            print(f"      Segments: {len(segments)}")
            
            # Create chunks
            chunks = create_chunks(segments, guest_name)
            print(f"      Chunks created: {len(chunks)}")
            
            # Add file reference to each chunk
            for chunk in chunks:
                chunk['file_name'] = file_path.name
            
            all_chunks.extend(chunks)
            
        except Exception as e:
            print(f"      ERROR processing {file_path.name}: {str(e)}")
            continue
    
    print(f"\n   Total chunks created: {len(all_chunks)}")
    
    # Generate embeddings
    print("\n4. Generating embeddings with OpenAI...")
    texts = [chunk['text'] for chunk in all_chunks]
    embeddings = generate_embeddings(texts)
    print(f"   Generated {len(embeddings)} embeddings.")
    
    # Store in database
    print("\n5. Storing in database...")
    ids = [f"chunk_{i}" for i in range(len(all_chunks))]
    metadatas = [
        {
            'guest_name': chunk['guest_name'],
            'timestamp': chunk['start_timestamp'],
            'file_name': chunk['file_name'],
            'tokens': chunk['tokens']
        }
        for chunk in all_chunks
    ]
    
    # Add to collection in batches
    batch_size = 500
    for i in range(0, len(all_chunks), batch_size):
        end_idx = min(i + batch_size, len(all_chunks))
        print(f"   Storing batch {i//batch_size + 1}/{(len(all_chunks)-1)//batch_size + 1}")
        
        collection.add(
            ids=ids[i:end_idx],
            embeddings=embeddings[i:end_idx],
            documents=texts[i:end_idx],
            metadatas=metadatas[i:end_idx]
        )
    
    print("\n" + "=" * 60)
    print("✓ Ingestion Complete!")
    print("=" * 60)
    print(f"Total episodes processed: {len(transcript_files)}")
    print(f"Total chunks stored: {len(all_chunks)}")
    print(f"Database location: {DATABASE_DIR}")
    print("\nYou can now proceed to build the MCP server!")

if __name__ == "__main__":
    main()
