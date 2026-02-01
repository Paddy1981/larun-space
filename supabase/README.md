# LARUN.SPACE Supabase Edge Functions

This directory contains Supabase Edge Functions for the LARUN.SPACE backend.

## Setup

### 1. Install Supabase CLI

```bash
npm install -g supabase
```

### 2. Login to Supabase

```bash
supabase login
```

### 3. Link to your project

```bash
cd /path/to/larun-space
supabase link --project-ref mwmbcfcvnkwegrjlauis
```

### 4. Set OpenAI API Key (optional but recommended)

```bash
supabase secrets set OPENAI_API_KEY=sk-your-openai-api-key
```

Without the OpenAI key, the chat function will use simulated responses.

### 5. Deploy the Edge Functions

```bash
supabase functions deploy chat
```

## Available Functions

### chat

AI-powered chat endpoint for exoplanet analysis.

**Endpoint:** `https://mwmbcfcvnkwegrjlauis.supabase.co/functions/v1/chat`

**Method:** POST

**Request Body:**
```json
{
  "message": "Search for transits in TIC 307210830",
  "conversation_id": "optional-conversation-id"
}
```

**Response:**
```json
{
  "response": "AI response text...",
  "conversation_id": "conversation-id"
}
```

## Testing Locally

```bash
supabase functions serve chat --env-file .env.local
```

Create `.env.local` with:
```
OPENAI_API_KEY=sk-your-key
```
