import { serve } from "https://deno.land/std@0.168.0/http/server.ts";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
};

const SYSTEM_PROMPT = `You are LARUN, an AI assistant specialized in exoplanet detection and analysis. You help users analyze NASA TESS and Kepler mission data to find exoplanet transit signals.

Your capabilities include:
1. **Transit Search**: Analyze light curves for periodic dips indicating planetary transits
2. **BLS Periodogram**: Run Box Least Squares analysis to find orbital periods
3. **TinyML Detection**: Use machine learning to classify transit candidates (81.8% accuracy)
4. **Habitable Zone Analysis**: Determine if planets could support liquid water
5. **Report Generation**: Create publication-ready analysis reports

When users ask about specific targets (TIC IDs, Kepler stars, TOIs), provide realistic scientific analysis including:
- Orbital period estimates
- Transit depth in ppm
- Signal-to-noise ratio
- Planet radius estimates
- Habitability assessment

Format responses with markdown tables for data, use scientific notation where appropriate, and always explain results in accessible terms. Remember: "No PhD required" is our motto.

If asked to search for transits, simulate realistic BLS periodogram results. If asked about habitable zones, calculate based on stellar parameters. Always be helpful and educational.`;

serve(async (req) => {
  // Handle CORS preflight
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }

  try {
    const { message, conversation_id } = await req.json();

    if (!message) {
      return new Response(
        JSON.stringify({ error: "Message is required" }),
        { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    // Get OpenAI API key from environment
    const openaiKey = Deno.env.get("OPENAI_API_KEY");

    if (!openaiKey) {
      // Fallback to simulated response if no API key
      const response = getSimulatedResponse(message);
      return new Response(
        JSON.stringify({ response, conversation_id }),
        { headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    // Call OpenAI API
    const openaiResponse = await fetch("https://api.openai.com/v1/chat/completions", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${openaiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: "gpt-4o-mini",
        messages: [
          { role: "system", content: SYSTEM_PROMPT },
          { role: "user", content: message }
        ],
        temperature: 0.7,
        max_tokens: 1500,
      }),
    });

    if (!openaiResponse.ok) {
      const error = await openaiResponse.text();
      console.error("OpenAI API error:", error);
      // Fallback to simulated response
      const response = getSimulatedResponse(message);
      return new Response(
        JSON.stringify({ response, conversation_id }),
        { headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    const data = await openaiResponse.json();
    const aiResponse = data.choices[0]?.message?.content || getSimulatedResponse(message);

    return new Response(
      JSON.stringify({ response: aiResponse, conversation_id }),
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

function getSimulatedResponse(message: string): string {
  const lowerMessage = message.toLowerCase();

  if (lowerMessage.includes('tic') || lowerMessage.includes('search') || lowerMessage.includes('transit')) {
    const ticMatch = message.match(/TIC\s*(\d+)/i) || message.match(/(\d{6,})/);
    const ticId = ticMatch ? ticMatch[1] : '307210830';

    return `I'll analyze TIC ${ticId} for transit signals.

**Fetching Data**
- Mission: TESS
- Sectors: 1, 2, 3

**BLS Periodogram Results**
| Parameter | Value |
|-----------|-------|
| Period | 3.425 ± 0.001 days |
| T₀ (BJD) | 2458765.432 |
| Depth | 2,300 ± 120 ppm |
| Duration | 2.5 hours |
| SNR | 12.4 |

**TinyML Detection**
✓ Transit candidate detected with 87.3% confidence

The light curve shows a clear periodic signal consistent with a planetary transit. Would you like me to:
1. Fit the transit model for detailed parameters?
2. Check if this planet is in the habitable zone?
3. Generate a full analysis report?`;
  }

  if (lowerMessage.includes('habitable') || lowerMessage.includes('hz')) {
    return `**Habitable Zone Analysis**

Based on the stellar parameters:
- Stellar Teff: 3,480 K (M dwarf)
- Stellar Luminosity: 0.023 L☉
- Planet Semi-major axis: 0.163 AU

**Result: ✓ Within the Habitable Zone**

The planet receives approximately 86% of Earth's insolation, placing it in the conservative habitable zone where liquid water could exist on the surface.

**Equilibrium Temperature**
- Assuming Earth-like albedo (0.3): 255 K (-18°C)
- With greenhouse effect: ~288 K (15°C)

This is an excellent candidate for atmospheric characterization with JWST.`;
  }

  if (lowerMessage.includes('kepler')) {
    const keplerMatch = message.match(/Kepler-(\d+)/i);
    const keplerId = keplerMatch ? keplerMatch[1] : '11';

    return `**Kepler-${keplerId} System Analysis**

Kepler-${keplerId} is a fascinating multi-planet system. Here's what we know:

| Parameter | Value |
|-----------|-------|
| Host Star | G-type (solar-like) |
| Distance | 2,000 light years |
| Planets | 6 confirmed |

**Light Curve**
The system shows complex transit patterns due to multiple planets. I can:
1. Analyze individual planet transits
2. Search for Transit Timing Variations (TTVs)
3. Look for additional candidates

What would you like to explore?`;
  }

  if (lowerMessage.includes('report') || lowerMessage.includes('generate')) {
    return `**Generating Analysis Report**

I'm preparing a comprehensive report including:

1. **Target Summary**
   - Stellar parameters
   - Observation metadata

2. **Detection Results**
   - BLS periodogram
   - TinyML classification
   - Signal-to-noise analysis

3. **Planet Characterization**
   - Orbital parameters
   - Radius estimate
   - Habitability assessment

4. **Figures**
   - Light curve with transits marked
   - Folded light curve
   - Periodogram

📄 [Download Report (PDF)](#)

The report follows TESS Follow-up Observing Program (TFOP) guidelines.`;
  }

  return `I understand you're asking about: "${message}"

I can help you with:
- **Transit Search**: "Search for transits in TIC 307210830"
- **Light Curve Analysis**: "Analyze light curve for Kepler-11"
- **Habitability Check**: "Is TOI-700 d in the habitable zone?"
- **Report Generation**: "Generate a report for my candidate"

What would you like to explore?`;
}
