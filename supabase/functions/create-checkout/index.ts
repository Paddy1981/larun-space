import { serve } from "https://deno.land/std@0.168.0/http/server.ts";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
};

// Lemon Squeezy Variant IDs - set these in Supabase secrets
// These are the product variant IDs from your Lemon Squeezy store
const LEMONSQUEEZY_VARIANTS: Record<string, string> = {
  researcher: Deno.env.get("LEMONSQUEEZY_VARIANT_RESEARCHER") || "",
  scientist: Deno.env.get("LEMONSQUEEZY_VARIANT_SCIENTIST") || "",
};

serve(async (req) => {
  // Handle CORS preflight
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }

  try {
    const apiKey = Deno.env.get("LEMONSQUEEZY_API_KEY");
    const storeId = Deno.env.get("LEMONSQUEEZY_STORE_ID");

    if (!apiKey) {
      return new Response(
        JSON.stringify({
          error: "Payment system not configured",
          message: "Payment processing is being set up. Please try again later or contact support@larun.space"
        }),
        { status: 503, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    const { tier, user_id, user_email, success_url, cancel_url } = await req.json();

    if (!tier || !LEMONSQUEEZY_VARIANTS[tier]) {
      return new Response(
        JSON.stringify({ error: "Invalid subscription tier" }),
        { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    const variantId = LEMONSQUEEZY_VARIANTS[tier];

    if (!variantId) {
      return new Response(
        JSON.stringify({ error: "Product variant not configured for this tier" }),
        { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    // Create Lemon Squeezy Checkout
    const lsResponse = await fetch("https://api.lemonsqueezy.com/v1/checkouts", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${apiKey}`,
        "Content-Type": "application/vnd.api+json",
        "Accept": "application/vnd.api+json",
      },
      body: JSON.stringify({
        data: {
          type: "checkouts",
          attributes: {
            checkout_data: {
              email: user_email || "",
              custom: {
                user_id: user_id || "",
                tier: tier
              }
            },
            checkout_options: {
              embed: false,
              media: true,
              button_color: "#7c3aed"
            },
            product_options: {
              redirect_url: success_url || "https://larun.space/app.html?subscription=success",
              receipt_button_text: "Go to Dashboard",
              receipt_link_url: "https://larun.space/app.html"
            }
          },
          relationships: {
            store: {
              data: {
                type: "stores",
                id: storeId || ""
              }
            },
            variant: {
              data: {
                type: "variants",
                id: variantId
              }
            }
          }
        }
      }),
    });

    if (!lsResponse.ok) {
      const error = await lsResponse.text();
      console.error("Lemon Squeezy API error:", error);
      return new Response(
        JSON.stringify({ error: "Failed to create checkout session" }),
        { status: 500, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    const checkoutData = await lsResponse.json();
    const checkoutUrl = checkoutData.data?.attributes?.url;

    if (!checkoutUrl) {
      return new Response(
        JSON.stringify({ error: "No checkout URL returned" }),
        { status: 500, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    return new Response(
      JSON.stringify({
        url: checkoutUrl,
        checkout_id: checkoutData.data?.id
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
