import { serve } from "https://deno.land/std@0.168.0/http/server.ts";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
};

// Stripe Price IDs - set these in Supabase secrets
const STRIPE_PRICES: Record<string, string> = {
  researcher: Deno.env.get("STRIPE_PRICE_RESEARCHER") || "",
  scientist: Deno.env.get("STRIPE_PRICE_SCIENTIST") || "",
};

serve(async (req) => {
  // Handle CORS preflight
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }

  try {
    const stripeKey = Deno.env.get("STRIPE_SECRET_KEY");

    if (!stripeKey) {
      // Stripe not configured - return helpful message
      return new Response(
        JSON.stringify({
          error: "Stripe not configured",
          message: "Payment processing is being set up. Please try again later or contact support@larun.space"
        }),
        { status: 503, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    const { tier, user_id, user_email, success_url, cancel_url } = await req.json();

    if (!tier || !STRIPE_PRICES[tier]) {
      return new Response(
        JSON.stringify({ error: "Invalid subscription tier" }),
        { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    const priceId = STRIPE_PRICES[tier];

    if (!priceId) {
      return new Response(
        JSON.stringify({ error: "Price not configured for this tier" }),
        { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    // Create Stripe Checkout Session
    const stripeResponse = await fetch("https://api.stripe.com/v1/checkout/sessions", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${stripeKey}`,
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: new URLSearchParams({
        "mode": "subscription",
        "payment_method_types[0]": "card",
        "line_items[0][price]": priceId,
        "line_items[0][quantity]": "1",
        "success_url": success_url || "https://larun.space/app.html?subscription=success",
        "cancel_url": cancel_url || "https://larun.space/pricing.html?subscription=cancelled",
        "customer_email": user_email || "",
        "client_reference_id": user_id || "",
        "metadata[user_id]": user_id || "",
        "metadata[tier]": tier,
      }),
    });

    if (!stripeResponse.ok) {
      const error = await stripeResponse.text();
      console.error("Stripe API error:", error);
      return new Response(
        JSON.stringify({ error: "Failed to create checkout session" }),
        { status: 500, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    const session = await stripeResponse.json();

    return new Response(
      JSON.stringify({
        url: session.url,
        session_id: session.id
      }),
      { headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );

  } catch (error) {
    console.error("Error:", error);
    return new Response(
      JSON.stringify({ error: error.message }),
      { status: 500, headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );
  }
});
