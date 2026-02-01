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

### 5. Set Stripe Keys (for subscriptions)

1. Create a [Stripe account](https://stripe.com) if you don't have one
2. Get your API keys from the Stripe Dashboard
3. Create products and prices for Researcher ($9/mo) and Scientist ($29/mo) plans
4. Set the secrets:

```bash
supabase secrets set STRIPE_SECRET_KEY=sk_live_... --project-ref mwmbcfcvnkwegrjlauis
supabase secrets set STRIPE_PRICE_RESEARCHER=price_... --project-ref mwmbcfcvnkwegrjlauis
supabase secrets set STRIPE_PRICE_SCIENTIST=price_... --project-ref mwmbcfcvnkwegrjlauis
```

### 6. Deploy the Edge Functions

```bash
supabase functions deploy chat
supabase functions deploy create-checkout
```

Or use the deploy script:
```bash
./supabase/deploy.sh
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

### create-checkout

Creates a Stripe Checkout session for subscription payments.

**Endpoint:** `https://mwmbcfcvnkwegrjlauis.supabase.co/functions/v1/create-checkout`

**Method:** POST

**Headers:**
- `Authorization: Bearer <user_access_token>`

**Request Body:**
```json
{
  "tier": "researcher",
  "user_id": "user-uuid",
  "user_email": "user@example.com",
  "success_url": "https://larun.space/app.html?subscription=success",
  "cancel_url": "https://larun.space/pricing.html?subscription=cancelled"
}
```

**Response:**
```json
{
  "url": "https://checkout.stripe.com/...",
  "session_id": "cs_..."
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
