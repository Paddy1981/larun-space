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
echo "To enable Lemon Squeezy subscriptions, set your keys:"
echo "supabase secrets set LEMONSQUEEZY_API_KEY=your-api-key --project-ref mwmbcfcvnkwegrjlauis"
echo "supabase secrets set LEMONSQUEEZY_STORE_ID=your-store-id --project-ref mwmbcfcvnkwegrjlauis"
echo "supabase secrets set LEMONSQUEEZY_VARIANT_RESEARCHER=variant-id --project-ref mwmbcfcvnkwegrjlauis"
echo "supabase secrets set LEMONSQUEEZY_VARIANT_SCIENTIST=variant-id --project-ref mwmbcfcvnkwegrjlauis"
