# Claude Pro — Uso en vivo en terminal

CLI que muestra en **tiempo real** en tu terminal el uso de tu cuenta **Claude Pro** (límite de 5h y 7d), **usando las mismas credenciales y la misma API que el comando `/usage` de Claude Code**. No lee el navegador ni cookies de Chrome.

## Requisitos

- **macOS** (usa el Keychain donde Claude Code guarda tus credenciales)
- Haber iniciado sesión en **Claude Code** al menos una vez (`claude` en la terminal → OAuth en el navegador)
- **Python 3.8+** (solo biblioteca estándar, no hace falta `pip install`)

El token debe tener el scope `user:profile` (el login por navegador en Claude Code lo da; `claude setup-token` no). Si solo usas `setup-token`, borra la entrada del Keychain, cierra todas las sesiones de `claude` y vuelve a abrirlo para hacer login por navegador.

## Uso

```bash
python3 claude-usage-live.py
```

Verás algo como:

```
Claude Pro — Uso en vivo (misma API que /usage)
Credenciales: Claude Code (Keychain) · Actualizando cada 30 s · 14:32:01

  5h (sesión)   ██████░░░░░░  62%   Resetea ~3h 45m
  7d (semana)   ███░░░░░░░░░  28%   Resetea ~5d 2h

Ctrl+C para salir
```

El script actualiza los datos cada 30 segundos. Para salir: **Ctrl+C**.

## Cómo funciona

- Lee el **accessToken** de Claude Code desde el Keychain (entrada `Claude Code-credentials`).
- Llama a **la misma API** que usa el comando `/usage`: `GET https://api.anthropic.com/api/oauth/usage` con `Authorization: Bearer <token>` y el header `anthropic-beta: oauth-2025-04-20`.
- No usa Chrome ni cookies; todo se basa en lo que ya tiene Claude Code tras hacer login.

## Si no ves datos

1. Abre `claude` en la terminal y asegúrate de estar logueado (si no, te pedirá OAuth en el navegador).
2. Si el script dice que no hay credenciales, cierra todas las ventanas/sesiones de `claude`, vuelve a abrirlo e inicia sesión de nuevo.
3. Si la API devuelve error de scope (`user:profile`), la entrada del Keychain puede ser antigua o de `setup-token`. Borra la entrada y vuelve a hacer login por navegador:
   ```bash
   security delete-generic-password -s "Claude Code-credentials"
   ```
   Luego cierra todo `claude` y ábrelo de nuevo para que te pida login otra vez.
