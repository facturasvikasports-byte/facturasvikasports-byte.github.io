# Visor de fotos para iPad

Muestra en los iPad, de forma fluida, las fotos que van llegando a una carpeta del ordenador Windows.

## Cómo funciona
- El ordenador crea **copias ligeras** de cada foto (unos 300 KB en vez de 10–25 MB). Los originales no se tocan.
- Los iPad abren una **galería en Safari** que precarga las fotos siguientes, así que deslizar es instantáneo.
- Las fotos nuevas aparecen solas en los iPad a los pocos segundos de caer en la carpeta.
- Funciona **sin internet**, solo con el router.

## Instalación (una sola vez)
1. Instala Python desde https://www.python.org/downloads/ y **marca "Add python.exe to PATH"** durante la instalación.
2. Copia la carpeta `visor-fotos` al ordenador (por ejemplo al Escritorio).

## Uso
1. Haz doble clic en **`INICIAR VISOR.bat`**.
2. La primera vez te pedirá la ruta de la carpeta de fotos (por ejemplo `C:\Users\TuNombre\Pictures\Evento`). Se recuerda para las siguientes veces. Para cambiarla, borra el archivo `carpeta.txt`.
3. Si Windows pregunta por el **Firewall**, pulsa **Permitir** (red privada).
4. La ventana mostrará una dirección como `http://192.168.1.50:8000`. Escríbela en **Safari** en cada iPad.
5. Recomendado en cada iPad: botón **Compartir → Añadir a pantalla de inicio**. Así se abre a pantalla completa como una app.

No cierres la ventana negra mientras se usen los iPad.

## En los iPad
- Toca una foto para verla en grande.
- Desliza a izquierda o derecha para pasar de foto.
- Desliza hacia abajo o pulsa **Cerrar** para volver a la galería.
- Las más recientes salen siempre arriba.

## Para que la red vaya al máximo
- Ordenador conectado al router **por cable**.
- iPads en la red **5 GHz**; router cerca y solo para este sistema.
- Para que el iPad no se bloquee: Ajustes → Pantalla y brillo → Bloqueo automático → Nunca.
- Para que el cliente no salga de la galería: Ajustes → Accesibilidad → **Acceso guiado**.

## Notas
- Formatos: JPG, PNG y WEBP. Los RAW (NEF, CR2…) no se muestran: exporta o dispara en RAW+JPG.
- También lee las subcarpetas.
- La carpeta `cache` guarda las copias ligeras; se puede borrar al terminar el evento.
