const http = require("node:http");

const PORT = Number(process.env.PORT || 3000);
const OPENAI_API_KEY = process.env.OPENAI_API_KEY;
const OPENAI_MODEL = process.env.OPENAI_MODEL || "gpt-4.1-mini";
const ALLOWED_ORIGIN = process.env.ALLOWED_ORIGIN || "";

function send(res, status, data) {
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff"
  });
  res.end(JSON.stringify(data));
}

const server = http.createServer(async (req, res) => {
  const origin = req.headers.origin || "";
  if (ALLOWED_ORIGIN && origin && origin !== ALLOWED_ORIGIN) {
    return send(res, 403, { error: "Origin not allowed." });
  }
  if (req.method === "OPTIONS") {
    res.writeHead(204, {
      "Access-Control-Allow-Origin": ALLOWED_ORIGIN || origin,
      "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
      "Vary": "Origin"
    });
    return res.end();
  }
  if (origin) {
    res.setHeader("Access-Control-Allow-Origin", ALLOWED_ORIGIN || origin);
    res.setHeader("Vary", "Origin");
  }
  if (req.method === "GET" && req.url === "/health") {
    return send(res, 200, { status: "online", aiConfigured: Boolean(OPENAI_API_KEY) });
  }
  if (req.method !== "POST" || req.url !== "/api/chat") {
    return send(res, 404, { error: "Not found." });
  }
  if (!OPENAI_API_KEY) return send(res, 503, { error: "JARVIS AI is not configured yet. Add OPENAI_API_KEY to the hosting environment." });

  let raw = "";
  for await (const chunk of req) {
    raw += chunk;
    if (raw.length > 20000) return send(res, 413, { error: "Message is too large." });
  }
  let body;
  try { body = JSON.parse(raw || "{}"); }
  catch { return send(res, 400, { error: "Invalid JSON." }); }
  const message = typeof body.message === "string" ? body.message.trim() : "";
  if (!message || message.length > 4000) return send(res, 400, { error: "Enter a message under 4,000 characters." });

  try {
    const upstream = await fetch("https://api.openai.com/v1/responses", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${OPENAI_API_KEY}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        model: OPENAI_MODEL,
        instructions: "You are MELYVN JARVIS, a helpful, professional personal AI assistant. Address the user respectfully as Sir. Be clear, practical, and concise. Do not claim to control the user's computer, access accounts, send messages, or perform actions unless a connected tool actually confirms it.",
        input: message,
        max_output_tokens: 700
      })
    });
    const result = await upstream.json();
    if (!upstream.ok) {
      console.error("OpenAI request failed:", upstream.status, result.error?.type || "unknown");
      return send(res, 502, { error: "The AI service could not complete that request. Check the server API key, billing, and model access." });
    }
    const answer = (result.output || [])
      .flatMap(item => item.content || [])
      .filter(item => item.type === "output_text")
      .map(item => item.text)
      .join("\n")
      .trim();
    if (!answer) return send(res, 502, { error: "The AI returned an empty response. Please try again." });
    return send(res, 200, { reply: answer });
  } catch (error) {
    console.error("AI connection error:", error.message);
    return send(res, 500, { error: "JARVIS could not reach the AI service. Please try again." });
  }
});

server.listen(PORT, () => console.log(`MELYVN JARVIS API listening on port ${PORT}`));
