// Phonotek : fonction Supabase qui interroge Discogs, Deezer et iTunes pour l'application.
// La clé Discogs est lue dans les secrets Supabase (DISCOGS_TOKEN), jamais envoyée au téléphone.
import { createClient } from "jsr:@supabase/supabase-js@2";

const UA = "Phonotek/0.2 (catalogue musical personnel)";
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};
const COVER_HOSTS = ["mzstatic.com", "dzcdn.net", "deezer.com", "discogs.com"];
// Clé d'administration : ancienne variable (SUPABASE_SERVICE_ROLE_KEY) ou nouvelles clés (SUPABASE_SECRET_KEYS).
function serviceKey(): string {
  const legacy = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (legacy) return legacy;
  try {
    const keys = JSON.parse(Deno.env.get("SUPABASE_SECRET_KEYS") || "{}");
    return keys.default || Object.values(keys)[0] as string || "";
  } catch {
    return "";
  }
}
const admin = createClient(Deno.env.get("SUPABASE_URL")!, serviceKey() || "absente");

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { ...CORS, "Content-Type": "application/json" } });

class UserError extends Error {}

async function getJson(url: string, headers: Record<string, string> = {}) {
  const r = await fetch(url, { headers: { "User-Agent": UA, ...headers } });
  if (r.status === 401) throw new UserError("Discogs refuse la clé : vérifie le secret DISCOGS_TOKEN.");
  if (!r.ok) throw new UserError(`Service indisponible (erreur ${r.status}).`);
  return r.json();
}

// ---------- Styles ----------
const GENRE_MAP: [string, string][] = [
  ["rap français", "Rap français"], ["french rap", "Rap français"],
  ["rap", "Rap / Hip-hop"], ["hip", "Rap / Hip-hop"],
  ["reggae", "Reggae"], ["dub", "Reggae"], ["ska", "Reggae"],
  ["jazz", "Jazz"], ["blues", "Blues"],
  ["soul", "Soul / Funk"], ["funk", "Soul / Funk"], ["r&b", "Soul / Funk"],
  ["electro", "Électro"], ["dance", "Électro"], ["house", "Électro"], ["techno", "Électro"],
  ["alternative", "Rock"], ["indie", "Rock"], ["rock", "Rock"], ["metal", "Rock"], ["punk", "Rock"],
  ["chanson", "Chanson française"], ["variété française", "Chanson française"], ["french pop", "Chanson française"],
  ["world", "Musiques du monde"], ["afro", "Musiques du monde"], ["latin", "Musiques du monde"],
  ["classique", "Classique"], ["classical", "Classique"], ["soundtrack", "Bande originale"],
  ["films", "Bande originale"], ["pop", "Pop"],
];
const mapGenre = (raw: string) => {
  const low = (raw || "").toLowerCase();
  return GENRE_MAP.find(([k]) => low.includes(k))?.[1] ?? null;
};
const slug = (s: string) => (s || "").normalize("NFKD").replace(/[^\w]/g, "").toLowerCase();
function bestMatch<T>(results: T[], artist: string, getArtist: (r: T) => string) {
  const target = slug(artist);
  return results.find((r) => {
    const n = slug(getArtist(r));
    return n && (n.includes(target) || target.includes(n));
  }) ?? results[0];
}

async function genre(artist: string, album: string) {
  const q = encodeURIComponent(`${artist === "Artistes divers" ? "" : artist} ${album}`);
  let deezer: string | null = null, deezerRaw = "";
  try {
    const found = bestMatch((await getJson(`https://api.deezer.com/search/album?limit=5&q=${q}`)).data ?? [],
      artist, (r: any) => r.artist?.name);
    if (found) {
      const g = (await getJson(`https://api.deezer.com/album/${(found as any).id}`)).genres?.data ?? [];
      deezerRaw = g.map((x: any) => x.name).join(", ");
      deezer = mapGenre(deezerRaw);
    }
  } catch { /* on tente iTunes */ }
  if (deezer && deezer !== "Pop" && deezer !== "Électro") return { genre: deezer, raw: deezerRaw };
  try {
    const found: any = bestMatch((await getJson(`https://itunes.apple.com/search?entity=album&limit=5&term=${q}`)).results ?? [],
      artist, (r: any) => r.artistName);
    if (found && mapGenre(found.primaryGenreName)) return { genre: mapGenre(found.primaryGenreName), raw: found.primaryGenreName };
  } catch { /* rien */ }
  return { genre: deezer, raw: deezerRaw };
}

// ---------- Discogs ----------
const discogs = (path: string, params: Record<string, string> = {}) => {
  const token = Deno.env.get("DISCOGS_TOKEN");
  if (!token) throw new UserError("Clé Discogs absente des secrets Supabase.");
  const qs = new URLSearchParams(params).toString();
  return getJson(`https://api.discogs.com${path}${qs ? "?" + qs : ""}`, { Authorization: `Discogs token=${token}` });
};
const cleanArtist = (s: string) => s.replace(/\s*\(\d+\)/g, "").replace(/\*/g, "").trim();

async function discogsSearch(q: string, barcode: string) {
  const params: Record<string, string> = { type: "release", format: "Vinyl", per_page: "20" };
  if (barcode) params.barcode = barcode; else params.q = q;
  const res = (await discogs("/database/search", params)).results ?? [];
  return res.map((r: any) => {
    const [artist, album] = (r.title || "").includes(" - ") ? r.title.split(/ - (.*)/s) : ["", r.title || ""];
    return {
      discogsId: r.id, artist: cleanArtist(artist), album: (album || "").trim(),
      year: r.year || "", country: r.country || "", label: (r.label || [""])[0],
      format: [...new Set(r.format || [])].join(", "),
      genre: mapGenre([...(r.genre || []), ...(r.style || [])].join(", ")),
      thumb: r.thumb || "", cover: r.cover_image || "",
    };
  });
}

async function discogsPrice(id: string) {
  const [stats, release] = await Promise.all([
    discogs(`/marketplace/stats/${id}`, { curr_abbr: "EUR" }),
    discogs(`/releases/${id}`, { curr_abbr: "EUR" }),
  ]);
  let suggestions: Record<string, number> | null = null;
  try {
    const raw = await discogs(`/marketplace/price_suggestions/${id}`);
    suggestions = Object.fromEntries(Object.entries(raw).map(([k, v]: any) => [k, Math.round(v.value * 100) / 100]));
  } catch { /* paramètres vendeur non remplis */ }
  return {
    lowest: stats.lowest_price?.value ?? null, numForSale: stats.num_for_sale ?? 0,
    have: release.community?.have ?? null, want: release.community?.want ?? null,
    suggestions, sellUrl: `https://www.discogs.com/sell/release/${id}`,
    checked: new Date().toISOString().slice(0, 10),
  };
}

async function discogsTracks(id: string) {
  const tracks = ((await discogs(`/releases/${id}`)).tracklist ?? []).filter((t: any) => (t.type_ ?? "track") === "track");
  return tracks.map((t: any, i: number) => ({ n: i + 1, pos: t.position || "", title: t.title || "" }));
}

// ---------- Deezer et pochettes ----------
async function deezerLink(artist: string, title: string, kind: string) {
  const field = kind === "album" ? "album" : "track";
  let data = (await getJson(`https://api.deezer.com/search/${field}?limit=5&q=` +
    encodeURIComponent(`artist:"${artist}" ${field}:"${title}"`))).data ?? [];
  if (!data.length) data = (await getJson(`https://api.deezer.com/search/${field}?limit=5&q=` +
    encodeURIComponent(`${artist} ${title}`))).data ?? [];
  if (!data.length) throw new UserError("Introuvable sur Deezer.");
  return { link: data[0].link, id: data[0].id, kind: field };
}

async function coverSearch(q: string) {
  const out: any[] = [];
  const enc = encodeURIComponent(q);
  try {
    for (const r of (await getJson(`https://itunes.apple.com/search?entity=album&limit=8&term=${enc}`)).results ?? [])
      if (r.artworkUrl100) out.push({ source: "iTunes", label: `${r.artistName} - ${r.collectionName}`,
        thumb: r.artworkUrl100.replace("100x100", "300x300"), url: r.artworkUrl100.replace("100x100", "600x600") });
  } catch { /* rien */ }
  try {
    for (const r of (await getJson(`https://api.deezer.com/search/album?limit=8&q=${enc}`)).data ?? [])
      if (r.cover_big) out.push({ source: "Deezer", label: `${r.artist.name} - ${r.title}`, thumb: r.cover_medium, url: r.cover_big });
  } catch { /* rien */ }
  return out;
}

async function setCover(userId: string, albumId: string, url: string) {
  const host = new URL(url).hostname;
  if (!url.startsWith("https://") || !COVER_HOSTS.some((h) => host.endsWith(h))) throw new UserError("Adresse de pochette non autorisée.");
  const { data: row } = await admin.from("albums").select("owner,data").eq("id", albumId).single();
  if (!row || row.owner !== userId) throw new UserError("Ce disque n’est pas le tien.");
  const img = await fetch(url, { headers: { "User-Agent": UA } });
  if (!img.ok) throw new UserError("Pochette impossible à télécharger.");
  const path = `${userId}/${albumId}-${Date.now()}.jpg`;
  const up = await admin.storage.from("covers").upload(path, await img.arrayBuffer(), { contentType: "image/jpeg", upsert: true });
  if (up.error) throw new Error(up.error.message);
  const cover = admin.storage.from("covers").getPublicUrl(path).data.publicUrl;
  const data = { ...row.data, cover, coverSource: "manuel" };
  await admin.from("albums").update({ data }).eq("id", albumId);
  return data;
}

// ---------- Point d'entrée ----------
Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  try {
    const token = (req.headers.get("Authorization") || "").replace("Bearer ", "");
    const { data: { user } } = await admin.auth.getUser(token);
    if (!user) return json({ error: "Connexion requise." }, 401);
    const b = await req.json();
    switch (b.action) {
      case "discogsSearch": return json(await discogsSearch(b.q || "", b.barcode || ""));
      case "discogsPrice": return json(await discogsPrice(String(b.id)));
      case "discogsTracks": return json(await discogsTracks(String(b.id)));
      case "deezer": return json(await deezerLink(b.artist || "", b.title || "", b.kind || "track"));
      case "coverSearch": return json(await coverSearch(b.q || ""));
      case "setCover": return json(await setCover(user.id, b.albumId, b.url));
      case "genre": return json(await genre(b.artist || "", b.album || ""));
      default: return json({ error: "Action inconnue." }, 400);
    }
  } catch (e) {
    return json({ error: e instanceof UserError ? e.message : "Erreur : " + (e as Error).message }, e instanceof UserError ? 400 : 500);
  }
});
