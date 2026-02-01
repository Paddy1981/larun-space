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

### 4. Set Gemini API Key (optional but recommended)

**Recommended: Get your API key from Google AI Studio**

1. Go to [Google AI Studio](https://aistudio.google.com/)
2. Sign in with your Google account
3. Click "Get API Key" in the top right
4. Click "Create API Key" and select a project (or create new)
5. Copy the generated API key

```bash
supabase secrets set GEMINI_API_KEY=your-gemini-api-key --project-ref mwmbcfcvnkwegrjlauis
```

Without the Gemini key, the chat function will use simulated responses that still provide helpful exoplanet analysis guidance.

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
GEMINI_API_KEY=your-gemini-key
```
