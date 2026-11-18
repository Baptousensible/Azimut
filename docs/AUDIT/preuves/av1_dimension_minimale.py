"""Preuve LOG-002 : libsvtav1 refuse une hauteur < 64 px, libaom-av1 l'accepte (ffmpeg réel).
À lancer depuis n'importe où : python docs/AUDIT/preuves/av1_dimension_minimale.py"""
import subprocess

for encoder in ("libsvtav1", "libaom-av1"):
    results = []
    for height in (16, 32, 46, 47, 48, 63, 64, 65, 128):
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=s=128x{height}:d=0.2:r=5",
                            "-frames:v", "1", "-c:v", encoder, "-f", "null", "-"], capture_output=True, text=True)
        results.append((height, "ok" if r.returncode == 0 else "ÉCHEC"))
    print(encoder, results)
