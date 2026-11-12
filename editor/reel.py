#!/usr/bin/env python3
"""
Editor automático de Reels para Instagram (9:16, 1080x1920, 10-20 s).

Uso rápido:
  python3 editor/reel.py clip1.mp4 clip2.mp4 foto.jpg -o reel.mp4 \
      --estilo energia --texto "DÍA DE PIERNA 🔥" --musica cancion.mp3

Cada clip puede llevar el segundo de inicio con @:  sentadilla.mp4@4.5
Estilos:
  energia    -> cortes rápidos, transiciones con zoom y deslizamiento (entreno)
  cambio     -> antes/después con flash blanco y etiquetas ANTES / DESPUÉS
  cinematico -> transiciones suaves, look oscuro y contrastado (motivación)
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

W, H, FPS = 1080, 1920, 30
FONT = "/usr/share/fonts/opentype/inter/Inter-ExtraBold.otf"
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic"}

ESTILOS = {
    "energia": {
        "trans": ["zoomin", "slideleft", "smoothup", "fadewhite", "slideright", "circleopen"],
        "dur_trans": 0.25,
        "grade": "eq=contrast=1.12:saturation=1.25:brightness=0.01,unsharp=5:5:0.6",
    },
    "cambio": {
        "trans": ["fadewhite"],
        "dur_trans": 0.35,
        "grade": "eq=contrast=1.10:saturation=1.15,unsharp=5:5:0.5",
    },
    "cinematico": {
        "trans": ["fadeblack", "smoothleft", "dissolve", "radial"],
        "dur_trans": 0.5,
        "grade": "eq=contrast=1.18:saturation=0.85:brightness=-0.03,"
                 "curves=preset=increase_contrast,vignette=PI/4.5",
    },
}


def probe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
        capture_output=True, text=True, check=True).stdout
    return float(json.loads(out)["format"]["duration"])


def has_audio(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
         "-of", "csv=p=0", path], capture_output=True, text=True).stdout
    return bool(out.strip())


def limpiar(text):
    """Quita emojis (la fuente no los tiene): mejor ponerlos en el copy de Instagram."""
    t = "".join(c for c in text if ord(c) < 0x2190 or 0x2E80 <= ord(c) < 0x1F000)
    return " ".join(t.split())


def text_filter(tmpdir, name, text, start, end, y, size, box=True):
    """drawtext con aparición 'pop' (fade in rápido)."""
    text = limpiar(text)
    path = os.path.join(tmpdir, f"{name}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    alpha = f"if(lt(t,{start}),0,if(lt(t,{start}+0.15),(t-{start})/0.15,if(lt(t,{end}-0.2),1,max(0,({end}-t)/0.2))))"
    f = (f"drawtext=fontfile={FONT}:textfile={path}:fontsize={size}:fontcolor=white"
         f":x=(w-text_w)/2:y={y}:alpha='{alpha}'"
         f":shadowcolor=black@0.6:shadowx=4:shadowy=4"
         f":enable='between(t,{start},{end})'")
    if box:
        f += ":box=1:boxcolor=black@0.45:boxborderw=28"
    return f


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clips", nargs="+", help="vídeos o fotos, en orden (opcional archivo@segundo_inicio)")
    ap.add_argument("-o", "--salida", default="reel.mp4")
    ap.add_argument("-d", "--duracion", type=float, default=15.0, help="duración total en s (10-20)")
    ap.add_argument("-e", "--estilo", choices=ESTILOS, default="energia")
    ap.add_argument("-t", "--texto", default="", help="gancho que aparece los primeros 2.5 s")
    ap.add_argument("--cierre", default="", help="texto final (p.ej. 'SÍGUEME PARA MÁS 💪')")
    ap.add_argument("--usuario", default="", help="@usuario pequeño abajo durante todo el vídeo")
    ap.add_argument("-m", "--musica", default="", help="mp3/m4a de fondo")
    ap.add_argument("--musica-inicio", type=float, default=0.0, help="segundo de la canción donde empezar (el drop)")
    ap.add_argument("--audio-original", action="store_true", help="mantener el sonido de los clips")
    a = ap.parse_args()

    est = ESTILOS[a.estilo]
    T = est["dur_trans"]
    n = len(a.clips)
    seg = (a.duracion + (n - 1) * T) / n  # duración de cada tramo contando solapes
    if seg < T * 2:
        sys.exit("Demasiados clips para esa duración: sube --duracion o usa menos clips.")

    tmpdir = tempfile.mkdtemp(prefix="reel_")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    fc = []
    audio_ok = []

    for i, spec in enumerate(a.clips):
        path, _, start = spec.partition("@")
        if not os.path.exists(path):
            sys.exit(f"No encuentro {path}")
        es_img = os.path.splitext(path)[1].lower() in IMG_EXT
        if es_img:
            cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{seg:.3f}", "-i", path]
            frames = int(seg * FPS) + 1
            # Ken Burns: zoom lento hacia el centro
            v = (f"[{i}:v]scale={W*2}:{H*2}:force_original_aspect_ratio=increase,crop={W*2}:{H*2},"
                 f"zoompan=z='min(zoom+0.0012,1.12)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                 f":d={frames}:s={W}x{H}:fps={FPS}")
            audio_ok.append(False)
        else:
            dur = probe_duration(path)
            if start:
                ss = float(start)
            else:  # por defecto, el tramo central (suele ser la mejor repetición)
                ss = max(0.0, (dur - seg) / 2)
            ss = min(ss, max(0.0, dur - seg))
            cmd += ["-ss", f"{ss:.3f}", "-t", f"{seg:.3f}", "-i", path]
            v = (f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                 f"fps={FPS}")
            # si el clip es más corto que el tramo, congelar el último frame
            v += f",tpad=stop_mode=clone:stop_duration={seg:.3f},trim=duration={seg:.3f}"
            audio_ok.append(has_audio(path))
        v += f",setsar=1,format=yuv420p,{est['grade']},setpts=PTS-STARTPTS"

        if a.estilo == "cambio" and n >= 2 and i in (0, n - 1):
            etiqueta = "ANTES" if i == 0 else "DESPUÉS"
            v += "," + text_filter(tmpdir, f"lbl{i}", etiqueta, 0.1, seg - 0.05, "h*0.78", 110)
        fc.append(v + f"[v{i}]")

    # Cadena de transiciones
    last = "v0"
    offset = 0.0
    for i in range(1, n):
        offset += seg - T
        tr = est["trans"][(i - 1) % len(est["trans"])]
        out = f"x{i}"
        fc.append(f"[{last}][v{i}]xfade=transition={tr}:duration={T}:offset={offset:.3f}[{out}]")
        last = out
    total = seg * n - T * (n - 1)

    # Textos globales
    capas = []
    if a.texto:
        capas.append(text_filter(tmpdir, "hook", a.texto, 0.0, min(2.8, total), "h*0.16", 92))
    if a.cierre:
        capas.append(text_filter(tmpdir, "cierre", a.cierre, max(0, total - 2.2), total, "h*0.45", 84))
    if a.usuario:
        p = os.path.join(tmpdir, "user.txt")
        open(p, "w", encoding="utf-8").write(limpiar(a.usuario))
        capas.append(f"drawtext=fontfile={FONT}:textfile={p}:fontsize=44:fontcolor=white@0.85"
                     f":x=(w-text_w)/2:y=h*0.90:shadowcolor=black@0.6:shadowx=2:shadowy=2")
    capas.append(f"fade=t=out:st={total-0.3:.3f}:d=0.3")
    fc.append(f"[{last}]" + ",".join(capas) + "[vout]")

    # Audio
    maps = ["-map", "[vout]"]
    if a.musica:
        idx = n
        cmd += ["-ss", str(a.musica_inicio), "-i", a.musica]
        fc.append(f"[{idx}:a]atrim=duration={total:.3f},asetpts=PTS-STARTPTS,"
                  f"afade=t=in:d=0.05,afade=t=out:st={total-1.0:.3f}:d=1.0,loudnorm=I=-14:TP=-1[aout]")
        maps += ["-map", "[aout]"]
    elif a.audio_original:
        partes = []
        for i in range(n):
            if audio_ok[i]:
                fc.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,"
                          f"apad,atrim=duration={seg:.3f}[a{i}]")
            else:
                fc.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={seg:.3f}[a{i}]")
            partes.append(f"a{i}")
        prev = partes[0]
        for i in range(1, n):
            fc.append(f"[{prev}][{partes[i]}]acrossfade=d={T}[ac{i}]")
            prev = f"ac{i}"
        fc.append(f"[{prev}]afade=t=out:st={total-0.5:.3f}:d=0.5,loudnorm=I=-14:TP=-1[aout]")
        maps += ["-map", "[aout]"]
    else:
        # pista silenciosa: añade la música desde la app de Instagram (más alcance)
        fc.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={total:.3f}[aout]")
        maps += ["-map", "[aout]"]

    cmd += ["-filter_complex", ";".join(fc)] + maps + [
        "-t", f"{total:.3f}", "-r", str(FPS),
        "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-profile:v", "high",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart", a.salida]

    r = subprocess.run(cmd)
    if r.returncode != 0:
        sys.exit("ffmpeg falló")
    print(f"✅ {a.salida}  ({total:.1f} s, {W}x{H}, estilo {a.estilo})")


if __name__ == "__main__":
    main()
