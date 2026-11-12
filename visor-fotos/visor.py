# -*- coding: utf-8 -*-
"""
Visor de fotos fluido para iPad (red local).

- Vigila una carpeta del ordenador donde caen las fotos.
- Crea automáticamente copias ligeras (miniatura + vista de pantalla).
- Sirve una galería web que los iPad abren en Safari y que precarga
  las fotos siguientes para que pasar de una a otra sea instantáneo.

Uso:  python visor.py "C:\\Ruta\\A\\Mis Fotos"
      (si no se indica carpeta, se pregunta la primera vez y se recuerda)
"""
import hashlib
import json
import os
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image, ImageOps

# ----------------------------- Ajustes ---------------------------------
PUERTO = 8000
LADO_VISTA = 2048       # px del lado largo de la foto a pantalla completa
CALIDAD_VISTA = 82
LADO_MINI = 720         # px del lado largo de la miniatura (nítida en pantalla Retina)
CALIDAD_MINI = 80
INTERVALO_ESCANEO = 2   # segundos entre revisiones de la carpeta
EXTENSIONES = {".jpg", ".jpeg", ".png", ".webp"}
# -----------------------------------------------------------------------

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "cache")
ARCHIVO_CARPETA = os.path.join(BASE, "carpeta.txt")
HTML = os.path.join(BASE, "galeria.html")

fotos = {}            # id -> {"nombre", "t"}  (solo las ya preparadas)
fotos_lock = threading.Lock()
version = 0           # cambia cada vez que la lista cambia
pendientes = {}       # ruta -> (tamaño, mtime) visto en el escaneo anterior
en_proceso = set()
trabajadores = ThreadPoolExecutor(max_workers=max(2, (os.cpu_count() or 4) - 1))


def obtener_carpeta():
    if len(sys.argv) > 1:
        carpeta = sys.argv[1]
    elif os.path.exists(ARCHIVO_CARPETA):
        with open(ARCHIVO_CARPETA, encoding="utf-8") as f:
            carpeta = f.read().strip()
    else:
        print("Escribe o pega la ruta de la carpeta donde se guardan las fotos")
        print(r"(por ejemplo  C:\Users\TuNombre\Pictures\Evento)")
        carpeta = input("> ").strip()
    carpeta = carpeta.strip('"').strip()
    if not os.path.isdir(carpeta):
        print(f"\nNo encuentro la carpeta: {carpeta}")
        if os.path.exists(ARCHIVO_CARPETA):
            os.remove(ARCHIVO_CARPETA)
        input("Pulsa Enter para salir y vuelve a intentarlo.")
        sys.exit(1)
    with open(ARCHIVO_CARPETA, "w", encoding="utf-8") as f:
        f.write(carpeta)
    return carpeta


def id_de(ruta, tam, mtime):
    return hashlib.sha1(f"{ruta}|{tam}|{mtime}".encode("utf-8")).hexdigest()[:20]


def guardar_reducida(img, lado, calidad, destino):
    copia = img.copy()
    copia.thumbnail((lado, lado), Image.LANCZOS)
    tmp = destino + ".tmp"
    copia.save(tmp, "JPEG", quality=calidad, optimize=True, progressive=True)
    os.replace(tmp, destino)


def preparar(ruta, fid, mtime):
    global version
    try:
        vista = os.path.join(CACHE, fid + "_v.jpg")
        mini = os.path.join(CACHE, fid + "_m.jpg")
        if not (os.path.exists(vista) and os.path.exists(mini)):
            with Image.open(ruta) as img:
                if img.format == "JPEG":
                    img.draft("RGB", (LADO_VISTA, LADO_VISTA))  # decodifica más rápido
                img = ImageOps.exif_transpose(img).convert("RGB")
                guardar_reducida(img, LADO_VISTA, CALIDAD_VISTA, vista)
                guardar_reducida(img, LADO_MINI, CALIDAD_MINI, mini)
        with fotos_lock:
            fotos[fid] = {"nombre": os.path.basename(ruta), "t": mtime}
            version += 1
    except Exception as e:  # archivo dañado o aún copiándose
        print(f"  No se pudo preparar {os.path.basename(ruta)}: {e}")
    finally:
        en_proceso.discard(ruta)


def escanear(carpeta):
    global version
    vistos = set()
    for raiz, dirs, archivos in os.walk(carpeta):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for nombre in archivos:
            if os.path.splitext(nombre)[1].lower() not in EXTENSIONES:
                continue
            ruta = os.path.join(raiz, nombre)
            try:
                st = os.stat(ruta)
            except OSError:
                continue
            firma = (st.st_size, st.st_mtime)
            fid = id_de(ruta, *firma)
            vistos.add(fid)
            if fid in fotos or ruta in en_proceso:
                continue
            # Solo se procesa cuando el archivo ya no está cambiando (copia terminada)
            if pendientes.get(ruta) == firma and time.time() - st.st_mtime > 1:
                pendientes.pop(ruta, None)
                en_proceso.add(ruta)
                trabajadores.submit(preparar, ruta, fid, st.st_mtime)
            else:
                pendientes[ruta] = firma
    with fotos_lock:
        borradas = [f for f in fotos if f not in vistos]
        for f in borradas:
            del fotos[f]
        if borradas:
            version += 1


def bucle_escaneo(carpeta):
    while True:
        try:
            escanear(carpeta)
        except Exception as e:
            print("Error revisando la carpeta:", e)
        time.sleep(INTERVALO_ESCANEO)


class Manejador(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass  # silencio en la consola

    def enviar(self, cuerpo, tipo, cache="no-store"):
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self):
        ruta = self.path.split("?")[0]
        if ruta in ("/", "/index.html"):
            with open(HTML, "rb") as f:
                self.enviar(f.read(), "text/html; charset=utf-8")
        elif ruta == "/api/fotos":
            with fotos_lock:
                lista = sorted(({"id": k, **v} for k, v in fotos.items()),
                               key=lambda x: x["t"], reverse=True)
                datos = {"version": version, "fotos": lista}
            self.enviar(json.dumps(datos).encode("utf-8"), "application/json")
        elif ruta.startswith(("/v/", "/m/")):
            fid = ruta[3:].removesuffix(".jpg")
            if not (fid.isalnum() and fid in fotos):
                return self.send_error(404)
            sufijo = "_v.jpg" if ruta[1] == "v" else "_m.jpg"
            try:
                with open(os.path.join(CACHE, fid + sufijo), "rb") as f:
                    datos = f.read()
            except OSError:
                return self.send_error(404)
            # El id cambia si la foto cambia, así que el iPad la puede guardar para siempre
            self.enviar(datos, "image/jpeg", "public, max-age=31536000, immutable")
        else:
            self.send_error(404)


def ips_locales():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # no envía nada, solo elige la interfaz
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


def main():
    carpeta = obtener_carpeta()
    os.makedirs(CACHE, exist_ok=True)
    print(f"\nCarpeta de fotos: {carpeta}")
    print("Preparando fotos (la primera vez con muchas fotos puede tardar un poco)...")
    threading.Thread(target=bucle_escaneo, args=(carpeta,), daemon=True).start()

    servidor = ThreadingHTTPServer(("0.0.0.0", PUERTO), Manejador)
    print("\n" + "=" * 56)
    print("  VISOR EN MARCHA. En cada iPad abre Safari y escribe:")
    for ip in ips_locales() or ["(IP de este ordenador)"]:
        print(f"      http://{ip}:{PUERTO}")
    print("  No cierres esta ventana mientras se usen los iPad.")
    print("=" * 56 + "\n")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
