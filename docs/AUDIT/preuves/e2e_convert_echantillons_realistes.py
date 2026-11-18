"""Preuve exécutée (FFmpeg réel). À lancer depuis la racine du dépôt : python docs/AUDIT/preuves/e2e_convert_echantillons_realistes.py"""
import json, os, subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "outils" / "stub")); sys.path.insert(0, os.getcwd())
from PIL import Image
from azimut.modules.convert import engine, profiles, probe as pr
tmp = Path(tempfile.mkdtemp(prefix="e2e2_")); FF, FP = "ffmpeg", "ffprobe"
def run(c): return subprocess.run(c, capture_output=True, text=True, timeout=240)
img = tmp/"big.png"; Image.effect_noise((320, 240), 40).convert("RGB").save(img)
vp9 = tmp/"vp9.mkv"; h264 = tmp/"h264.mkv"
assert run([FF,"-y","-v","error","-f","lavfi","-i","testsrc2=s=320x240:d=1:r=10","-f","lavfi","-i","sine=f=440:d=1","-ac","1","-c:v","libvpx-vp9","-c:a","libopus",str(vp9)]).returncode==0
assert run([FF,"-y","-v","error","-f","lavfi","-i","testsrc2=s=321x241:d=1:r=10","-f","lavfi","-i","sine=f=440:d=1","-ac","2","-c:v","libx264","-pix_fmt","yuv444p","-c:a","aac",str(h264)]).returncode==0
encs = engine.parse_encoders(run([FF,"-hide_banner","-encoders"]).stdout)
def trial(pid, src, label):
    p = profiles.get_profile(pid)
    pb = pr.parse_media_probe(run([FP,*engine.build_media_probe_command(FP,src)[1:]]).stdout) if p.kind=="video" else None
    for lab, probe in ((("sans sonde",None),("avec sonde",pb)) if p.kind=="video" else (("-",None),)):
        out = engine.part_path(tmp/"o"/f"{pid}{p.out_ext}"); out.parent.mkdir(exist_ok=True)
        r = run(engine.build_command(p, src, out, ffmpeg=FF, encoders=encs, probe=probe))
        ok = r.returncode==0 and out.exists() and out.stat().st_size>0
        det = ""
        if ok:
            st = json.loads(run([FP,"-v","error","-show_entries","stream=codec_type,codec_name,width,height,channels","-of","json",str(out)]).stdout)["streams"]
            det = " ; ".join(f"{s['codec_type']}:{s['codec_name']}"+(f" {s.get('width')}x{s.get('height')}" if s['codec_type']=='video' else f" ch={s.get('channels')}") for s in st)
        else: det = (r.stderr.strip().splitlines() or ["rc=%d"%r.returncode])[-1][:150]
        print(f"{pid:<16} {label:<22} {lab:<11} {'OK   ' if ok else 'ÉCHEC'} {det}")
for pid in ("image_avif",): trial(pid, img, "png 320x240")
for pid in ("video_webm_av1","video_mp4_av1"): trial(pid, vp9, "vp9 320x240 mono")
for pid in ("video_webm_av1","video_mp4_av1"): trial(pid, h264, "h264 321x241 444 st")
for pid in ("video_fix_mono",): trial(pid, vp9, "vp9 mono")
for pid in ("video_fix_mono",): trial(pid, h264, "h264 stéréo")
p = profiles.get_profile("video_fix_mono"); print("fix_mono:", p.out_ext, p.video_copy, p.audio_locked, p.video_encoders, p.audio_encoders)
