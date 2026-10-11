"""Accès à la base Discogs.

La clé d'accès personnelle est rangée dans le Trousseau macOS (service « phonotek-discogs »),
jamais dans un fichier du projet.
"""
import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request

import scan

KEYCHAIN_SERVICE = "phonotek-discogs"
API = "https://api.discogs.com"


def _key():
    r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def configured():
    return bool(_key())


def get(path, params=None):
    key = _key()
    if not key:
        raise RuntimeError("Clé Discogs absente du Trousseau (voir l’étape d’installation).")
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"User-Agent": scan.USER_AGENT,
                                               "Authorization": "Discogs token=" + key})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise RuntimeError("Discogs refuse la clé : vérifie-la dans le Trousseau.")
        raise RuntimeError("Discogs indisponible (erreur %d)." % e.code)


def split_title(title):
    """« Artiste - Album » -> (artiste, album)."""
    artist, album = title.split(" - ", 1) if " - " in title else ("", title)
    return clean_artist(artist), album.strip()


def clean_artist(name):
    """Discogs ajoute « * » (variante de nom) et « (2) » (homonymes) : on les retire."""
    return re.sub(r"\s*\(\d+\)", "", name).replace("*", "").strip()


def price(release_id):
    """Prix actuels sur Discogs (en euros), et cote par état si les paramètres vendeur sont remplis."""
    stats = get("/marketplace/stats/%s" % release_id, {"curr_abbr": "EUR"})
    release = get("/releases/%s" % release_id, {"curr_abbr": "EUR"})
    lowest = stats.get("lowest_price") or {}
    try:
        raw = get("/marketplace/price_suggestions/%s" % release_id)
        suggestions = {cond: round(v["value"], 2) for cond, v in raw.items()}
        currency = next(iter(raw.values()), {}).get("currency", "EUR")
    except RuntimeError:
        suggestions, currency = None, None
    community = release.get("community") or {}
    return {
        "lowest": lowest.get("value"),
        "numForSale": stats.get("num_for_sale") or 0,
        "have": community.get("have"),
        "want": community.get("want"),
        "suggestions": suggestions,
        "suggestionsCurrency": currency,
        "sellUrl": "https://www.discogs.com/sell/release/%s" % release_id,
    }


def search(query="", barcode=""):
    params = {"type": "release", "format": "Vinyl", "per_page": 20}
    if barcode:
        params["barcode"] = barcode
    else:
        params["q"] = query
    results = []
    for r in get("/database/search", params).get("results", []):
        artist, album = split_title(r.get("title", ""))
        results.append({
            "discogsId": r["id"],
            "artist": artist,
            "album": album,
            "year": r.get("year") or "",
            "country": r.get("country") or "",
            "label": (r.get("label") or [""])[0],
            "format": ", ".join(dict.fromkeys(r.get("format") or [])),
            "genre": scan.map_genre(", ".join((r.get("genre") or []) + (r.get("style") or []))),
            "thumb": r.get("thumb") or "",
            "cover": r.get("cover_image") or "",
        })
    return results


def tracklist(release_id):
    """Pistes d'un vinyle avec leur face (A1, A2, B1...)."""
    release = get("/releases/%s" % release_id)
    tracks = [t for t in release.get("tracklist", []) if t.get("type_", "track") == "track"]
    return [{"n": i + 1, "pos": t.get("position") or "", "title": t.get("title") or ""} for i, t in enumerate(tracks)]
