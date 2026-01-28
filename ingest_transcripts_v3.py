#!/usr/bin/env python3
"""
Lenny's Podcast Transcript Ingestion Script - V3 (Enhanced Accuracy)
Adds speaker-level metadata and improved chunking for better search accuracy
"""

import os
import re
from pathlib import Path
from typing import List, Dict, Tuple
import chromadb
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
DATABASE_DIR = "/Users/aryan/Documents/lenny-mcp-server/database"
CHUNK_SIZE = 600  # Reduced from 800 for better precision
OVERLAP = 150     # Increased overlap for better context

def count_tokens(text: str) -> int:
    """Count the number of tokens in a text string."""
    return len(encoding.encode(text))

def normalize_timestamp(timestamp: str) -> str:
    """
    Normalize timestamps to HH:MM:SS format.
    Handles both HH:MM:SS and MM:SS formats.
    """
    parts = timestamp.split(':')
    if len(parts) == 2:
        return f"00:{parts[0].zfill(2)}:{parts[1].zfill(2)}"
    elif len(parts) == 3:
        return f"{parts[0].zfill(2)}:{parts[1].zfill(2)}:{parts[2].zfill(2)}"
    else:
        return timestamp

def identify_host_name(segments: List[Dict]) -> str:
    """
    Identify the host name (usually 'Lenny' or variations).
    Returns the most common speaker name that appears to be the host.
    """
    speaker_counts = {}
    for segment in segments:
        speaker = segment['speaker']
        speaker_counts[speaker] = speaker_counts.get(speaker, 0) + 1

    # Host typically speaks most often
    # Also check for common host names
    host_candidates = [s for s in speaker_counts.keys() if 'lenny' in s.lower()]
    if host_candidates:
        return host_candidates[0]

    # Otherwise, return the most frequent speaker (likely the host)
    return max(speaker_counts.items(), key=lambda x: x[1])[0]

def analyze_chunk_speakers(chunk_segments: List[Dict], host_name: str) -> Dict:
    """
    Analyze speaker distribution in a chunk.
    Returns metadata about who spoke and how much.
    """
    speaker_tokens = {}
    total_tokens = 0

    for segment in chunk_segments:
        speaker = segment['speaker']
        segment_text = f"{speaker}: {segment['text']}"
        tokens = count_tokens(segment_text)

        speaker_tokens[speaker] = speaker_tokens.get(speaker, 0) + tokens
        total_tokens += tokens

    # Calculate percentages
    host_tokens = speaker_tokens.get(host_name, 0)
    guest_tokens = total_tokens - host_tokens

    host_percentage = (host_tokens / total_tokens * 100) if total_tokens > 0 else 0
    guest_percentage = (guest_tokens / total_tokens * 100) if total_tokens > 0 else 0

    # Categorize chunk
    if guest_percentage >= 70:
        category = "guest_heavy"
    elif host_percentage >= 70:
        category = "host_heavy"
    else:
        category = "mixed"

    # Get primary speaker
    primary_speaker = max(speaker_tokens.items(), key=lambda x: x[1])[0] if speaker_tokens else "unknown"

    return {
        'host_percentage': round(host_percentage, 1),
        'guest_percentage': round(guest_percentage, 1),
        'speaker_category': category,
        'primary_speaker': primary_speaker,
        'num_speakers': len(speaker_tokens),
        'speakers': list(speaker_tokens.keys())
    }

def parse_transcript(file_path: str) -> Tuple[str, List[Dict]]:
    """
    Parse a transcript file and extract structured data.
    Handles multiple formats.

    Returns:
        Tuple of (guest_name, list of parsed segments)
    """
    guest_name = Path(file_path).stem

    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Define multiple patterns
    patterns = [
        r'^([^(\[]+?)\s*\((\d{1,2}:\d{2}(?::\d{2})?)\):',
        r'^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*([^:]+):',
    ]

    segments = []
    lines = content.split('\n')
    current_speaker = None
    current_timestamp = None
    current_text = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        matched = False
        for pattern_idx, pattern in enumerate(patterns):
            match = re.match(pattern, line)
            if match:
                if current_speaker and current_text:
                    segments.append({
                        'speaker': current_speaker.strip(),
                        'timestamp': normalize_timestamp(current_timestamp),
                        'text': ' '.join(current_text)
                    })

                if pattern_idx == 0:
                    current_speaker = match.group(1)
                    current_timestamp = match.group(2)
                else:
                    current_timestamp = match.group(1)
                    current_speaker = match.group(2)

                current_text = []
                matched = True
                break

        if not matched and current_speaker:
            current_text.append(line)

    if current_speaker and current_text:
        segments.append({
            'speaker': current_speaker.strip(),
            'timestamp': normalize_timestamp(current_timestamp),
            'text': ' '.join(current_text)
        })

    return guest_name, segments

def create_chunks_with_speaker_metadata(segments: List[Dict], guest_name: str) -> List[Dict]:
    """
    Create chunks with enhanced speaker metadata for better filtering.
    """
    if not segments:
        return []

    # Identify host
    host_name = identify_host_name(segments)

    chunks = []
    current_chunk_segments = []
    current_chunk_text = []
    current_tokens = 0
    chunk_start_timestamp = None

    for segment in segments:
        segment_text = f"{segment['speaker']}: {segment['text']}"
        segment_tokens = count_tokens(segment_text)

        # If this segment alone is larger than chunk size, split it
        if segment_tokens > CHUNK_SIZE:
            # Save current chunk if it exists
            if current_chunk_segments:
                speaker_metadata = analyze_chunk_speakers(current_chunk_segments, host_name)
                chunks.append({
                    'text': '\n\n'.join(current_chunk_text),
                    'guest_name': guest_name,
                    'start_timestamp': chunk_start_timestamp,
                    'tokens': current_tokens,
                    **speaker_metadata
                })
                current_chunk_segments = []
                current_chunk_text = []
                current_tokens = 0

            # Split large segment into multiple chunks
            words = segment_text.split()
            temp_chunk = []
            temp_tokens = 0

            for word in words:
                word_tokens = count_tokens(word + " ")
                if temp_tokens + word_tokens > CHUNK_SIZE and temp_chunk:
                    # Create a temporary segment dict for speaker analysis
                    temp_segment = {
                        'speaker': segment['speaker'],
                        'text': ' '.join(temp_chunk).replace(f"{segment['speaker']}: ", "")
                    }
                    speaker_metadata = analyze_chunk_speakers([temp_segment], host_name)

                    chunks.append({
                        'text': ' '.join(temp_chunk),
                        'guest_name': guest_name,
                        'start_timestamp': segment['timestamp'],
                        'tokens': temp_tokens,
                        **speaker_metadata
                    })
                    temp_chunk = []
                    temp_tokens = 0

                temp_chunk.append(word)
                temp_tokens += word_tokens

            if temp_chunk:
                current_chunk_text = [' '.join(temp_chunk)]
                current_tokens = temp_tokens
                chunk_start_timestamp = segment['timestamp']
                current_chunk_segments = [{
                    'speaker': segment['speaker'],
                    'text': ' '.join(temp_chunk).replace(f"{segment['speaker']}: ", "")
                }]

        # Check if adding this segment would exceed chunk size
        elif current_tokens + segment_tokens > CHUNK_SIZE and current_chunk_segments:
            # Save current chunk with speaker metadata
            speaker_metadata = analyze_chunk_speakers(current_chunk_segments, host_name)
            chunks.append({
                'text': '\n\n'.join(current_chunk_text),
                'guest_name': guest_name,
                'start_timestamp': chunk_start_timestamp,
                'tokens': current_tokens,
                **speaker_metadata
            })

            # Start new chunk with this segment
            current_chunk_segments = [segment]
            current_chunk_text = [segment_text]
            current_tokens = segment_tokens
            chunk_start_timestamp = segment['timestamp']
        else:
            # Add to current chunk
            if not current_chunk_segments:
                chunk_start_timestamp = segment['timestamp']
            current_chunk_segments.append(segment)
            current_chunk_text.append(segment_text)
            current_tokens += segment_tokens

    # Don't forget the last chunk
    if current_chunk_segments:
        speaker_metadata = analyze_chunk_speakers(current_chunk_segments, host_name)
        chunks.append({
            'text': '\n\n'.join(current_chunk_text),
            'guest_name': guest_name,
            'start_timestamp': chunk_start_timestamp,
            'tokens': current_tokens,
            **speaker_metadata
        })

    return chunks

def generate_embeddings(texts: List[str]) -> List[List[float]]:
    """Generate embeddings using OpenAI's API."""
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
    print("Lenny's Podcast Transcript Ingestion - V3 (Enhanced)")
    print("=" * 60)

    # Initialize Chroma client
    print("\n1. Initializing database...")
    client = chromadb.PersistentClient(path=DATABASE_DIR)

    # Create new collection with updated name
    collection_name = "lenny_transcripts_v3"
    try:
        collection = client.get_collection(name=collection_name)
        print(f"   Found existing collection '{collection_name}'. Deleting to rebuild...")
        client.delete_collection(name=collection_name)
    except:
        pass

    collection = client.create_collection(
        name=collection_name,
        metadata={"description": "Lenny's Podcast transcripts with speaker metadata and improved chunking"}
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
    print("\n3. Processing transcripts with speaker analysis...")
    all_chunks = []
    success_count = 0
    fail_count = 0

    for idx, file_path in enumerate(transcript_files, 1):
        print(f"\n   [{idx}/{len(transcript_files)}] Processing: {file_path.name}")

        try:
            guest_name, segments = parse_transcript(str(file_path))
            print(f"      Guest: {guest_name}")
            print(f"      Segments: {len(segments)}")

            if len(segments) == 0:
                print(f"      ⚠️  WARNING: No segments parsed! Skipping.")
                fail_count += 1
                continue

            # Create chunks with speaker metadata
            chunks = create_chunks_with_speaker_metadata(segments, guest_name)
            print(f"      Chunks created: {len(chunks)}")

            # Add file reference to each chunk
            for chunk in chunks:
                chunk['file_name'] = file_path.name

            all_chunks.extend(chunks)
            success_count += 1

        except Exception as e:
            print(f"      ❌ ERROR processing {file_path.name}: {str(e)}")
            fail_count += 1
            continue

    print(f"\n   Total chunks created: {len(all_chunks)}")
    print(f"   Success: {success_count} files")
    print(f"   Failed: {fail_count} files")

    if len(all_chunks) == 0:
        print("\n   No chunks to process. Exiting.")
        return

    # Generate embeddings
    print("\n4. Generating embeddings with OpenAI...")
    texts = [chunk['text'] for chunk in all_chunks]
    embeddings = generate_embeddings(texts)
    print(f"   Generated {len(embeddings)} embeddings.")

    # Store in database
    print("\n5. Storing in database with enhanced metadata...")

    ids = [f"chunk_{i}" for i in range(len(all_chunks))]
    metadatas = [
        {
            'guest_name': chunk['guest_name'],
            'timestamp': chunk['start_timestamp'],
            'file_name': chunk['file_name'],
            'tokens': chunk['tokens'],
            'speaker_category': chunk['speaker_category'],
            'primary_speaker': chunk['primary_speaker'],
            'guest_percentage': chunk['guest_percentage'],
            'host_percentage': chunk['host_percentage'],
            'num_speakers': chunk['num_speakers']
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

    # Print statistics
    print("\n" + "=" * 60)
    print("✓ Ingestion Complete!")
    print("=" * 60)
    print(f"Episodes processed: {len(transcript_files)}")
    print(f"Successful: {success_count}")
    print(f"Failed: {fail_count}")
    print(f"Total chunks stored: {len(all_chunks)}")
    print(f"Database location: {DATABASE_DIR}")
    print(f"Collection name: {collection_name}")

    # Speaker category statistics
    categories = {}
    for chunk in all_chunks:
        cat = chunk['speaker_category']
        categories[cat] = categories.get(cat, 0) + 1

    print(f"\n📊 Speaker Distribution:")
    print(f"   Guest-heavy chunks (70%+ guest): {categories.get('guest_heavy', 0)}")
    print(f"   Host-heavy chunks (70%+ host): {categories.get('host_heavy', 0)}")
    print(f"   Mixed chunks: {categories.get('mixed', 0)}")

    if fail_count > 0:
        print(f"\n⚠️  {fail_count} files failed. These might need manual inspection.")

if __name__ == "__main__":
    main()
