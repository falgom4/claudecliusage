#!/usr/bin/env python3
"""CLI que muestra en tiempo real el uso de tu cuenta Claude Pro en la terminal.

Usa las mismas credenciales que Claude Code (Keychain) y la misma API que
el comando /usage. No lee el navegador ni cookies de Chrome.

Requisitos: macOS, haber iniciado sesión en Claude Code (claude) al menos una vez.
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

# =============================================================================
# Constantes
# =============================================================================

USAGE_API_URL = "https://api.anthropic.com/api/oauth/usage"
OAUTH_BETA_HEADER = "oauth-2025-04-20"
POLL_INTERVAL = 30  # segundos
KEYCHAIN_SERVICE = "Claude Code-credentials"
KEYCHAIN_TIMEOUT = 30

RESET = "\033[0m"
BAR_WIDTH = 12
BAR_FILLED = "\u2588"
BAR_EMPTY = "\u2591"

GREEN = "\033[38;2;166;227;161m"
YELLOW = "\033[38;2;249;226;175m"
PEACH = "\033[38;2;250;179;135m"
RED = "\033[38;2;243;139;168m"
DIM = "\033[38;2;127;132;156m"
ACCENT = "\033[38;2;180;190;254m"
LABEL = "\033[38;2;166;173;200m"


def color_for_pct(pct):
    pct = int(pct)
    if pct >= 90:
        return RED
    if pct >= 70:
        return PEACH
    if pct >= 50:
        return YELLOW
    return GREEN


# =============================================================================
# Credenciales (Claude Code en Keychain)
# =============================================================================

def get_claude_code_token():
    """Obtiene el accessToken de Claude Code desde el Keychain (macOS)."""
    if sys.platform != "darwin":
        return None, "Solo macOS (usa Keychain de Claude Code)."
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            timeout=KEYCHAIN_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return None, "Keychain no respondió. Ejecuta el script en tu terminal y acepta el permiso si macOS lo pide."
    except FileNotFoundError:
        return None, "Comando 'security' no encontrado (solo macOS)."
    if result.returncode != 0 or not result.stdout.strip():
        return None, (
            "No hay credenciales de Claude Code. Abre 'claude' en la terminal, "
            "inicia sesión (OAuth en el navegador) y vuelve a ejecutar este script."
        )
    try:
        creds = json.loads(result.stdout.strip())
        oauth = creds.get("claudeAiOauth") or {}
        token = (oauth.get("accessToken") or "").strip()
        if not token or token == "null":
            return None, (
                "El Keychain no tiene accessToken de Claude Code. "
                "Cierra todas las sesiones de 'claude', vuelve a abrirlo e inicia sesión."
            )
        return token, None
    except json.JSONDecodeError:
        return None, "Credenciales en Keychain no son JSON válido. Re-inicia sesión en 'claude'."


# =============================================================================
# API de uso (la misma que /usage en Claude Code)
# =============================================================================

def fetch_usage(access_token):
    """Llama a la API de uso de Anthropic (OAuth)."""
    req = urllib.request.Request(
        USAGE_API_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "anthropic-beta": OAUTH_BETA_HEADER,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()), None
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            err = json.loads(body)
            msg = err.get("error", {}).get("message", body)
        except json.JSONDecodeError:
            msg = body or str(e)
        return None, msg
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        return None, str(e)


# =============================================================================
# Formateo y pantalla
# =============================================================================

def format_reset_time(resets_at):
    if not resets_at:
        return ""
    try:
        # Ej: "2026-02-08T04:59:59.000000+00:00" -> "2026-02-08T04:59:59+00:00"
        ts = resets_at.strip().replace("Z", "+00:00")
        if "." in ts:
            ts = ts.split(".")[0] + "+00:00"
        reset_dt = datetime.fromisoformat(ts)
        now = datetime.now(timezone.utc)
        diff = (reset_dt - now).total_seconds()
        if diff < 0:
            return "reseteando"
        hours = int(diff // 3600)
        minutes = int((diff % 3600) // 60)
        if hours >= 24:
            days = hours // 24
            rem_hours = hours % 24
            return f"~{days}d {rem_hours}h"
        return f"~{hours}h {minutes:02d}m"
    except Exception:
        return ""


def bar(pct):
    pct = int(pct)
    filled = max(0, min(BAR_WIDTH, pct * BAR_WIDTH // 100))
    c = color_for_pct(pct)
    return f"{c}{BAR_FILLED * filled}{DIM}{BAR_EMPTY * (BAR_WIDTH - filled)}{RESET}"


def render_screen(usage, last_update, error_msg=None):
    """Limpia la pantalla y muestra el panel de uso."""
    print("\033[2J\033[H", end="")
    print(f"{ACCENT}Claude Pro — Uso en vivo (misma API que /usage){RESET}")
    print(f"{DIM}Credenciales: Claude Code (Keychain) · Actualizando cada {POLL_INTERVAL} s · {last_update}{RESET}\n")
    if error_msg:
        print(f"{RED}{error_msg}{RESET}\n")
    if usage:
        five = usage.get("five_hour") or {}
        seven = usage.get("seven_day") or {}
        p5 = int(float(five.get("utilization", 0)))
        p7 = int(float(seven.get("utilization", 0)))
        r5 = format_reset_time(five.get("resets_at"))
        r7 = format_reset_time(seven.get("resets_at"))
        print(f"  {LABEL}5h (sesión){RESET}   {bar(p5)} {color_for_pct(p5)}{p5}%{RESET}   Resetea {DIM}{r5}{RESET}")
        print(f"  {LABEL}7d (semana){RESET}   {bar(p7)} {color_for_pct(p7)}{p7}%{RESET}   Resetea {DIM}{r7}{RESET}")
    elif not error_msg:
        print(f"  {DIM}Cargando uso...{RESET}")
    print(f"\n{DIM}Ctrl+C para salir{RESET}")


# =============================================================================
# Bucle principal
# =============================================================================

def main():
    last_update = datetime.now().strftime("%H:%M:%S")
    token, err = get_claude_code_token()
    if err:
        render_screen(None, last_update, err)
        try:
            while True:
                time.sleep(POLL_INTERVAL)
                token, err = get_claude_code_token()
                last_update = datetime.now().strftime("%H:%M:%S")
                if not err and token:
                    usage, api_err = fetch_usage(token)
                    if api_err:
                        render_screen(None, last_update, api_err)
                    else:
                        render_screen(usage, last_update, None)
                else:
                    render_screen(None, last_update, err)
        except KeyboardInterrupt:
            print("\n")
            sys.exit(0)

    usage, api_err = fetch_usage(token)
    if api_err:
        render_screen(None, last_update, api_err)
    else:
        render_screen(usage, last_update, None)

    try:
        while True:
            time.sleep(POLL_INTERVAL)
            token, err = get_claude_code_token()
            last_update = datetime.now().strftime("%H:%M:%S")
            if err:
                render_screen(None, last_update, err)
                continue
            usage, api_err = fetch_usage(token)
            if api_err:
                render_screen(None, last_update, api_err)
            else:
                render_screen(usage, last_update, None)
    except KeyboardInterrupt:
        print("\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
