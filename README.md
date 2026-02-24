# Claude Pro y Cursor — Uso en vivo en terminal

CLI que muestra en **tiempo real** en tu terminal el uso de **Claude Pro** (límite 5h/7d) y de **Cursor** (premium requests). Dos vistas en un mismo recuadro; **flechas izquierda y derecha** cambian entre Claude Pro y Cursor.

- **Claude Pro**: mismas credenciales y API que el comando `/usage` de Claude Code (Keychain). No lee el navegador ni cookies.
- **Cursor**: lee el token desde la base de datos local de Cursor (solo macOS) y llama a `cursor.com/api/usage`.

## Requisitos

- **macOS** (Keychain para Claude; ruta fija de Cursor para la pestaña Cursor)
- **Claude Pro**: haber iniciado sesión en **Claude Code** al menos una vez (`claude` en la terminal → OAuth en el navegador)
- **Cursor**: haber abierto **Cursor** al menos una vez e iniciado sesión (para que exista `state.vscdb` con token)
- **Python 3.8+** (solo biblioteca estándar, no hace falta `pip install`)

El token de Claude debe tener el scope `user:profile` (el login por navegador en Claude Code lo da; `claude setup-token` no). Si solo usas `setup-token`, borra la entrada del Keychain, cierra todas las sesiones de `claude` y vuelve a abrirlo para hacer login por navegador.

## Uso

```bash
python3 claude-usage-live.py
```

Verás un recuadro con la vista activa (por defecto **Claude Pro**). Los datos se actualizan cada 30 segundos.

- **Flecha derecha (→)**: pasar a la vista **Cursor** (o de Cursor a Claude).
- **Flecha izquierda (←)**: pasar a la vista **Claude Pro** (o de Claude a Cursor).
- **Ctrl+C**: salir.

Ejemplo (vista Claude Pro):

```
┌───────────────────────── Claude Pro ─────────────────────────┐
│                                                              │
│  5h   ██████░░░░░░  62%   ~3h 45m (14:00)                    │
│  7d   ███░░░░░░░░░  28%   ~5d 2h                             │
│                                                              │
└──────────────────────────── 14:32 ───────────────────────────┘
```

En la vista **Cursor** se muestra la barra de premium requests (p. ej. GPT-4) y el inicio del período de facturación.

## Cambio de vista

- Las **flechas ← y →** cambian entre **Claude Pro** y **Cursor** sin hacer un nuevo fetch; se redibuja con los últimos datos en caché.
- La pestaña **Cursor** solo está disponible en **macOS** (usa `~/Library/Application Support/Cursor/User/globalStorage/state.vscdb`). En otros sistemas, al cambiar a Cursor se muestra «Cursor solo en macOS».
- Si no has iniciado sesión en Cursor, al cambiar a esa vista verás un mensaje indicando que abras Cursor e inicies sesión.

## Cómo funciona

- **Claude Pro**: lee el **accessToken** de Claude Code desde el Keychain (entrada `Claude Code-credentials`) y llama a `GET https://api.anthropic.com/api/oauth/usage` con `Authorization: Bearer <token>` y el header `anthropic-beta: oauth-2025-04-20`. No usa Chrome ni cookies.
- **Cursor**: lee el token desde la base de datos SQLite de Cursor (`state.vscdb`), forma el session token y llama a `GET https://cursor.com/api/usage?user=<user_id>` con la cookie `WorkosCursorSessionToken`. Solo en macOS.

## Si no ves datos

**Claude Pro**

1. Abre `claude` en la terminal y asegúrate de estar logueado (si no, te pedirá OAuth en el navegador).
2. Si el script dice que no hay credenciales, cierra todas las ventanas/sesiones de `claude`, vuelve a abrirlo e inicia sesión de nuevo.
3. Si la API devuelve error de scope (`user:profile`), la entrada del Keychain puede ser antigua o de `setup-token`. Borra la entrada y vuelve a hacer login por navegador:
   ```bash
   security delete-generic-password -s "Claude Code-credentials"
   ```
   Luego cierra todo `claude` y ábrelo de nuevo para que te pida login otra vez.

**Cursor**

- Asegúrate de estar en macOS y de haber abierto Cursor al menos una vez con la cuenta iniciada. El token se guarda en `~/Library/Application Support/Cursor/User/globalStorage/state.vscdb`.
- Si ves «No hay token en Cursor», abre Cursor, inicia sesión en la cuenta y vuelve a ejecutar el script.
