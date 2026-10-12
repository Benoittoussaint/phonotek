"""Relie les CD de Phonotek à la bibliothèque musicale de l'Eversolo.

Pour chaque album, on relève l'identifiant Eversolo de l'album et de chaque piste. Phonotek peut alors
lancer un morceau « depuis la bibliothèque » (playMusic type=4) : l'album entier passe dans la file,
dans l'ordre du disque, avec l'affichage habituel de l'Eversolo.

Usage : python3 eversolo_ids.py   (Eversolo réveillé ; le relevé est volontairement lent)
"""
import json
import os
import time
import urllib.parse
import urllib.request

import scan
import sync

API = scan.EVERSOLO_API + "/ZidooMusicControl/v2"
PAUSE = 1.0   # la base musicale de l'Eversolo se bloque si on l'interroge trop vite


def get(path, **params):
    url = "%s/%s?%s" % (API, path, urllib.parse.urlencode(params))
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def norm(s):
    return (s or "").replace("’", "'").replace("‐", "-").strip().lower()


def crawl(log=print):
    """Renvoie {(dossier, nom de fichier): (id album, id piste, numéro)} pour toute la bibliothèque."""
    albums, start = [], 0
    while True:
        page = get("getAlbums", start=start, count=100).get("array", [])
        albums += page
        if len(page) < 100:
            break
        start += 100
        time.sleep(PAUSE)
    index = {}
    for i, album in enumerate(albums):
        time.sleep(PAUSE)
        try:
            tracks = get("getAlbumMusics", id=album["id"], start=0, count=200).get("array", [])
        except Exception as e:
            log("  album %s ignoré (%s)" % (album.get("name"), e))
            continue
        for t in tracks:
            folder, _, filename = (t.get("uri") or "").rpartition("/")
            index[(norm(os.path.basename(folder)), norm(filename))] = (album["id"], t["id"], t.get("number"))
        if (i + 1) % 25 == 0:
            log("  %d / %d albums relevés" % (i + 1, len(albums)))
    return index


def main():
    index = crawl()
    owner = json.load(open(sync.CONFIG_FILE))["owner"]
    rows = sync.call("GET", "/rest/v1/albums?select=id,data&owner=eq." + owner)
    linked = partial = 0
    for row in rows:
        d = row["data"]
        if d.get("source") != "eversolo" or not d.get("trackList"):
            continue
        folder = norm(os.path.basename(d["folder"]))
        hits = 0
        for t in d["trackList"]:
            found = index.get((folder, norm(t.get("file"))))
            if found:
                d["eversoloAlbumId"], t["eversoloId"] = found[0], found[1]
                hits += 1
        if hits:
            sync.call("PATCH", "/rest/v1/albums?id=eq." + row["id"], {"data": d})
            linked += 1
            partial += hits < len(d["trackList"])
    print("%d albums reliés à la bibliothèque de l'Eversolo (%d partiellement)." % (linked, partial))


if __name__ == "__main__":
    main()
