#!/bin/bash
# Deploy LARUN.SPACE Supabase Edge Functions

set -e

echo "🚀 Deploying LARUN.SPACE Edge Functions..."

# Check if supabase CLI is installed
if ! command -v supabase &> /dev/null; then
    echo "❌ Supabase CLI not found. Install with: npm install -g supabase"
    exit 1
fi

# Deploy chat function
echo "📦 Deploying chat function..."
supabase functions deploy chat --project-ref mwmbcfcvnkwegrjlauis

echo "✅ Deployment complete!"
echo ""
echo "Your API is available at:"
echo "https://mwmbcfcvnkwegrjlauis.supabase.co/functions/v1/chat"
echo ""
echo "To enable AI responses, set your OpenAI API key:"
echo "supabase secrets set OPENAI_API_KEY=sk-your-key --project-ref mwmbcfcvnkwegrjlauis"
