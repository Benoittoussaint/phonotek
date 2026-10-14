"""Corrige le nom d'artiste (bloc LIST/INFO, champ IART) de fichiers WAV sur l'Eversolo.

Sécurité :
- on part toujours de la copie de sauvegarde, jamais du fichier en place ;
- le nouveau fichier est construit sur le Mac, puis on vérifie que la musique (bloc « data ») est
  identique octet pour octet à l'original ;
- il est copié sur l'Eversolo sous un nom temporaire, puis renommé à la place de l'original.

Usage : python3 corriger_artiste.py <dossier sauvegarde> <dossier sur l'Eversolo> "<nouvel artiste>"
"""
import hashlib
import os
import shutil
import struct
import sys
import tempfile


def read_chunks(path):
    """Liste des blocs RIFF : (identifiant, position des données, taille)."""
    chunks = []
    with open(path, "rb") as f:
        riff, _, wave = struct.unpack("<4sI4s", f.read(12))
        if riff != b"RIFF" or wave != b"WAVE":
            raise ValueError("pas un fichier WAV : %s" % path)
        while True:
            head = f.read(8)
            if len(head) < 8:
                break
            cid, size = struct.unpack("<4sI", head)
            chunks.append((cid, f.tell(), size))
            f.seek(size + (size & 1), 1)
    return chunks


def info_items(data):
    """Champs d'un bloc LIST/INFO (sans le mot « INFO »), dans l'ordre."""
    items, i = [], 0
    while i + 8 <= len(data):
        key, size = struct.unpack("<4sI", data[i:i + 8])
        items.append([key, data[i + 8:i + 8 + size]])
        i += 8 + size + (size & 1)
    return items


def build_info(items):
    body = b"INFO"
    for key, value in items:
        body += struct.pack("<4sI", key, len(value)) + value + (b"\0" if len(value) & 1 else b"")
    return body


def data_digest(path, chunks):
    _, pos, size = next(c for c in chunks if c[0] == b"data")
    h = hashlib.sha256()
    with open(path, "rb") as f:
        f.seek(pos)
        left = size
        while left:
            block = f.read(min(left, 1 << 20))
            h.update(block)
            left -= len(block)
    return h.hexdigest(), size


def retag(src, dst, artist):
    """Écrit dans dst une copie de src avec IART = artist. Renvoie l'ancien artiste."""
    chunks = read_chunks(src)
    out = b""
    old = None
    with open(src, "rb") as f:
        for cid, pos, size in chunks:
            if cid == b"data":
                break
            f.seek(pos)
            payload = f.read(size)
            if cid == b"LIST" and payload[:4] == b"INFO":
                items = info_items(payload[4:])
                for item in items:
                    if item[0] == b"IART":
                        old = item[1].rstrip(b"\0").decode("utf-8", "replace")
                        item[1] = artist.encode("utf-8") + b"\0"
                if old is None:
                    items.append([b"IART", artist.encode("utf-8") + b"\0"])
                payload = build_info(items)
            out += struct.pack("<4sI", cid, len(payload)) + payload + (b"\0" if len(payload) & 1 else b"")
        _, dpos, dsize = next(c for c in chunks if c[0] == b"data")
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        fout.write(b"RIFF" + struct.pack("<I", 4 + len(out) + 8 + dsize + (dsize & 1)) + b"WAVE")
        fout.write(out)
        fout.write(struct.pack("<4sI", b"data", dsize))
        fin.seek(dpos)
        shutil.copyfileobj(fin, fout, 1 << 20)
        if dsize & 1:
            fout.write(b"\0")
    return old


def main(backup_dir, target_dir, artist):
    tmpdir = tempfile.mkdtemp(prefix="phonotek-")
    for name in sorted(os.listdir(backup_dir)):
        if not name.lower().endswith(".wav") or name.startswith("."):
            continue
        src = os.path.join(backup_dir, name)
        fixed = os.path.join(tmpdir, name)
        old = retag(src, fixed, artist)
        if data_digest(src, read_chunks(src)) != data_digest(fixed, read_chunks(fixed)):
            sys.exit("ARRÊT : la musique de %s ne serait pas identique. Rien n'a été remplacé pour ce fichier." % name)
        target = os.path.join(target_dir, name)
        temp_target = os.path.join(target_dir, ".phonotek-" + name)
        shutil.copyfile(fixed, temp_target)
        if os.path.getsize(temp_target) != os.path.getsize(fixed):
            os.remove(temp_target)
            sys.exit("ARRÊT : copie incomplète de %s sur l'Eversolo." % name)
        os.replace(temp_target, target)
        os.remove(fixed)
        print("  %s : %s -> %s" % (name, old, artist))
    os.rmdir(tmpdir)


if __name__ == "__main__":
    main(*sys.argv[1:4])
