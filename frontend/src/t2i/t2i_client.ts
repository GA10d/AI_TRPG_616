export type StoryImageTrigger = "scene_shift" | "npc_intro" | "manual" | "character_portrait";

export interface T2ICharacterRef {
  name: string;
  appearance: string;
  portraitUrl?: string;
}

export interface StoryImageRequest {
  prompt: string;
  trigger: StoryImageTrigger;
  theme: "parchment" | "nightwatch" | "neon";
  sceneId: string;
  characters?: T2ICharacterRef[];
  allowFallback?: boolean;
}

export interface StoryImageResult {
  imageUrl: string;
  revisedPrompt: string;
  provider: string;
  cached: boolean;
}

const CACHE_KEY = "trpg-t2i-cache-v1";
const memoryCache = new Map<string, StoryImageResult>();

function loadCacheFromStorage() {
  const raw = localStorage.getItem(CACHE_KEY);
  if (!raw) return;
  try {
    const parsed = JSON.parse(raw) as Record<string, StoryImageResult>;
    Object.entries(parsed).forEach(([k, v]) => memoryCache.set(k, v));
  } catch {
    localStorage.removeItem(CACHE_KEY);
  }
}

function persistCache() {
  const payload: Record<string, StoryImageResult> = {};
  memoryCache.forEach((value, key) => {
    // Avoid persisting large data URLs to localStorage (quota is typically ~5MB).
    if (!value.imageUrl.startsWith("data:")) {
      payload[key] = value;
    }
  });
  try {
    if (Object.keys(payload).length === 0) {
      localStorage.removeItem(CACHE_KEY);
      return;
    }
    localStorage.setItem(CACHE_KEY, JSON.stringify(payload));
  } catch {
    // Storage full or unavailable; keep in-memory cache only.
    localStorage.removeItem(CACHE_KEY);
  }
}

function hashText(input: string): string {
  let hash = 5381;
  for (let i = 0; i < input.length; i += 1) {
    hash = (hash * 33) ^ input.charCodeAt(i);
  }
  return (hash >>> 0).toString(16);
}

function toCacheKey(req: StoryImageRequest): string {
  const charTag = (req.characters ?? [])
    .map((c) => `${c.name}:${c.appearance}`)
    .join("|");
  return hashText(`${req.sceneId}|${req.trigger}|${req.theme}|${req.prompt}|${charTag}`);
}

function buildFallbackImage(prompt: string, theme: StoryImageRequest["theme"]): string {
  const canvas = document.createElement("canvas");
  canvas.width = 1280;
  canvas.height = 720;
  const ctx = canvas.getContext("2d");
  if (!ctx) return "";

  const palette = {
    parchment: ["#7d5d2e", "#312315", "#f0e6ca"],
    nightwatch: ["#13263f", "#0a141f", "#8bc7ff"],
    neon: ["#11221f", "#0f0e1f", "#4bf0ca"],
  }[theme];

  const gradient = ctx.createLinearGradient(0, 0, canvas.width, canvas.height);
  gradient.addColorStop(0, palette[0]);
  gradient.addColorStop(1, palette[1]);
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  ctx.fillStyle = "rgba(255, 255, 255, 0.08)";
  for (let i = 0; i < 24; i += 1) {
    const x = ((i * 89) % canvas.width) + 20;
    const y = ((i * 61) % canvas.height) + 16;
    const w = 140 + (i % 7) * 20;
    const h = 50 + (i % 5) * 14;
    ctx.fillRect(x % canvas.width, y % canvas.height, w, h);
  }

  ctx.fillStyle = palette[2];
  ctx.font = "bold 44px serif";
  ctx.fillText("TRPG SCENE PREVIEW", 58, 86);

  ctx.fillStyle = "rgba(255,255,255,0.9)";
  ctx.font = "30px serif";
  const max = 46;
  const text = prompt.length > max ? `${prompt.slice(0, max)}...` : prompt;
  ctx.fillText(text, 58, 152);

  return canvas.toDataURL("image/png");
}

async function callT2IEndpoint(req: StoryImageRequest): Promise<StoryImageResult> {
  const endpoint = import.meta.env.VITE_T2I_ENDPOINT ?? "http://127.0.0.1:8787/api/t2i";
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 35_000);

  try {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      body: JSON.stringify({
        prompt: req.prompt,
        trigger: req.trigger,
        theme: req.theme,
        scene_id: req.sceneId,
        characters: req.characters ?? [],
      }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = (await response.json()) as {
      image_url?: string;
      prompt?: string;
      provider?: string;
      cached?: boolean;
    };

    if (!data.image_url) throw new Error("Missing image_url");

    return {
      imageUrl: data.image_url,
      revisedPrompt: data.prompt ?? req.prompt,
      provider: data.provider ?? "gemini-http",
      cached: Boolean(data.cached),
    };
  } finally {
    window.clearTimeout(timer);
  }
}

loadCacheFromStorage();

export async function generateStoryImage(req: StoryImageRequest): Promise<StoryImageResult> {
  const key = toCacheKey(req);
  const existing = memoryCache.get(key);
  if (existing && existing.provider !== "local-fallback") return { ...existing, cached: true };
  if (existing && existing.provider === "local-fallback") {
    memoryCache.delete(key);
    persistCache();
  }

  try {
    const fresh = await callT2IEndpoint(req);
    memoryCache.set(key, fresh);
    persistCache();
    return fresh;
  } catch (error) {
    if (req.allowFallback === false) {
      throw error instanceof Error ? error : new Error("T2I request failed");
    }
    const fallback: StoryImageResult = {
      imageUrl: buildFallbackImage(req.prompt, req.theme),
      revisedPrompt: req.prompt,
      provider: "local-fallback",
      cached: false,
    };
    return fallback;
  }
}
