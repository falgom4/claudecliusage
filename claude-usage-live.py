#!/usr/bin/env python3
"""CLI que muestra en tiempo real el uso de Claude Pro y Cursor en la terminal.

Dos vistas: Claude Pro (Keychain + API Anthropic) y Cursor (state.vscdb + cursor.com).
Flechas izquierda/derecha cambian entre Claude Pro y Cursor.

Requisitos: macOS; Claude Code y/o Cursor abiertos al menos una vez para credenciales.
"""

import base64
import json
import os
import re
import select
import signal
import subprocess
import sys
import time
import urllib.request
try:
    import termios
    import tty
except ImportError:
    termios = None
    tty = None
import urllib.error
from datetime import datetime, timezone

# Tabs
TAB_CLAUDE = "claude"
TAB_CURSOR = "cursor"

# =============================================================================
# Constantes
# =============================================================================

USAGE_API_URL = "https://api.anthropic.com/api/oauth/usage"
OAUTH_BETA_HEADER = "oauth-2025-04-20"
POLL_INTERVAL = 30  # segundos
KEYCHAIN_SERVICE = "Claude Code-credentials"
KEYCHAIN_TIMEOUT = 30
SESSION_REFRESH_WAIT = 8  # segundos a esperar tras lanzar claude para que guarde el token

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
# Renovación de sesión (cuando el token expira)
# =============================================================================

def _is_auth_error(msg):
    """Devuelve True si el mensaje de error indica sesión expirada o token inválido."""
    if not msg:
        return False
    low = msg.lower()
    return any(k in low for k in ("unauthorized", "invalid", "expired", "unauthenticated", "401"))


def refresh_session(render_fn, last_update):
    """Abre claude en background para renovar el token y lo cierra tras SESSION_REFRESH_WAIT s."""
    render_fn(TAB_CLAUDE, None, last_update, "Sesión expirada — renovando credenciales…")
    try:
        proc = subprocess.Popen(
            ["claude", "--print", ""],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        time.sleep(SESSION_REFRESH_WAIT)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    except FileNotFoundError:
        render_fn(TAB_CLAUDE, None, last_update, "No se encontró el comando 'claude'. ¿Está instalado?")
        time.sleep(POLL_INTERVAL)


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


def _render_claude_body(usage, last_update, error_msg, w, line):
    """Contenido del recuadro para la pestaña Claude Pro."""
    if error_msg:
        msg = RED + error_msg + RESET
        print(line(msg))
        return
    if not usage:
        print(line(DIM + "cargando..." + RESET, "center"))
        print(line(""))
        return
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
        extra_label = extra.get("label", "Ex")
        raw_spend = extra.get("amount_spent") if extra.get("amount_spent") is not None else extra.get("spend", extra.get("dollars_used"))
        if raw_spend is not None:
            try:
                usd = float(raw_spend)
                usd_str = f"  ${usd:.2f}"
            except (TypeError, ValueError):
                usd_str = ""
        else:
            usd_str = ""
        if compact:
            row_extra = f"  {LABEL}{extra_label}{RESET}   {bar(pe)}  {color_for_pct(pe)}{pe}%{RESET}{usd_str}"
            reset_extra = f"       {DIM}{re_display}{RESET}"
            print(line(row_extra))
            print(line(reset_extra))
        else:
            row_extra = f"  {LABEL}{extra_label}{RESET}   {bar(pe)}  {color_for_pct(pe)}{pe}%{RESET}   {DIM}{re_display}{RESET}{usd_str}"
            print(line(row_extra))

    print(line(""))


def _render_cursor_body(usage, last_update, error_msg, w, line):
    """Contenido del recuadro para la pestaña Cursor: costo gastado / $20 incluidos."""
    if error_msg:
        msg = RED + error_msg + RESET
        print(line(msg))
        return
    if not usage:
        print(line(DIM + "cargando..." + RESET, "center"))
        print(line(""))
        return

    spent = usage.get("spent_dollars", 0.0)
    included = usage.get("included_dollars", 20.0)
    pct = int(100 * spent / included) if included else 0
    pct = min(pct, 100)

    # Fila principal: barra + porcentaje + costo
    cost_str = f"${spent:.2f} / ${included:.2f}"
    row = f"  {LABEL}Gasto{RESET}   {bar(pct)}  {color_for_pct(pct)}{pct}%{RESET}   {DIM}{cost_str}{RESET}"
    compact = w < 2 + 7 + BAR_WIDTH + 2 + 4 + 3 + 3 + len(cost_str)
    if compact:
        print(line(f"  {LABEL}Gasto{RESET}   {bar(pct)}  {color_for_pct(pct)}{pct}%{RESET}"))
        print(line(f"         {color_for_pct(pct)}{cost_str}{RESET}"))
    else:
        print(line(row))

    # Línea de período
    start_of_month = usage.get("startOfMonth") or ""
    period_month = usage.get("period_month")
    period_year = usage.get("period_year")
    if start_of_month:
        try:
            ts = start_of_month.replace("Z", "+00:00")
            if "." in ts:
                ts = ts.split(".")[0] + "+00:00"
            sub_start = datetime.fromisoformat(ts)
            if period_month and period_year:
                p_start = sub_start.replace(year=period_year, month=period_month)
                p_end_month = period_month % 12 + 1
                p_end_year = period_year + (1 if period_month == 12 else 0)
                p_end = sub_start.replace(year=p_end_year, month=p_end_month)
                period_str = f"{p_start.strftime('%d %b')} – {p_end.strftime('%d %b')}"
            else:
                period_str = sub_start.strftime("%d %b")
        except Exception:
            period_str = ""
        if period_str:
            print(line(f"         {DIM}{period_str}{RESET}"))

    # Indicador de factura pendiente a mitad de mes
    if usage.get("has_unpaid"):
        mid_paid = usage.get("mid_month_paid", 0.0)
        print(line(f"         {YELLOW}factura parcial pendiente  −${mid_paid:.2f} pagado{RESET}"))

    print(line(""))


def render_screen(tab, usage=None, last_update=None, error_msg=None):
    """Limpia la pantalla y muestra el panel de uso (Claude o Cursor) dentro de un recuadro.

    Si se pasa tab, se actualiza _state[tab] y _state['current_tab']. Si no se pasa usage/
    last_update/error_msg, se usan los de _state[tab] (redibujar desde caché).
    """
    if tab is not None:
        _state[tab]["usage"] = usage
        _state[tab]["last_update"] = last_update or ""
        _state[tab]["error_msg"] = error_msg
        _state["current_tab"] = tab
    current = _state["current_tab"]
    s = _state[current]
    usage = s["usage"] if usage is None else usage
    last_update = s["last_update"] if last_update is None else last_update
    error_msg = s["error_msg"] if error_msg is None else error_msg

    w = get_box_width()
    title_text = " Claude Pro " if current == TAB_CLAUDE else " Cursor "
    title_styled = ACCENT + title_text + RESET
    title_len = visible_len(title_styled)
    dash_total = max(0, w - title_len)
    left_dashes = dash_total // 2
    right_dashes = dash_total - left_dashes
    top = BOX_TOP_LEFT + BOX_H * left_dashes + title_styled + BOX_H * right_dashes + BOX_TOP_RIGHT

    def line(content, align="left"):
        return BOX_V + pad_to_width(content, w, align) + BOX_V

    print("\033[2J\033[H", end="")
    print()
    print(top)
    print(line(""))

    if current == TAB_CLAUDE:
        _render_claude_body(usage, last_update, error_msg, w, line)
    else:
        _render_cursor_body(usage, last_update, error_msg, w, line)

    # Indicador de flechas para cambiar de vista (vista activa resaltada)
    if current == TAB_CLAUDE:
        arrow_hint = f"  ← {ACCENT}Claude Pro{RESET}   {DIM}Cursor{RESET} →  "
    else:
        arrow_hint = f"  ← {DIM}Claude Pro{RESET}   {ACCENT}Cursor{RESET} →  "
    print(line(arrow_hint, "center"))

    time_styled = DIM + last_update + RESET
    time_len = visible_len(time_styled)
    dash_total = max(0, w - time_len)
    left_dashes = dash_total // 2
    right_dashes = dash_total - left_dashes
    bottom = BOX_BOTTOM_LEFT + BOX_H * left_dashes + time_styled + BOX_H * right_dashes + BOX_BOTTOM_RIGHT
    print(bottom)


# =============================================================================
# Estado global (por tab; redibujar al redimensionar)
# =============================================================================

def _default_tab_state():
    return {"usage": None, "last_update": "", "error_msg": None}


_state = {
    TAB_CLAUDE: _default_tab_state(),
    TAB_CURSOR: _default_tab_state(),
    "current_tab": TAB_CLAUDE,
}
_saved_termios = None


def _on_resize(signum, frame):
    tab = _state["current_tab"]
    s = _state[tab]
    render_screen(tab, s["usage"], s["last_update"], s["error_msg"])


signal.signal(signal.SIGWINCH, _on_resize)


# =============================================================================
# Cursor: token desde state.vscdb y API de uso
# =============================================================================

CURSOR_STATE_DB = os.path.expanduser(
    "~/Library/Application Support/Cursor/User/globalStorage/state.vscdb"
)
CURSOR_USAGE_URL = "https://cursor.com/api/usage"


def _decode_jwt_payload(token_str):
    """Decodifica el payload (segundo segmento) de un JWT; devuelve dict o None."""
    try:
        parts = token_str.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload_b64 = payload_b64.replace("-", "+").replace("_", "/")
        raw = base64.b64decode(payload_b64)
        return json.loads(raw)
    except Exception:
        return None


def get_cursor_session_token():
    """Obtiene session token y user_id desde la base de datos local de Cursor (macOS).

    Devuelve (session_token, user_id, error_msg). En éxito error_msg es None.
    """
    if sys.platform != "darwin":
        return None, None, "Cursor solo en macOS."
    if not os.path.isfile(CURSOR_STATE_DB):
        return None, None, (
            "No hay credenciales de Cursor. Abre Cursor, inicia sesión y vuelve a ejecutar."
        )
    try:
        import sqlite3
        conn = sqlite3.connect(CURSOR_STATE_DB)
        cur = conn.execute(
            "SELECT value FROM ItemTable WHERE key = 'cursorAuth/accessToken'"
        )
        row = cur.fetchone()
        conn.close()
        if not row:
            return None, None, (
                "No hay token en Cursor. Abre Cursor, inicia sesión y vuelve a ejecutar."
            )
        access_token = row[0]
        payload = _decode_jwt_payload(access_token)
        if not payload or "sub" not in payload:
            return None, None, "Token de Cursor inválido. Vuelve a iniciar sesión en Cursor."
        sub = str(payload["sub"])
        if "|" in sub:
            user_id = sub.split("|", 1)[1]
        else:
            user_id = sub
        session_token = f"{user_id}%3A%3A{access_token}"
        return session_token, user_id, None
    except Exception as e:
        return None, None, f"Error leyendo Cursor: {e}"


CURSOR_INVOICE_URL = "https://cursor.com/api/dashboard/get-monthly-invoice"
CURSOR_PRO_INCLUDED_CENTS = 2000  # $20.00 incluidos en Cursor Pro


def _cursor_browser_headers(session_token):
    return {
        "Cookie": f"WorkosCursorSessionToken={session_token}",
        "Content-Type": "application/json",
        "Origin": "https://cursor.com",
        "Referer": "https://cursor.com/dashboard",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
        "Accept-Language": "en",
        "Cache-Control": "no-cache",
    }


def _cursor_post(url, body, session_token):
    """POST JSON a la API de Cursor. Devuelve (data, error_msg)."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=_cursor_browser_headers(session_token), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        body_bytes = e.read().decode("utf-8", errors="replace")
        try:
            err = json.loads(body_bytes)
            msg = err.get("error", err.get("message", body_bytes))
        except json.JSONDecodeError:
            msg = body_bytes or str(e)
        return None, f"HTTP {e.code}: {msg}"
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        return None, str(e)


def fetch_cursor_usage(session_token, user_id):
    """Obtiene el gasto mensual de Cursor Pro desde get-monthly-invoice.

    Cursor Pro = pool de $20/mes para modelos non-Auto (desde junio 2025).
    Devuelve (usage_dict, error_msg).
    """
    # 1. Obtener startOfMonth desde /api/usage
    url_usage = f"{CURSOR_USAGE_URL}?user={user_id}"
    req_usage = urllib.request.Request(url_usage, headers=_cursor_browser_headers(session_token))
    start_of_month = ""
    try:
        with urllib.request.urlopen(req_usage, timeout=10) as resp:
            raw_usage = json.loads(resp.read().decode("utf-8"))
        start_of_month = raw_usage.get("startOfMonth") or ""
    except Exception:
        pass  # Sin startOfMonth usamos mes actual

    # Calcular mes/año del ciclo de facturación actual
    now = datetime.now(timezone.utc)
    if start_of_month:
        try:
            ts = start_of_month.replace("Z", "+00:00")
            if "." in ts:
                ts = ts.split(".")[0] + "+00:00"
            sub_start = datetime.fromisoformat(ts)
            months_since = (now.year - sub_start.year) * 12 + (now.month - sub_start.month)
            period_month = sub_start.month + months_since
            period_year = sub_start.year + (period_month - 1) // 12
            period_month = ((period_month - 1) % 12) + 1
        except Exception:
            period_month, period_year = now.month, now.year
    else:
        period_month, period_year = now.month, now.year

    # 2. Pedir factura del mes actual
    invoice, err = _cursor_post(
        CURSOR_INVOICE_URL,
        {"month": period_month, "year": period_year, "includeUsageEvents": False},
        session_token,
    )
    if err:
        # Intentar con mes anterior como fallback
        prev_month = period_month - 1 or 12
        prev_year = period_year - (1 if period_month == 1 else 0)
        invoice, err2 = _cursor_post(
            CURSOR_INVOICE_URL,
            {"month": prev_month, "year": prev_year, "includeUsageEvents": False},
            session_token,
        )
        if err2:
            return None, err  # devolver error original

    items = invoice.get("items") or []

    # 3. Sumar centavos de uso (ignorar pagos a mitad de mes)
    spent_cents = 0
    mid_month_cents = 0
    for item in items:
        cents = item.get("cents")
        if cents is None:
            continue
        desc = item.get("description") or ""
        if "Mid-month usage paid" in desc:
            mid_month_cents += abs(cents)
        else:
            spent_cents += max(0, cents)

    spent_dollars = spent_cents / 100.0
    included_dollars = CURSOR_PRO_INCLUDED_CENTS / 100.0

    usage = {
        "spent_dollars": spent_dollars,
        "included_dollars": included_dollars,
        "startOfMonth": start_of_month,
        "period_month": period_month,
        "period_year": period_year,
        "has_unpaid": invoice.get("hasUnpaidMidMonthInvoice", False),
        "mid_month_paid": mid_month_cents / 100.0,
    }
    return usage, None


# =============================================================================
# Lectura de teclas (flechas)
# =============================================================================

def _read_arrow_or_timeout(timeout_sec):
    """Espera hasta timeout_sec. Devuelve 'left', 'right' si se pulsó esa flecha, None si timeout."""
    if termios is None or not sys.stdin.isatty():
        time.sleep(timeout_sec)
        return None
    r, _, _ = select.select([sys.stdin], [], [], timeout_sec)
    if not r:
        return None
    buf = os.read(sys.stdin.fileno(), 1)
    if buf != b"\x1b":
        return None
    deadline = time.monotonic() + 0.05
    while time.monotonic() < deadline and len(buf) < 3:
        r2, _, _ = select.select([sys.stdin], [], [], 0.02)
        if r2:
            buf += os.read(sys.stdin.fileno(), 3 - len(buf))
    if buf == b"\x1b[D":
        return "left"
    if buf == b"\x1b[C":
        return "right"
    return None


# =============================================================================
# Bucle principal
# =============================================================================

def _fetch_tab(tab):
    """Actualiza _state[tab] con el resultado del fetch. No redibuja. Devuelve 'refresh_claude' si hay que renovar sesión."""
    last_update = datetime.now().strftime("%H:%M")
    if tab == TAB_CLAUDE:
        token, err = get_claude_code_token()
        if err:
            _state[TAB_CLAUDE]["usage"] = None
            _state[TAB_CLAUDE]["last_update"] = last_update
            _state[TAB_CLAUDE]["error_msg"] = err
            return "refresh_claude" if _is_auth_error(err) else None
        usage, api_err = fetch_usage(token)
        last_update = datetime.now().strftime("%H:%M")
        if api_err:
            _state[TAB_CLAUDE]["usage"] = None
            _state[TAB_CLAUDE]["last_update"] = last_update
            _state[TAB_CLAUDE]["error_msg"] = api_err
            return "refresh_claude" if _is_auth_error(api_err) else None
        _state[TAB_CLAUDE]["usage"] = usage
        _state[TAB_CLAUDE]["last_update"] = last_update
        _state[TAB_CLAUDE]["error_msg"] = None
        return None
    # TAB_CURSOR
    if sys.platform != "darwin":
        _state[TAB_CURSOR]["usage"] = None
        _state[TAB_CURSOR]["last_update"] = last_update
        _state[TAB_CURSOR]["error_msg"] = "Cursor solo en macOS."
        return None
    session_token, user_id, err = get_cursor_session_token()
    if err:
        _state[TAB_CURSOR]["usage"] = None
        _state[TAB_CURSOR]["last_update"] = last_update
        _state[TAB_CURSOR]["error_msg"] = err
        return None
    usage, api_err = fetch_cursor_usage(session_token, user_id)
    last_update = datetime.now().strftime("%H:%M")
    if api_err:
        _state[TAB_CURSOR]["usage"] = None
        _state[TAB_CURSOR]["last_update"] = last_update
        _state[TAB_CURSOR]["error_msg"] = api_err
        return None
    _state[TAB_CURSOR]["usage"] = usage
    _state[TAB_CURSOR]["last_update"] = last_update
    _state[TAB_CURSOR]["error_msg"] = None
    return None


def main():
    global _saved_termios
    print(CURSOR_HIDE, end="")
    try:
        if termios is not None and tty is not None and sys.stdin.isatty():
            _saved_termios = termios.tcgetattr(sys.stdin)
            tty.setcbreak(sys.stdin.fileno())
    except OSError:
        _saved_termios = None

    try:
        # Dibujo inicial (Claude) desde caché vacío
        render_screen(TAB_CLAUDE, None, datetime.now().strftime("%H:%M"), None)

        # Carga inicial inmediata de ambas pestañas (no esperar 30 s)
        need_refresh = _fetch_tab(TAB_CLAUDE)
        if need_refresh == "refresh_claude":
            refresh_session(render_screen, _state[TAB_CLAUDE]["last_update"])
        else:
            render_screen(None, None, None, None)
        _fetch_tab(TAB_CURSOR)
        render_screen(None, None, None, None)

        while True:
            arrow = _read_arrow_or_timeout(POLL_INTERVAL)

            if arrow is not None:
                # Cambiar de pestaña y redibujar desde caché
                cur = _state["current_tab"]
                if arrow == "right":
                    _state["current_tab"] = TAB_CURSOR if cur == TAB_CLAUDE else TAB_CLAUDE
                else:
                    _state["current_tab"] = TAB_CLAUDE if cur == TAB_CURSOR else TAB_CURSOR
                tab = _state["current_tab"]
                if tab == TAB_CURSOR and sys.platform != "darwin":
                    _state[TAB_CURSOR]["error_msg"] = "Cursor solo en macOS."
                    _state[TAB_CURSOR]["last_update"] = datetime.now().strftime("%H:%M")
                render_screen(None, None, None, None)
                continue

            # Timeout: actualizar ambas pestañas de forma independiente y redibujar la activa
            need_refresh = _fetch_tab(TAB_CLAUDE)
            if need_refresh == "refresh_claude":
                refresh_session(render_screen, _state[TAB_CLAUDE]["last_update"])
            else:
                _fetch_tab(TAB_CURSOR)
                render_screen(None, None, None, None)

    except KeyboardInterrupt:
        print()
    finally:
        if _saved_termios is not None and termios is not None:
            try:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, _saved_termios)
            except OSError:
                pass
        print(CURSOR_SHOW, end="")


if __name__ == "__main__":
    main()
