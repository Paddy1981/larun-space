// LARUN API - Supabase Edge Function
// Deploy this at: https://supabase.com/dashboard/project/mwmbcfcvnkwegrjlauis/functions

import { serve } from "https://deno.land/std@0.168.0/http/server.ts"
import { createClient } from "https://esm.sh/@supabase/supabase-js@2"

const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
}

serve(async (req) => {
  // Handle CORS preflight
  if (req.method === 'OPTIONS') {
    return new Response('ok', { headers: corsHeaders })
  }

  const url = new URL(req.url)
  const path = url.pathname.replace('/api/v1', '').replace('/v1', '')

  try {
    // Initialize Supabase client
    const supabaseUrl = Deno.env.get('SUPABASE_URL')!
    const supabaseKey = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!
    const supabase = createClient(supabaseUrl, supabaseKey)

    // Get API key from header
    const apiKey = req.headers.get('x-api-key') || req.headers.get('Authorization')?.replace('Bearer ', '')

    // Routes
    if (path === '' || path === '/') {
      return jsonResponse({
        name: 'LARUN API',
        version: '1.0.0',
        status: 'operational',
        endpoints: {
          '/health': 'Health check',
          '/analyze': 'Analyze a target for exoplanets (POST)',
          '/targets': 'List available targets (GET)',
          '/user': 'Get user info (requires auth)',
        }
      })
    }

    if (path === '/health') {
      return jsonResponse({ status: 'healthy', timestamp: new Date().toISOString() })
    }

    // Protected routes - require API key
    if (apiKey) {
      const user = await validateApiKey(supabase, apiKey)

      if (!user) {
        return jsonResponse({ error: 'Invalid API key' }, 401)
      }

      if (path === '/user') {
        return jsonResponse({
          id: user.user_id,
          tier: user.tier,
          authenticated: true
        })
      }

      if (path === '/analyze' && req.method === 'POST') {
        const body = await req.json()
        const target = body.target || body.tic_id

        if (!target) {
          return jsonResponse({ error: 'Missing target parameter' }, 400)
        }

        // Log usage
        await supabase.from('usage_logs').insert({
          user_id: user.user_id,
          action: 'analyze',
          target_name: target,
          metadata: { tier: user.tier }
        })

        // Simulated analysis result (replace with actual LARUN analysis)
        const result = await analyzeTarget(target)
        return jsonResponse(result)
      }

      if (path === '/targets') {
        return jsonResponse({
          popular: [
            { id: 'TIC307210830', name: 'TOI-700', type: 'TESS' },
            { id: 'TIC260128333', name: 'TOI-849', type: 'TESS' },
            { id: 'KIC11904151', name: 'Kepler-90', type: 'Kepler' },
            { id: 'KIC10227020', name: 'Kepler-22', type: 'Kepler' },
          ]
        })
      }
    }

    // Public routes
    if (path === '/targets') {
      return jsonResponse({
        message: 'API key required for full access',
        sample: [
          { id: 'TIC307210830', name: 'TOI-700' }
        ]
      })
    }

    return jsonResponse({ error: 'Not found', path }, 404)

  } catch (error) {
    console.error('API Error:', error)
    return jsonResponse({ error: 'Internal server error' }, 500)
  }
})

// Helper: JSON response
function jsonResponse(data: any, status = 200) {
  return new Response(JSON.stringify(data, null, 2), {
    status,
    headers: { ...corsHeaders, 'Content-Type': 'application/json' }
  })
}

// Helper: Validate API key
async function validateApiKey(supabase: any, apiKey: string) {
  // Hash the API key
  const encoder = new TextEncoder()
  const data = encoder.encode(apiKey)
  const hashBuffer = await crypto.subtle.digest('SHA-256', data)
  const hashArray = Array.from(new Uint8Array(hashBuffer))
  const keyHash = hashArray.map(b => b.toString(16).padStart(2, '0')).join('')

  // Look up in database
  const { data: result } = await supabase
    .rpc('validate_api_key', { key_hash_input: keyHash })
    .single()

  return result
}

// Helper: Analyze target (simulated - replace with actual LARUN logic)
async function analyzeTarget(target: string) {
  // This would call your actual LARUN analysis
  // For now, return simulated results

  const isTIC = target.toUpperCase().startsWith('TIC')
  const id = target.replace(/[^0-9]/g, '')

  return {
    target: target,
    type: isTIC ? 'TESS' : 'Kepler',
    analysis: {
      status: 'complete',
      timestamp: new Date().toISOString(),
      star: {
        magnitude: 10 + Math.random() * 3,
        temperature: 5000 + Math.random() * 1500,
        type: 'G-type'
      },
      transit_candidates: [
        {
          period_days: 3 + Math.random() * 10,
          depth_ppm: 500 + Math.random() * 1000,
          confidence: 0.8 + Math.random() * 0.19,
          snr: 5 + Math.random() * 10
        }
      ],
      bls_peak: {
        period: 3.42,
        power: 0.85,
        t0: 2459000.5
      }
    },
    credits_used: 1
  }
}
