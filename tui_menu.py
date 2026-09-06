#!/usr/bin/env python3
"""
TUI Menu Module for YouTube Automation Workflow.
Supports arrow-key navigation (UP/DOWN + ENTER) with graceful non-interactive fallback.
"""

import os
import sys
import shutil
import re

try:
    import termios
    import tty
    import select
    HAS_TERMIOS = True
except ImportError:
    HAS_TERMIOS = False

# ANSI Color and Formatting Codes
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"
BG_BLUE = "\033[44m"
WHITE = "\033[97m"

ANSI_ESCAPE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from string to measure visible character length."""
    return ANSI_ESCAPE.sub('', text)


def truncate_line(text: str, max_width: int) -> str:
    """Safely truncate text so visible length does not exceed max_width, preventing line-wrap."""
    plain = strip_ansi(text)
    if len(plain) <= max_width:
        return text
    limit = max(10, max_width - 3)
    curr_len = 0
    res = []
    i = 0
    while i < len(text) and curr_len < limit:
        if text[i] == '\x1b':
            m = ANSI_ESCAPE.match(text, i)
            if m:
                res.append(m.group(0))
                i = m.end()
                continue
        res.append(text[i])
        curr_len += 1
        i += 1
    res.append(f"...{RESET}")
    return "".join(res)


def clear_screen():
    """Clear terminal screen and scrollback if supported."""
    if sys.stdout.isatty():
        os.system("cls" if os.name == "nt" else "clear")
        sys.stdout.write("\033[2J\033[3J\033[H")
        sys.stdout.flush()

def print_banner(subtitle: str = "YouTube & Instagram Automation Suite", platform: str = None):
    plat = (platform or "").strip().lower()
    if plat == "youtube":
        print(f"{CYAN}{BOLD}╔══════════════════════════════════════════════════════════════╗{RESET}")
        print(f"{CYAN}{BOLD}║               YOUTUBE WORKFLOW AUTOMATION                    ║{RESET}")
        print(f"{CYAN}{BOLD}║         Fully Automated Download & Upload Suite              ║{RESET}")
        print(f"{CYAN}{BOLD}╚══════════════════════════════════════════════════════════════╝{RESET}")
    elif plat == "instagram":
        print(f"{CYAN}{BOLD}╔══════════════════════════════════════════════════════════════╗{RESET}")
        print(f"{CYAN}{BOLD}║              INSTAGRAM WORKFLOW AUTOMATION                   ║{RESET}")
        print(f"{CYAN}{BOLD}║         Fully Automated Download & Upload Suite              ║{RESET}")
        print(f"{CYAN}{BOLD}╚══════════════════════════════════════════════════════════════╝{RESET}")
    elif plat == "settings":
        print(f"{CYAN}{BOLD}╔══════════════════════════════════════════════════════════════╗{RESET}")
        print(f"{CYAN}{BOLD}║                    WORKFLOW SETTINGS                         ║{RESET}")
        print(f"{CYAN}{BOLD}║            Credentials & Cookies Configuration               ║{RESET}")
        print(f"{CYAN}{BOLD}╚══════════════════════════════════════════════════════════════╝{RESET}")
    else:
        print(f"{CYAN}{BOLD}╔══════════════════════════════════════════════════════════════╗{RESET}")
        print(f"{CYAN}{BOLD}║         MULTI-PLATFORM WORKFLOW AUTOMATION                   ║{RESET}")
        print(f"{CYAN}{BOLD}║              (YouTube & Instagram)                           ║{RESET}")
        print(f"{CYAN}{BOLD}╚══════════════════════════════════════════════════════════════╝{RESET}")
    if subtitle:
        print(f"{YELLOW}▶ {subtitle}{RESET}\n")

def get_key(fd: int) -> str:
    """
    Reads a single keypress or ANSI escape sequence from the raw file descriptor.
    Returns normalized key string:
      'UP', 'DOWN', 'LEFT', 'RIGHT', 'PAGE_UP', 'PAGE_DOWN', 'HOME', 'END',
      'ENTER', 'ESC', 'QUIT', '1'-'9', or character string.
    """
    ch = os.read(fd, 1)
    if not ch:
        return "EOF"
    if ch == b"\x03":
        raise KeyboardInterrupt
    if ch == b"\x04":
        return "EOF"
    if ch in (b"\r", b"\n"):
        return "ENTER"
    if ch == b" ":
        return "SPACE"
    if ch == b"\x1b":
        # Check if more bytes are pending in the sequence (arrow keys, etc.)
        r, _, _ = select.select([fd], [], [], 0.05)
        if not r:
            return "ESC"
        seq = os.read(fd, 32)
        full = ch + seq
        # Cursor keys (both standard ANSI and application mode SS3)
        if full.startswith((b"\x1b[A", b"\x1bOA")):
            return "UP"
        if full.startswith((b"\x1b[B", b"\x1bOB")):
            return "DOWN"
        if full.startswith((b"\x1b[C", b"\x1bOC")):
            return "RIGHT"
        if full.startswith((b"\x1b[D", b"\x1bOD")):
            return "LEFT"
        # Page up / down
        if full.startswith((b"\x1b[5~", b"\x1b[I")):
            return "PAGE_UP"
        if full.startswith((b"\x1b[6~", b"\x1b[G")):
            return "PAGE_DOWN"
        # Home / End
        if full.startswith((b"\x1b[H", b"\x1b[1~", b"\x1bOH", b"\x1b[7~")):
            return "HOME"
        if full.startswith((b"\x1b[F", b"\x1b[4~", b"\x1bOF", b"\x1b[8~")):
            return "END"
        return "ESC"

    try:
        c = ch.decode("utf-8", errors="replace")
        if c in ("k", "K"):
            return "UP"
        if c in ("j", "J"):
            return "DOWN"
        if c in ("q", "Q"):
            return "QUIT"
        return c
    except Exception:
        return ""

def select_menu(prompt: str, options: list, default_index: int = 0) -> int:
    """
    Display an interactive menu navigable with UP/DOWN arrow keys, j/k, or number keys (1-9).
    Confirms with ENTER or direct number key.
    Handles terminal redraws cleanly without cursor drift, ghosting, or line duplication.
    """
    if not options:
        return -1

    # Non-interactive fallback (pipes, scripts, redirects, non-TTY)
    if not sys.stdin.isatty() or not sys.stdout.isatty() or not HAS_TERMIOS:
        print(f"\n{BOLD}{prompt}{RESET}")
        for i, opt in enumerate(options):
            print(f"  [{i + 1}] {opt}")
        while True:
            try:
                raw = input(f"\nChoose option [1-{len(options)}]: ").strip()
                if raw.isdigit() and 1 <= int(raw) <= len(options):
                    return int(raw) - 1
            except (EOFError, KeyboardInterrupt):
                return -1

    selected = max(0, min(default_index, len(options) - 1))
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    rendered_row_count = 0

    def build_render_lines(sel_idx: int) -> list:
        cols = shutil.get_terminal_size((80, 24)).columns
        max_w = max(40, cols - 2)
        render = [
            truncate_line(f"{BOLD}{CYAN}{prompt}{RESET}", max_w),
            truncate_line(f"{DIM}(Use ↑ / ↓ arrow keys or press [1-{len(options)}], Enter to confirm, 'q' to cancel){RESET}", max_w)
        ]
        for i, option in enumerate(options):
            num_tag = f"[{i + 1}]"
            if i == sel_idx:
                line_str = f"  {GREEN}{BOLD}❯ {num_tag} {option}{RESET}"
            else:
                line_str = f"    {DIM}{num_tag} {option}{RESET}"
            render.append(truncate_line(line_str, max_w))
        return render

    def calc_visual_rows(lines: list) -> int:
        cols = shutil.get_terminal_size((80, 24)).columns
        total = 0
        for l in lines:
            vlen = len(strip_ansi(l))
            total += max(1, (vlen + cols - 1) // cols) if cols > 0 else 1
        return total

    def clear_rendered_menu():
        nonlocal rendered_row_count
        if rendered_row_count > 0:
            sys.stdout.write(f"\033[{rendered_row_count}A\r")
            for _ in range(rendered_row_count):
                sys.stdout.write("\033[2K\n")
            sys.stdout.write(f"\033[{rendered_row_count}A\r")
            sys.stdout.flush()
            rendered_row_count = 0

    try:
        tty.setcbreak(fd)
        # Hide cursor
        sys.stdout.write("\033[?25l")
        sys.stdout.flush()

        first_render = True
        while True:
            render_lines = build_render_lines(selected)
            rows = calc_visual_rows(render_lines)

            if not first_render and rendered_row_count > 0:
                # Move cursor back up to the start of the menu
                sys.stdout.write(f"\033[{rendered_row_count}A\r")

            # Redraw lines, clearing each line completely with \033[2K
            for line in render_lines:
                sys.stdout.write(f"\033[2K{line}\n")
            sys.stdout.flush()

            rendered_row_count = rows
            first_render = False

            key = get_key(fd)

            # Navigation
            if key == "UP":
                selected = (selected - 1) % len(options)
            elif key == "DOWN":
                selected = (selected + 1) % len(options)
            elif key in ("PAGE_UP", "HOME"):
                selected = 0
            elif key in ("PAGE_DOWN", "END"):
                selected = len(options) - 1
            # Direct number key selection (1 to 9)
            elif key.isdigit():
                num = int(key)
                if 1 <= num <= len(options):
                    selected = num - 1
                    clear_rendered_menu()
                    print(f"{GREEN}✔ Selected:{RESET} {BOLD}{options[selected]}{RESET}\n")
                    return selected
            # Confirm selection
            elif key in ("ENTER", "SPACE"):
                clear_rendered_menu()
                print(f"{GREEN}✔ Selected:{RESET} {BOLD}{options[selected]}{RESET}\n")
                return selected
            # Quit / Cancel
            elif key in ("QUIT", "ESC", "EOF"):
                clear_rendered_menu()
                return -1

    except (KeyboardInterrupt, EOFError):
        clear_rendered_menu()
        return -1
    finally:
        # Restore terminal settings and make cursor visible again
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        except Exception:
            pass
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()

def ask_text(prompt: str, allow_empty: bool = False, validator=None) -> str:
    """Prompt user for text input with optional validation."""
    while True:
        try:
            val = input(f"{BOLD}{YELLOW}? {prompt}{RESET} ").strip()
            if not val and not allow_empty:
                print(f"{RED}Input cannot be empty. Please try again.{RESET}")
                continue
            if validator and val:
                valid, msg = validator(val)
                if not valid:
                    print(f"{RED}{msg}{RESET}")
                    continue
            return val
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")
            raise KeyboardInterrupt
