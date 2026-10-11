"""Envoie la collection du Mac (CD de l'Eversolo, vinyles déjà saisis) vers Supabase.

Usage : python3 sync.py
- Les disques absents en ligne sont ajoutés, avec leur pochette.
- Les disques déjà en ligne ne sont jamais écrasés (les corrections faites sur l'iPhone sont conservées) :
  seul l'indicateur « introuvable sur l'Eversolo » est mis à jour.
La clé d'administration Supabase est lue dans le Trousseau (service « phonotek-supabase »).
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

import scan

SUPABASE_URL = "https://ugasyhsxwabairtzihvb.supabase.co"
CONFIG_FILE = os.path.join(scan.ROOT, "data", "sync.json")
LOCAL_ONLY = {"coverVersion", "_cover_file"}


def secret():
    r = subprocess.run(["security", "find-generic-password", "-s", "phonotek-supabase", "-w"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("Clé Supabase absente du Trousseau (service phonotek-supabase).")
    return r.stdout.strip()


KEY = secret()


def call(method, path, body=None, headers=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    h = {"apikey": KEY, "Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(SUPABASE_URL + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            text = r.read()
            return json.loads(text) if text else None
    except urllib.error.HTTPError as e:
        raise RuntimeError("%s %s : %s %s" % (method, path, e.code, e.read()[:300].decode(errors="replace")))


def owner_id():
    """Identifiant Supabase de Benoit (retrouvé par son e-mail, puis mémorisé)."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return json.load(f)["owner"]
    email = input("E-mail de ton compte Phonotek : ").strip().lower()
    users = call("GET", "/auth/v1/admin/users?per_page=50").get("users", [])
    me = next((u for u in users if (u.get("email") or "").lower() == email), None)
    if not me:
        sys.exit("Compte %s introuvable dans Supabase." % email)
    with open(CONFIG_FILE, "w") as f:
        json.dump({"owner": me["id"]}, f)
    return me["id"]


def upload_cover(owner, album):
    local = os.path.join(scan.ROOT, "web", album["cover"]) if album.get("cover") else None
    if not local or not os.path.exists(local):
        return None
    path = "%s/%s.jpg" % (owner, album["id"])
    with open(local, "rb") as f:
        call("POST", "/storage/v1/object/covers/" + urllib.parse.quote(path), raw=f.read(),
             headers={"Content-Type": "image/jpeg", "x-upsert": "true"})
    return "%s/storage/v1/object/public/covers/%s" % (SUPABASE_URL, path)


def sync(log=print):
    owner = owner_id()
    local = scan.load_collection()["albums"]
    remote = {r["id"]: r for r in call("GET", "/rest/v1/albums?select=id,data&owner=eq." + owner + "&limit=10000")}
    added = updated = 0
    for album in local:
        if album["id"] in remote:
            data = remote[album["id"]]["data"]
            if album.get("source") == "eversolo" and bool(data.get("missing")) != bool(album.get("missing")):
                data["missing"] = bool(album.get("missing"))
                call("PATCH", "/rest/v1/albums?id=eq." + album["id"], {"data": data})
                updated += 1
            continue
        data = {k: v for k, v in album.items() if k not in LOCAL_ONLY and k != "id"}
        data["cover"] = upload_cover(owner, album)
        call("POST", "/rest/v1/albums", {"id": album["id"], "owner": owner, "data": data},
             headers={"Prefer": "return=minimal"})
        added += 1
        if added % 25 == 0:
            log("  %d disques envoyés..." % added)
    return {"added": added, "updated": updated, "online": len(remote) + added}


if __name__ == "__main__":
    print("Envoi de la collection vers Phonotek en ligne...")
    r = sync()
    print("Terminé : %(added)d ajoutés, %(updated)d mis à jour, %(online)d disques en ligne." % r)
