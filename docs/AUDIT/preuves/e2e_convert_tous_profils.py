"""Exécute réellement ffmpeg pour CHAQUE profil de Convert sur un petit échantillon (preuve §7.4)."""
import json, os, subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "outils" / "stub")); sys.path.insert(0, os.getcwd())
from PIL import Image
from azimut.modules.convert import engine, profiles, probe as pr

tmp = Path(tempfile.mkdtemp(prefix="e2e_"))
FF, FP = "ffmpeg", "ffprobe"
def run(cmd, timeout=180):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
img = tmp / "src.png"
im = Image.new("RGB", (65, 47)); im.putdata([(x*3 % 256, y*5 % 256, (x+y) % 256) for y in range(47) for x in range(65)]); im.save(img)
vid = tmp / "src.mkv"
r = run([FF, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=65x47:d=1:r=10", "-f", "lavfi", "-i", "sine=f=440:d=1",
         "-c:v", "ffv1", "-c:a", "pcm_s16le", str(vid)]); assert r.returncode == 0, r.stderr
aud = tmp / "src.wav"
r = run([FF, "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=440:d=1", str(aud)]); assert r.returncode == 0, r.stderr
encs = engine.parse_encoders(run([FF, "-hide_banner", "-encoders"]).stdout)
src_of = {"image": img, "video": vid, "audio": aud}
HW = ("nvenc", "vaapi", "qsv", "amf", "videotoolbox")
rows = []
for p in profiles.PROFILES:
    why = engine.profile_unavailable_reason(p, encs)
    if any(any(h in e for h in HW) for e in p.video_encoders): why = "matériel (GPU requis)"
    if why: rows.append((p.id, "SKIP", why)); continue
    src = src_of[p.kind]
    variants = [("sans sonde", None)]
    if p.kind == "video":
        pj = run([FP, *engine.build_media_probe_command(FP, src)[1:]]); variants.append(("avec sonde", pr.parse_media_probe(pj.stdout)))
    for label, pb in variants:
        dest = tmp / "out" / f"{p.id}{p.out_ext}"; dest.parent.mkdir(exist_ok=True)
        out = engine.part_path(dest)
        cmd = engine.build_command(p, src, out, ffmpeg=FF, encoders=encs, probe=pb)
        res = run(cmd)
        ok = res.returncode == 0 and out.exists() and out.stat().st_size > 0
        info = ""
        if ok:
            j = json.loads(run([FP, "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height", "-of", "json", str(out)]).stdout or "{}")
            st = j.get("streams", []); v = [s for s in st if s["codec_type"] == "video"]; a = [s for s in st if s["codec_type"] == "audio"]
            info = f"{out.stat().st_size} o ; v={[ (s['codec_name'], s.get('width'), s.get('height')) for s in v]} a={[s['codec_name'] for s in a]}"
            if p.kind == "video" and not v: ok = False; info += " ; PAS DE FLUX VIDÉO"
            if p.kind == "video" and not a and label == "avec sonde": ok = False; info += " ; AUDIO PERDU"
            if p.kind == "audio" and not a: ok = False; info += " ; PAS D'AUDIO"
            if p.kind == "image" and not v: ok = False; info += " ; PAS D'IMAGE"
            if p.even_dims and v and (v[0].get("width", 2) % 2 or v[0].get("height", 2) % 2): ok = False; info += " ; DIMENSIONS IMPAIRES"
        else:
            info = (res.stderr or "").strip().splitlines()[-1][:160] if res.stderr.strip() else f"rc={res.returncode}"
        rows.append((f"{p.id} [{label}]", "OK" if ok else "ÉCHEC", info))
w = max(len(r[0]) for r in rows)
for r in rows: print(f"{r[0]:<{w}}  {r[1]:<6} {r[2]}")
from collections import Counter
print(Counter(r[1] for r in rows))
