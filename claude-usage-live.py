#!/usr/bin/env python3
"""CLI que muestra en tiempo real el uso de tu cuenta Claude Pro en la terminal.

Usa las mismas credenciales que Claude Code (Keychain) y la misma API que
el comando /usage. No lee el navegador ni cookies de Chrome.

Requisitos: macOS, haber iniciado sesión en Claude Code (claude) al menos una vez.
"""

import json
import os
import re
import signal
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
CURSOR_HIDE = "\033[?25l"
CURSOR_SHOW = "\033[?25h"
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


def format_reset_at_local(resets_at):
    """Hora del próximo corte en hora local (HH:MM)."""
    if not resets_at:
        return ""
    try:
        ts = resets_at.strip().replace("Z", "+00:00")
        if "." in ts:
            ts = ts.split(".")[0] + "+00:00"
        reset_dt = datetime.fromisoformat(ts)
        local = reset_dt.astimezone()
        return local.strftime("%H:%M")
    except Exception:
        return ""


def bar(pct):
    pct = int(pct)
    filled = max(0, min(BAR_WIDTH, pct * BAR_WIDTH // 100))
    c = color_for_pct(pct)
    return f"{c}{BAR_FILLED * filled}{DIM}{BAR_EMPTY * (BAR_WIDTH - filled)}{RESET}"


# Bordes del recuadro (estilo lazygit/btop)
BOX_TOP_LEFT = "\u250c"
BOX_TOP_RIGHT = "\u2510"
BOX_BOTTOM_LEFT = "\u2514"
BOX_BOTTOM_RIGHT = "\u2518"
BOX_H = "\u2500"
BOX_V = "\u2502"

ANSI_STRIP = re.compile(r"\033\[[0-9;]*m")


def visible_len(s):
    """Longitud visible del texto (sin contar códigos ANSI)."""
    return len(ANSI_STRIP.sub("", s))


def pad_to_width(s, width, align="left"):
    """Rellena la línea hasta `width` caracteres visibles. align: 'left', 'center', 'right'."""
    n = width - visible_len(s)
    if n <= 0:
        return s
    pad = " " * n
    if align == "center":
        left = n // 2
        return " " * left + s + " " * (n - left)
    if align == "right":
        return pad + s
    return s + pad


def get_box_width():
    """Ancho interior del recuadro (terminal - bordes)."""
    try:
        return os.get_terminal_size().columns - 2
    except OSError:
        return 78


def render_screen(usage, last_update, error_msg=None):
    """Limpia la pantalla y muestra el panel de uso dentro de un recuadro."""
    _state["usage"] = usage
    _state["last_update"] = last_update
    _state["error_msg"] = error_msg
    w = get_box_width()
    title_text = " Claude Pro "
    title_styled = ACCENT + title_text + RESET
    title_len = visible_len(title_styled)
    dash_total = max(0, w - title_len)
    left_dashes = dash_total // 2
    right_dashes = dash_total - left_dashes
    top = BOX_TOP_LEFT + BOX_H * left_dashes + title_styled + BOX_H * right_dashes + BOX_TOP_RIGHT
    bottom = BOX_BOTTOM_LEFT + BOX_H * w + BOX_BOTTOM_RIGHT

    def line(content, align="left"):
        return BOX_V + pad_to_width(content, w, align) + BOX_V

    print("\033[2J\033[H", end="")
    print()
    print(top)
    print(line(""))

    if error_msg:
        msg = RED + error_msg + RESET
        print(line(msg))
    elif usage:
        five = usage.get("five_hour") or {}
        seven = usage.get("seven_day") or {}
        extra = usage.get("extra_usage") or usage.get("extra_hour") or {}
        has_extra = extra and (extra.get("utilization") is not None or extra.get("resets_at"))

        p5 = int(float(five.get("utilization", 0)))
        p7 = int(float(seven.get("utilization", 0)))
        show_7d = p7 > 0
        r5 = format_reset_time(five.get("resets_at"))
        r7 = format_reset_time(seven.get("resets_at"))
        at5 = format_reset_at_local(five.get("resets_at"))
        r5_display = f"{r5} ({at5})" if at5 else r5
        # Ancho visible de una fila sin el reset:
        # "  5h   " + bar(12) + "  100%   " = 2+2+3+12+2+4+3 = 28
        # Si no cabe el reset en la misma línea, lo bajamos a la siguiente
        row_base_w = 2 + 2 + 3 + BAR_WIDTH + 2 + 4 + 3
        compact = w < row_base_w + max(len(r5_display), len(r7) if show_7d else 0)
        if compact:
            row5 = f"  {LABEL}5h{RESET}   {bar(p5)}  {color_for_pct(p5)}{p5}%{RESET}"
            reset5 = f"       {DIM}{r5_display}{RESET}"
            print(line(row5))
            print(line(reset5))
            if show_7d:
                row7 = f"  {LABEL}7d{RESET}   {bar(p7)}  {color_for_pct(p7)}{p7}%{RESET}"
                reset7 = f"       {DIM}{r7}{RESET}"
                print(line(row7))
                print(line(reset7))
        else:
            row5 = f"  {LABEL}5h{RESET}   {bar(p5)}  {color_for_pct(p5)}{p5}%{RESET}   {DIM}{r5_display}{RESET}"
            print(line(row5))
            if show_7d:
                row7 = f"  {LABEL}7d{RESET}   {bar(p7)}  {color_for_pct(p7)}{p7}%{RESET}   {DIM}{r7}{RESET}"
                print(line(row7))

        if has_extra:
            pe = int(float(extra.get("utilization", 0)))
            re = format_reset_time(extra.get("resets_at"))
            ate = format_reset_at_local(extra.get("resets_at"))
            re_display = f"{re} ({ate})" if ate else re
            extra_label = extra.get("label", "Extra")
            if compact:
                row_extra = f"  {LABEL}{extra_label}{RESET}   {bar(pe)}  {color_for_pct(pe)}{pe}%{RESET}"
                reset_extra = f"       {DIM}{re_display}{RESET}"
                print(line(row_extra))
                print(line(reset_extra))
            else:
                row_extra = f"  {LABEL}{extra_label}{RESET}   {bar(pe)}  {color_for_pct(pe)}{pe}%{RESET}   {DIM}{re_display}{RESET}"
                print(line(row_extra))

        print(line(""))
    else:
        print(line(DIM + "cargando..." + RESET, "center"))
        print(line(""))
    time_styled = DIM + last_update + RESET
    time_len = visible_len(time_styled)
    dash_total = max(0, w - time_len)
    left_dashes = dash_total // 2
    right_dashes = dash_total - left_dashes
    bottom = BOX_BOTTOM_LEFT + BOX_H * left_dashes + time_styled + BOX_H * right_dashes + BOX_BOTTOM_RIGHT
    print(bottom)


# =============================================================================
# Estado global (para redibujar al redimensionar)
# =============================================================================

_state = {"usage": None, "last_update": "", "error_msg": None}


def _on_resize(signum, frame):
    render_screen(_state["usage"], _state["last_update"], _state["error_msg"])


signal.signal(signal.SIGWINCH, _on_resize)


# =============================================================================
# Bucle principal
# =============================================================================

def main():
    print(CURSOR_HIDE, end="")
    try:
        last_update = datetime.now().strftime("%H:%M")
        token, err = get_claude_code_token()
        if err:
            render_screen(None, last_update, err)
            while True:
                time.sleep(POLL_INTERVAL)
                token, err = get_claude_code_token()
                last_update = datetime.now().strftime("%H:%M")
                if not err and token:
                    usage, api_err = fetch_usage(token)
                    if api_err:
                        render_screen(None, last_update, api_err)
                    else:
                        render_screen(usage, last_update, None)
                else:
                    render_screen(None, last_update, err)
        else:
            usage, api_err = fetch_usage(token)
            if api_err:
                render_screen(None, last_update, api_err)
            else:
                render_screen(usage, last_update, None)
            while True:
                time.sleep(POLL_INTERVAL)
                token, err = get_claude_code_token()
                last_update = datetime.now().strftime("%H:%M")
                if err:
                    render_screen(None, last_update, err)
                    continue
                usage, api_err = fetch_usage(token)
                if api_err:
                    render_screen(None, last_update, api_err)
                else:
                    render_screen(usage, last_update, None)
    except KeyboardInterrupt:
        print()
    finally:
        print(CURSOR_SHOW, end="")


if __name__ == "__main__":
    main()
