#!/usr/bin/env python3
"""
BRUTAL: editor de reels virales de gym a partir de CLIPS DE VÍDEO.

Cortes sincronizados con el ritmo de la música, zoom-punch y temblor en cada golpe,
flash blanco, glitch RGB, speed ramps (rápido -> cámara lenta suave), stutters,
gradación de color de cine, grano y texto con efecto "pop".

Ejemplo:
  python3 editor/brutal.py clips/*.mp4 -m cancion.mp3 --musica-inicio 42 \
      -t "SIN GYM. SIN EXCUSAS." --cierre "SÍGUEME" -o reels/reel.mp4

Sin música: --bpm 128 corta al ritmo de 128 BPM (luego elige en Instagram un audio
con ese tempo) o añade --beat-demo para meter un beat sintético de prueba.
Cada clip admite inicio con @:  dominadas.mp4@3.2
"""
import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile

import numpy as np

W, H, FPS = 1080, 1920, 30
BW, BH = 1620, 2880  # base sobredimensionada para zoom/temblor sin perder nitidez
# HDR del iPhone (HLG/PQ) -> SDR, si no en Instagram se ve lavado o quemado
TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,"
           "zscale=t=bt709:m=bt709:r=tv,format=yuv420p,")
FONT = "/usr/share/fonts/opentype/inter/Inter-ExtraBold.otf"

LOOKS = {
    # contraste fuerte, sombras frías, pieles cálidas
    "cine": "colorbalance=rs=-0.04:gs=-0.01:bs=0.06:rh=0.06:gh=0.02:bh=-0.05,"
            "eq=contrast=1.16:saturation=1.12:gamma=0.97,unsharp=5:5:0.7",
    # oscuro, desaturado, muy dramático (phonk / motivación)
    "oscuro": "eq=contrast=1.28:saturation=0.70:brightness=-0.04:gamma=0.92,"
              "colorbalance=bs=0.05:bm=0.03,unsharp=5:5:0.8",
    # vivo y saturado
    "vivo": "eq=contrast=1.12:saturation=1.35:brightness=0.01,vibrance=intensity=0.25,unsharp=5:5:0.6",
}


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stderr[-3000:])
        sys.exit(f"ffmpeg falló: {' '.join(cmd[:6])}...")
    return r


def probe(path):
    out = run(["ffprobe", "-v", "error", "-show_entries",
               "format=duration:stream=codec_type,r_frame_rate,color_transfer",
               "-of", "json", path]).stdout
    j = json.loads(out)
    fps, hdr = 30.0, False
    for s in j.get("streams", []):
        if s.get("codec_type") == "video":
            n, d = s["r_frame_rate"].split("/")
            fps = float(n) / float(d or 1)
            hdr = s.get("color_transfer") in ("arib-std-b67", "smpte2084")
    return float(j["format"]["duration"]), fps, hdr


# ---------------------------------------------------------------- ritmo
def detectar_beats(audio, inicio, dur):
    sr, hop = 22050, 256
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(inicio), "-t", str(dur), "-i", audio,
                          "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"], capture_output=True).stdout
    x = np.frombuffer(raw, dtype=np.float32)
    if len(x) < sr * 3:
        sys.exit("La canción es demasiado corta desde ese punto de inicio.")
    # envolvente de ataque: energía log en bandas, ventana corta centrada
    nfft = 1024
    xp = np.pad(x, (nfft // 2, nfft // 2))
    frames = 1 + (len(xp) - nfft) // hop
    idx = np.arange(nfft)[None, :] + hop * np.arange(frames)[:, None]
    spec = np.log1p(10 * np.abs(np.fft.rfft(xp[idx] * np.hanning(nfft), axis=1)))
    def norm(f):
        f = np.concatenate([[0], np.maximum(0, np.diff(f, axis=0)).sum(axis=1)])
        return (f - f.mean()) / (f.std() + 1e-9)
    graves = int(250 * nfft / sr)  # bombo y bajo: lo que marca el golpe
    flux = 2 * norm(spec[:, :graves]) + norm(spec)
    fr = sr / hop
    # tempo por autocorrelación (70-180 BPM), con preferencia suave por ~120
    ac = np.correlate(flux, flux, "full")[len(flux) - 1:]
    best, period = -1e9, None
    for bpm in np.arange(70, 180, 0.1):
        lag = 60 * fr / bpm
        l0 = int(lag)
        v = ac[l0] + (lag - l0) * (ac[l0 + 1] - ac[l0])
        v *= np.exp(-0.5 * (np.log2(bpm / 120) / 1.0) ** 2)
        if v > best:
            best, period = v, lag
    # ajuste fino conjunto de tempo y fase: rejilla que más golpes alinea
    bpm0 = 60 * fr / period
    t_env = np.arange(len(flux)) / fr
    best = (-1e9, bpm0, 0.0)
    for bpm in np.arange(bpm0 - 2, bpm0 + 2, 0.02):
        p = 60 / bpm
        n = int((t_env[-1] - p) / p)
        for fase in np.arange(0, p, 0.005):
            idx_b = np.rint((fase + p * np.arange(n)) * fr).astype(int)
            sc = flux[idx_b].sum()
            if sc > best[0]:
                best = (sc, bpm, fase)
    return best[1], best[2]
 # bpm, segundo del primer golpe


# ---------------------------------------------------------------- beat de prueba (phonk)
def generar_beat(bpm, dur, path):
    sr = 48000
    n = int(sr * (dur + 0.5))
    t = np.arange(n) / sr
    b = 60 / bpm
    y = np.zeros(n)
    rng = np.random.default_rng(1)

    def add(t0, sig):
        i = int(t0 * sr)
        if i < n:
            y[i:i + len(sig)] += sig[:n - i]

    def tt(d):
        return np.arange(int(d * sr)) / sr
    k = tt(0.45)
    kick = np.sin(2 * np.pi * (48 * k + 90 / 30 * (1 - np.exp(-30 * k)))) * np.exp(-6 * k)
    kick = np.tanh(2.5 * kick)
    c = tt(0.25)
    clap = rng.uniform(-1, 1, len(c)) * (np.exp(-25 * c) + 0.6 * np.exp(-25 * np.clip(c - 0.012, 0, None)))
    clap = np.convolve(clap, np.ones(3) / 3, "same")
    h = tt(0.06)
    hat = np.diff(rng.uniform(-1, 1, len(h) + 1)) * np.exp(-70 * h)
    cb = tt(0.22)
    notas = [0, 0, 3, 0, 5, 3, 0, -2]  # motivo de cencerro
    for j in range(int(dur / b) + 1):
        t0 = j * b
        if j % 8 not in (3,):
            add(t0, 0.9 * kick)
        if j % 2 == 1:
            add(t0, 0.45 * clap)
        for q in (0, 0.5):
            add(t0 + q * b, 0.12 * hat)
        f = 587 * 2 ** (notas[j % 8] / 12)
        bell = (np.sign(np.sin(2 * np.pi * f * cb)) + np.sign(np.sin(2 * np.pi * f * 1.48 * cb))) * np.exp(-14 * cb)
        add(t0 + 0.5 * b, 0.09 * bell)
    y = np.tanh(1.3 * y)
    y = (y / (np.abs(y).max() + 1e-9) * 0.9 * 32767).astype("<i2")
    import wave
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(y.tobytes())


# ---------------------------------------------------------------- plan de cortes
PATRON = ["punch", "flash", "punch", "glitch", "stutter", "punch", "flash", "ramp",
          "punch", "glitch", "punch", "stutter", "flash", "punch", "ramp"]


def planificar(total, unidad):
    """Lista de (tipo, duración). El primero es el gancho (2 golpes)."""
    plan = [("hook", 2 * unidad)]
    t, i = plan[0][1], 0
    while t < total - unidad * 0.5:
        tipo = PATRON[i % len(PATRON)]
        i += 1
        if tipo == "ramp":
            d = 2 * unidad
        elif tipo == "stutter":
            plan += [("stutter", unidad / 2), ("stutter2", unidad / 2)]
            t += unidad
            continue
        else:
            d = unidad
        if t + d > total + 0.01:
            d = unidad
        plan.append((tipo, d))
        t += d
    return plan


# ---------------------------------------------------------------- render de un corte
def zoompan(tipo, frames):
    if tipo in ("hook",):
        z = f"1.06+0.0025*on+0.22*exp(-on/4)"
        sh = 18
    elif tipo == "hold":  # plano final: empuje lento y limpio
        z = f"1.04+0.0022*on"
        sh = 0
    elif tipo == "ramp":
        z = f"1.05+0.0035*on"
        sh = 0
    elif tipo == "glitch":
        z = f"1.12+0.15*exp(-on/2)"
        sh = 30
    elif tipo == "stutter2":
        z = f"1.25+0.0*on"
        sh = 10
    else:  # punch / flash / stutter
        z = f"1.06+0.002*on+0.20*exp(-on/3)"
        sh = 22
    x = f"iw/2-(iw/zoom/2)+{sh}*sin(on*2.3)*exp(-on/5)"
    y = f"ih/2-(ih/zoom/2)+{sh}*cos(on*1.7)*exp(-on/5)"
    return f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={W}x{H}:fps={FPS}"


def render_corte(i, tipo, dur, src, ss, look, tmp, interp, hdr=False):
    tm = TONEMAP if hdr else ""
    out = os.path.join(tmp, f"c{i:03d}.mp4")
    frames = max(1, round(dur * FPS))
    base = f"scale={BW}:{BH}:force_original_aspect_ratio=increase,crop={BW}:{BH},setsar=1"
    efectos = []
    if tipo in ("flash", "hook"):
        efectos.append("fade=t=in:st=0:d=0.14:color=white")
    if tipo in ("punch", "flash", "hook", "stutter", "stutter2"):
        efectos.append("rgbashift=rh=-14:bh=14:enable='lt(t,0.12)'")
    if tipo == "glitch":
        efectos += ["rgbashift=rh=-34:gv=10:bh=34:bv=-8:enable='lt(t,0.2)+between(t,0.3,0.36)'",
                    "noise=alls=45:allf=t:enable='lt(t,0.2)'",
                    "eq=brightness=0.12:enable='between(t,0.04,0.08)'"]
    grano = "noise=alls=7:allf=t"
    fin = f"{look},{','.join(efectos) + ',' if efectos else ''}{grano},vignette=PI/5,format=yuv420p"

    if tipo == "ramp":
        # 40% del corte a 2x, 60% en cámara lenta 0.4x con interpolación de movimiento
        a_out, b_out = dur * 0.4, dur * 0.6
        a_src, b_src = a_out * 2, b_out * 0.4
        slow = (f"minterpolate=fps={int(FPS / 0.4)}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1,"
                if interp else f"fps={int(FPS / 0.4)},")
        fc = (f"[0:v]{tm}scale=-2:{H}:force_original_aspect_ratio=increase,split[a][b];"
              f"[a]trim=0:{a_src:.3f},setpts=(PTS-STARTPTS)/2,fps={FPS}[va];"
              f"[b]trim={a_src:.3f}:{a_src + b_src:.3f},setpts=PTS-STARTPTS,fps=30,{slow}"
              f"setpts=2.5*PTS,fps={FPS}[vb];"
              f"[va][vb]concat=n=2:v=1:a=0,{base},{zoompan(tipo, frames)},"
              f"eq=brightness='0.7*gte(t,{a_out:.3f})*exp(-max(0,t-{a_out:.3f})*22)':eval=frame,{fin},trim=duration={dur:.3f}[v]")
        src_len = a_src + b_src + 0.2
    else:
        fc = (f"[0:v]{tm}setpts=PTS-STARTPTS,fps={FPS},{base},tpad=stop_mode=clone:stop_duration={dur:.3f},"
              f"{zoompan(tipo, frames)},{fin},trim=duration={dur:.3f}[v]")
        src_len = dur + 0.2
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{ss:.3f}", "-t", f"{src_len:.3f}", "-i", src,
         "-filter_complex", fc, "-map", "[v]", "-frames:v", str(frames), "-an",
         "-c:v", "libx264", "-preset", "medium", "-crf", "14", "-r", str(FPS), out])
    return out


def _linea(tmp, nombre, txt, t0, t1, y, size):
    size = min(size, int(W * 0.72 / (0.64 * max(1, len(txt)))))  # con el pop nunca toca bordes
    p = os.path.join(tmp, nombre + ".txt")
    open(p, "w", encoding="utf-8").write(txt)
    fs = f"{size}*(1+0.28*exp(-max(0,t-{t0:.3f})*14))"  # pop: entra grande y se asienta
    return (f"drawtext=fontfile={FONT}:textfile={p}:fontsize='{fs}':fontcolor=white"
            f":borderw={max(6, size // 11)}:bordercolor=black"
            f":shadowcolor=black@0.55:shadowx=0:shadowy=10"
            f":x=(w-text_w)/2:y={y}-text_h/2:enable='between(t,{t0:.3f},{t1:.3f})'")


def texto(tmp, nombre, txt, t0, t1, y, size):
    """Texto grande; si es largo, en dos líneas (la 2ª entra medio golpe después)."""
    txt = " ".join("".join(c for c in txt if ord(c) < 0x2190 or 0x2E80 <= ord(c) < 0x1F000).split())
    pal = txt.split(" ")
    if len(txt) <= 8 or len(pal) < 2:
        return _linea(tmp, nombre, txt, t0, t1, f"h*{y}", size)
    cortes = [abs(len(" ".join(pal[:i])) - len(" ".join(pal[i:]))) for i in range(1, len(pal))]
    i = 1 + cortes.index(min(cortes))
    l1, l2 = " ".join(pal[:i]), " ".join(pal[i:])
    size = min(size, int(W * 0.72 / (0.64 * max(len(l1), len(l2)))))
    return (_linea(tmp, nombre + "1", l1, t0, t1, f"h*{y}-{size * 0.62:.0f}", size) + "," +
            _linea(tmp, nombre + "2", l2, t0 + 0.2, t1, f"h*{y}+{size * 0.62:.0f}", size))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clips", nargs="+")
    ap.add_argument("-o", "--salida", default="reel.mp4")
    ap.add_argument("-d", "--duracion", type=float, default=15)
    ap.add_argument("-m", "--musica", default="", help="mp3/m4a/mp4 (de un vídeo también vale)")
    ap.add_argument("--musica-inicio", type=float, default=0, help="segundo de la canción donde empezar")
    ap.add_argument("--bpm", type=float, default=0, help="forzar tempo (si no hay música o falla la detección)")
    ap.add_argument("--beat-demo", action="store_true", help="beat sintético de prueba si no hay música")
    ap.add_argument("--ritmo", type=int, choices=[1, 2], default=1,
                    help="1 = corte en cada golpe (frenético), 2 = cada dos golpes (más calmado)")
    ap.add_argument("-t", "--texto", default="")
    ap.add_argument("--cierre", default="")
    ap.add_argument("--look", choices=LOOKS, default="cine")
    ap.add_argument("--sin-interpolar", action="store_true", help="cámara lenta más rápida de renderizar")
    ap.add_argument("--plan", default="", help='JSON: [[clip, segundo, "tipo", golpes], ...] '
                    '(tipos: hook punch flash glitch stutter ramp hold)')
    ap.add_argument("--semilla", type=int, default=7, help="cambia para otra combinación de tomas")
    a = ap.parse_args()
    random.seed(a.semilla)

    # --- ritmo
    if a.musica and not a.bpm:
        bpm, fase = detectar_beats(a.musica, a.musica_inicio, a.duracion + 8)
        audio_ini = a.musica_inicio + fase
    else:
        bpm, audio_ini = (a.bpm or 120), a.musica_inicio
    beat = 60 / bpm
    unidad = beat * a.ritmo
    while unidad < 0.42:
        unidad *= 2
    print(f"♪ {bpm:.1f} BPM -> un corte cada {unidad:.2f} s")

    manual = None
    if a.plan:
        manual = []
        for c, ss, tipo, g in json.load(open(a.plan)):
            if tipo == "stutter":
                manual += [(c, ss, "stutter", g * unidad / 2), (c, ss, "stutter2", g * unidad / 2)]
            else:
                manual.append((c, ss, tipo, g * unidad))
        plan = [(t, d) for _, _, t, d in manual]
    else:
        plan = planificar(a.duracion, unidad)
    total = sum(d for _, d in plan)

    # --- clips y ventanas de origen (se recorre cada clip sin repetir tomas)
    clips = []
    for spec in a.clips:
        path, _, st = spec.partition("@")
        if not os.path.exists(path):
            sys.exit(f"No encuentro {path}")
        dur, fps, hdr = probe(path)
        clips.append({"path": path, "dur": dur, "hdr": hdr,
                      "cursor": float(st) if st else min(0.5, dur * 0.1)})
    orden = list(range(len(clips)))
    random.shuffle(orden)

    tmp = tempfile.mkdtemp(prefix="brutal_")
    partes, ultimo = [], None
    k = 0
    for i, (tipo, d) in enumerate(plan):
        if manual:
            c, ss = clips[manual[i][0]], manual[i][1]
        elif tipo == "stutter2":  # repite la misma toma: efecto tartamudeo
            c, ss = ultimo
        else:
            c = clips[orden[k % len(orden)]]
            k += 1
            need = d * (1.04 if tipo == "ramp" else 1) + 0.3
            if c["cursor"] + need > c["dur"]:
                c["cursor"] = 0.0 if c["dur"] > need else 0.0
            ss = c["cursor"]
            c["cursor"] += need * 1.3  # salta un poco: siguiente repetición/ángulo
        ultimo = (c, ss)
        print(f"  [{i + 1:02d}/{len(plan)}] {tipo:8s} {d:.2f}s  {os.path.basename(c['path'])} @{ss:.1f}s")
        partes.append(render_corte(i, tipo, d, c["path"], ss, LOOKS[a.look], tmp,
                                   not a.sin_interpolar, c["hdr"]))

    lista = os.path.join(tmp, "lista.txt")
    open(lista, "w").write("".join(f"file '{p}'\n" for p in partes))
    union = os.path.join(tmp, "union.mp4")
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lista, "-c", "copy", union])

    # --- textos + audio
    capas = []
    if a.texto:
        capas.append(texto(tmp, "hook", a.texto, 0.0, plan[0][1] + unidad, 0.24, 130))
    if a.cierre:
        capas.append(texto(tmp, "cierre", a.cierre, total - 2 * unidad, total, 0.5, 130))
    capas.append(f"fade=t=out:st={total - 0.25:.3f}:d=0.25")
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", union]
    if a.musica:
        cmd += ["-ss", f"{audio_ini:.3f}", "-i", a.musica]
        af = f"[1:a]atrim=duration={total:.3f},afade=t=out:st={total - 0.8:.3f}:d=0.8,loudnorm=I=-12:TP=-1[a]"
    elif a.beat_demo:
        wav = os.path.join(tmp, "beat.wav")
        generar_beat(bpm, total, wav)
        cmd += ["-i", wav]
        af = f"[1:a]atrim=duration={total:.3f},afade=t=out:st={total - 0.8:.3f}:d=0.8,loudnorm=I=-12:TP=-1[a]"
    else:
        cmd += ["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo"]
        af = f"[1:a]atrim=duration={total:.3f}[a]"
    cmd += ["-filter_complex", f"[0:v]{','.join(capas)}[v];{af}", "-map", "[v]", "-map", "[a]",
            "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-profile:v", "high",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart",
            a.salida]
    run(cmd)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"✅ {a.salida}  {total:.1f}s  {bpm:.0f} BPM  look {a.look}")


if __name__ == "__main__":
    main()
