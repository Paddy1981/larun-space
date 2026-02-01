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

# Deploy checkout function
echo "📦 Deploying create-checkout function..."
supabase functions deploy create-checkout --project-ref mwmbcfcvnkwegrjlauis

echo "✅ Deployment complete!"
echo ""
echo "Your APIs are available at:"
echo "  - https://mwmbcfcvnkwegrjlauis.supabase.co/functions/v1/chat"
echo "  - https://mwmbcfcvnkwegrjlauis.supabase.co/functions/v1/create-checkout"
echo ""
echo "To enable AI responses, set your Gemini API key:"
echo "supabase secrets set GEMINI_API_KEY=your-gemini-key --project-ref mwmbcfcvnkwegrjlauis"
echo ""
echo "To enable Stripe subscriptions, set your Stripe keys:"
echo "supabase secrets set STRIPE_SECRET_KEY=sk_... --project-ref mwmbcfcvnkwegrjlauis"
echo "supabase secrets set STRIPE_PRICE_RESEARCHER=price_... --project-ref mwmbcfcvnkwegrjlauis"
echo "supabase secrets set STRIPE_PRICE_SCIENTIST=price_... --project-ref mwmbcfcvnkwegrjlauis"
