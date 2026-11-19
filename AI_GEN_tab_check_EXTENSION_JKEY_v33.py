
import time
import json
import threading
import os
import shutil
from datetime import datetime
import subprocess
import ctypes

from ctypes import wintypes
from pathlib import Path
from urllib.parse import urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pyautogui
import pyperclip
import pyotp
import cv2
import xlwings as xw
from pywinauto import Desktop
from openpyxl import load_workbook
from NorenRestApiPy.NorenApi import NorenApi


# ============================================================
# SETTINGS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
EXCEL_FILE = BASE_DIR / "symbols.xlsx"
SHEET_NAME = "LOGIN"

EXT_DIR = BASE_DIR / "shoonya_jkey_extension"

# Temporary evidence folder: created on Desktop for each capture run and
# removed automatically only after the jKey is confirmed VALID.
SCREENSHOT_DIR = None

SHOONYA_URL = "https://trade.shoonya.com/#/"
SHOONYA_HOST = "trade.shoonya.com"

RECEIVER_HOST = "127.0.0.1"
RECEIVER_PORT = 8765

# Short wait for a fresh jKey request; the outer loop keeps retrying.
JKEY_TIMEOUT = 4

# Extension scan/reload acknowledgements are bounded; outer workflow retries.
RELOAD_TIMEOUT = 3

LOGIN_WAIT = 2

DETECTION_ATTEMPTS = 2
DETECTION_INTERVAL = 1

jkey_event = threading.Event()
captured_jkey = None

# Safe metadata only; never store or print credentials or the jKey.
jkey_diagnostics = []
candidate_request_count = 0
extension_heartbeat_event = threading.Event()
extension_heartbeat_info = {}

# Excel workbook is opened first and kept as the single live source of truth.
excel_book = None
login_field_rectangles = []
last_expected_mouse = None

tab_snapshot_event = threading.Event()
tab_snapshot = None

scan_requested = False
reload_requested = False

reload_event = threading.Event()
reload_result = None

scan_lock = threading.Lock()

user32 = ctypes.WinDLL("user32", use_last_error=True)

SW_RESTORE = 9  # Restore a minimized Chrome window.
SW_MAXIMIZE = 3  # Maximize Chrome so screenshots and click coordinates stay consistent.

# PyAutoGUI emergency corner stop remains enabled.
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05


# ============================================================
# CHROME EXTENSION FILES
# ============================================================

MANIFEST = r"""{
  "manifest_version": 3,
  "name": "Shoonya JKEY Capture + Tab Scanner",
  "version": "1.8.0",
  "description": "Captures Shoonya API jKey in the extension and delivers it to the local Excel receiver.",
  "permissions": ["tabs", "alarms", "webRequest"],
  "host_permissions": ["https://*/*", "http://127.0.0.1:8765/*"],
  "background": {
    "service_worker": "service_worker.js"
  }
}
"""


SERVICE_WORKER_JS = r"""'use strict';

const LOCAL = 'http://127.0.0.1:8765';
const SHOONYA_DOMAIN = 'shoonya.com';

let scanBusy = false;
let pendingJKey = null;
let sendingJKey = false;

// Send plain text JSON to avoid a browser CORS preflight.
// The Python receiver still validates the extension origin and request data.
async function postLocal(path, payload) {
    const response = await fetch(LOCAL + path, {
        method: 'POST',
        headers: {'Content-Type': 'text/plain;charset=UTF-8'},
        body: JSON.stringify(payload),
        cache: 'no-store'
    });
    return response;
}

function isShoonyaNorenRequest(urlText) {
    // Observe every HTTPS request to Shoonya-owned hosts, not only URLs whose
    // path contains NorenWClientWeb. Some Shoonya builds route web-session
    // calls through a different path. jKey is still accepted only when it is
    // explicitly present in that request's body or URL.
    try {
        const url = new URL(urlText);
        const host = url.hostname.toLowerCase();
        return url.protocol === 'https:' &&
            (host === SHOONYA_DOMAIN || host.endsWith('.' + SHOONYA_DOMAIN));
    } catch (_) {
        return false;
    }
}

function decodeRawParts(parts) {
    let result = '';
    for (const part of (parts || [])) {
        if (!part || part.bytes === undefined || part.bytes === null) continue;
        try {
            let bytes;
            if (part.bytes instanceof ArrayBuffer) {
                bytes = new Uint8Array(part.bytes);
            } else if (ArrayBuffer.isView(part.bytes)) {
                bytes = new Uint8Array(
                    part.bytes.buffer, part.bytes.byteOffset, part.bytes.byteLength
                );
            } else if (Array.isArray(part.bytes)) {
                bytes = new Uint8Array(part.bytes);
            } else {
                continue;
            }
            result += new TextDecoder('utf-8').decode(bytes);
        } catch (_) {}
    }
    return result;
}

function findJKeyInObject(value, depth = 0) {
    if (depth > 8 || value === null || value === undefined) return '';

    if (typeof value === 'string') {
        const text = value.trim();
        if (!text) return '';

        if ((text.startsWith('{') && text.endsWith('}')) ||
            (text.startsWith('[') && text.endsWith(']'))) {
            try {
                const found = findJKeyInObject(JSON.parse(text), depth + 1);
                if (found) return found;
            } catch (_) {}
        }

        if (text.includes('=') && (text.includes('&') || /jkey/i.test(text))) {
            try {
                for (const [key, item] of new URLSearchParams(text).entries()) {
                    if (String(key).toLowerCase() === 'jkey' && item) return item;
                    const found = findJKeyInObject(item, depth + 1);
                    if (found) return found;
                }
            } catch (_) {}
        }
        return '';
    }

    if (Array.isArray(value)) {
        for (const item of value) {
            const found = findJKeyInObject(item, depth + 1);
            if (found) return found;
        }
        return '';
    }

    if (typeof value === 'object') {
        for (const [key, item] of Object.entries(value)) {
            if (String(key).toLowerCase() === 'jkey') {
                if (Array.isArray(item) && item.length) return String(item[0]);
                if (item !== null && item !== undefined) return String(item);
            }
        }
        for (const item of Object.values(value)) {
            const found = findJKeyInObject(item, depth + 1);
            if (found) return found;
        }
    }
    return '';
}

function extractJKey(details) {
    const body = details.requestBody || {};

    if (body.formData) {
        for (const [key, values] of Object.entries(body.formData)) {
            if (String(key).toLowerCase() === 'jkey' &&
                Array.isArray(values) && values.length) {
                return String(values[0]);
            }
        }
    }

    const raw = decodeRawParts(body.raw).trim();
    if (raw) {
        try {
            const found = findJKeyInObject(JSON.parse(raw));
            if (found) return found;
        } catch (_) {}

        try {
            for (const [key, value] of new URLSearchParams(raw).entries()) {
                if (String(key).toLowerCase() === 'jkey' && value) return value;
                const found = findJKeyInObject(value);
                if (found) return found;
            }
        } catch (_) {}

        const match = raw.match(/(?:^|[&?])jkey=([^&]+)/i);
        if (match) {
            try {
                const decoded = decodeURIComponent(match[1].replace(/\+/g, ' '));
                if (decoded) return decoded;
            } catch (_) {}
        }

        const jsonMatch = raw.match(/["']jkey["']\s*:\s*["']([^"']+)["']/i);
        if (jsonMatch) return jsonMatch[1];
    }

    try {
        const url = new URL(details.url);
        for (const [key, value] of url.searchParams.entries()) {
            if (String(key).toLowerCase() === 'jkey' && value) return value;
        }
    } catch (_) {}

    return '';
}

function safeRequestInfo(details) {
    const url = new URL(details.url);
    const body = details.requestBody || {};
    return {
        host: url.hostname,
        path: url.pathname,
        method: details.method || '',
        resource_type: details.type || '',
        form_keys: body.formData ? Object.keys(body.formData).slice(0, 30) : [],
        has_raw_body: !!(body.raw && body.raw.length),
        time: new Date().toISOString()
    };
}

// Keep a captured key in memory and retry delivery until Python confirms it.
// The key is never logged or written to extension storage.
async function deliverPendingJKey() {
    if (!pendingJKey || sendingJKey) return;
    sendingJKey = true;
    try {
        const response = await postLocal('/jkey', pendingJKey);
        if (response.ok && pendingJKey) {
            pendingJKey = null;
        }
    } catch (_) {
        // Python may be restarting; the alarm will retry.
    } finally {
        sendingJKey = false;
    }
}

chrome.webRequest.onBeforeRequest.addListener(
    (details) => {
        if (!isShoonyaNorenRequest(details.url)) return;

        let info;
        try {
            info = safeRequestInfo(details);
        } catch (_) {
            return;
        }

        const jkey = extractJKey(details);
        if (!jkey || jkey.trim().length < 12) {
            postLocal('/candidate', {
                ...info,
                jkey_found: false
            }).catch(() => {});
            return;
        }

        pendingJKey = {
            jkey: jkey.trim(),
            request_host: info.host,
            request_path: info.path,
            method: info.method
        };
        deliverPendingJKey();
    },
    {urls: ['https://*/*']},
    ['requestBody']
);

// ------------------------------------------------------------
// TAB SCAN AND RELOAD
// ------------------------------------------------------------

function waitForTabComplete(tabId, timeoutMs) {
    return new Promise((resolve) => {
        let finished = false;
        const timer = setTimeout(() => {
            if (finished) return;
            finished = true;
            chrome.tabs.onUpdated.removeListener(listener);
            resolve(false);
        }, timeoutMs);

        function listener(updatedTabId, changeInfo) {
            if (updatedTabId !== tabId || changeInfo.status !== 'complete') return;
            if (finished) return;
            finished = true;
            clearTimeout(timer);
            chrome.tabs.onUpdated.removeListener(listener);
            resolve(true);
        }
        chrome.tabs.onUpdated.addListener(listener);
    });
}

async function sendHeartbeat() {
    try {
        await postLocal('/heartbeat', {
            extension: 'Shoonya JKEY Capture',
            version: chrome.runtime.getManifest().version,
            time: new Date().toISOString()
        });
    } catch (_) {}
}

async function checkForPythonScan() {
    if (scanBusy) return;
    scanBusy = true;

    try {
        await sendHeartbeat();
        const response = await fetch(LOCAL + '/scan', {cache: 'no-store'});
        if (!response.ok) return;

        const request = await response.json();
        if (!request.scan && !request.reload) return;

        const allTabs = await chrome.tabs.query({currentWindow: true});
        const reportTabs = allTabs.map((tab, index) => ({
            number: index + 1,
            id: tab.id,
            windowId: tab.windowId,
            active: Boolean(tab.active),
            url: tab.url || ''
        }));

        const shoonyaMatches = reportTabs.filter(tab => {
            try {
                return new URL(tab.url).hostname.toLowerCase() === 'trade.shoonya.com';
            } catch (_) {
                return false;
            }
        });
        // When several Shoonya tabs are open, prefer the currently active one.
        // If none is active, use the left-most (lowest tab position) match.
        const found =
            shoonyaMatches.find(tab => tab.active) ||
            shoonyaMatches.sort((a, b) => a.number - b.number)[0] ||
            null;

        let reloadStatus = 'NOT_REQUESTED';
        if (found) {
            await chrome.tabs.update(found.id, {active: true});
            await chrome.windows.update(found.windowId, {focused: true});

            if (request.reload) {
                reloadStatus = 'RELOAD_STARTED';
                const loadPromise = waitForTabComplete(found.id, 45000);
                await chrome.tabs.reload(found.id);
                const loaded = await loadPromise;

                if (loaded) {
                    const updatedTab = await chrome.tabs.get(found.id);
                    try {
                        reloadStatus =
                            new URL(updatedTab.url).hostname.toLowerCase() === 'trade.shoonya.com'
                                ? 'RELOAD_COMPLETE'
                                : 'URL_VERIFICATION_FAILED';
                    } catch (_) {
                        reloadStatus = 'URL_VERIFICATION_FAILED';
                    }
                } else {
                    reloadStatus = 'LOAD_TIMEOUT';
                }
            }
        } else if (request.reload) {
            reloadStatus = 'SHOONYA_TAB_NOT_FOUND';
        }

        await postLocal('/tabs', {
            tabs: reportTabs,
            shoonya: found ? {number: found.number} : null,
            reload_status: reloadStatus
        });
    } catch (_) {
        // The next alarm retries when Python becomes available.
    } finally {
        scanBusy = false;
        // Fast local polling while this service worker is alive. The alarm
        // below remains the fallback if Chrome suspends the worker.
        setTimeout(checkForPythonScan, 500);
    }
}

chrome.runtime.onInstalled.addListener(() => {
    chrome.alarms.create('python-tab-scan', {periodInMinutes: 0.5});
    chrome.alarms.create('retry-jkey', {periodInMinutes: 0.5});
    checkForPythonScan();
});

chrome.runtime.onStartup.addListener(() => {
    chrome.alarms.create('python-tab-scan', {periodInMinutes: 0.5});
    chrome.alarms.create('retry-jkey', {periodInMinutes: 0.5});
    checkForPythonScan();
});

chrome.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name === 'python-tab-scan') checkForPythonScan();
    if (alarm.name === 'retry-jkey') deliverPendingJKey();
});

checkForPythonScan();
"""


def write_extension_files():
    """Create extension files only when missing; never modify Chrome's installed extension."""
    EXT_DIR.mkdir(parents=True, exist_ok=True)

    files = {
        "manifest.json": MANIFEST,
        "service_worker.js": SERVICE_WORKER_JS,
    }

    created = []
    for filename, content in files.items():
        path = EXT_DIR / filename
        if not path.exists():
            path.write_text(content, encoding="utf-8")
            created.append(filename)

    if created:
        print("Created missing extension file(s):", ", ".join(created))
    else:
        print("Existing Shoonya extension files preserved; no reload/removal action performed.")

    print("Extension folder:", EXT_DIR)


# ============================================================
# WINDOWS API
# ============================================================

EnumWindowsProc = ctypes.WINFUNCTYPE(
    wintypes.BOOL,
    wintypes.HWND,
    wintypes.LPARAM
)

user32.EnumWindows.argtypes = [
    EnumWindowsProc,
    wintypes.LPARAM
]

user32.EnumWindows.restype = wintypes.BOOL

user32.IsWindowVisible.argtypes = [
    wintypes.HWND
]

user32.IsWindowVisible.restype = wintypes.BOOL

user32.GetWindowTextLengthW.argtypes = [
    wintypes.HWND
]

user32.GetWindowTextLengthW.restype = ctypes.c_int

user32.GetWindowTextW.argtypes = [
    wintypes.HWND,
    wintypes.LPWSTR,
    ctypes.c_int
]

user32.GetWindowTextW.restype = ctypes.c_int

user32.IsIconic.argtypes = [
    wintypes.HWND
]

user32.IsIconic.restype = wintypes.BOOL
user32.IsZoomed.argtypes = [wintypes.HWND]
user32.IsZoomed.restype = wintypes.BOOL

user32.ShowWindow.argtypes = [
    wintypes.HWND,
    ctypes.c_int
]

user32.ShowWindow.restype = wintypes.BOOL

user32.SetForegroundWindow.argtypes = [
    wintypes.HWND
]

user32.SetForegroundWindow.restype = wintypes.BOOL

user32.GetForegroundWindow.restype = wintypes.HWND

# Extra Win32 functions used to request foreground activation reliably.
user32.GetWindowThreadProcessId.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(wintypes.DWORD)
]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

# GetCurrentThreadId is exported by kernel32.dll, not user32.dll.
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

user32.AttachThreadInput.argtypes = [
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.BOOL
]
user32.AttachThreadInput.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.BringWindowToTop.restype = wintypes.BOOL
user32.SetActiveWindow.argtypes = [wintypes.HWND]
user32.SetActiveWindow.restype = wintypes.HWND
user32.SetFocus.argtypes = [wintypes.HWND]
user32.SetFocus.restype = wintypes.HWND


# ============================================================
# FIND GOOGLE CHROME
# ============================================================

def find_chrome():

    possible_paths = [

        Path(
            os.environ.get(
                "PROGRAMFILES",
                "C:/Program Files"
            )
        ) / "Google/Chrome/Application/chrome.exe",

        Path(
            os.environ.get(
                "PROGRAMFILES(X86)",
                "C:/Program Files (x86)"
            )
        ) / "Google/Chrome/Application/chrome.exe",

        Path(
            os.environ.get("LOCALAPPDATA", "")
        ) / "Google/Chrome/Application/chrome.exe"

    ]


    for path in possible_paths:

        if path.exists():
            return str(path)


    found = shutil.which("chrome")

    if found:
        return found


    raise FileNotFoundError(
        "Google Chrome was not found."
    )


# ============================================================
# FIND CHROME WINDOW
# ============================================================

def find_chrome_window():

    found_windows = []


    def callback(hwnd, lparam):

        if not user32.IsWindowVisible(hwnd):
            return True

        length = user32.GetWindowTextLengthW(hwnd)

        if length <= 0:
            return True

        buffer = ctypes.create_unicode_buffer(length + 1)

        user32.GetWindowTextW(
            hwnd,
            buffer,
            length + 1
        )

        title = buffer.value.strip()

        if "google chrome" in title.lower():
            found_windows.append((hwnd, title))

        return True


    callback_function = EnumWindowsProc(callback)

    user32.EnumWindows(
        callback_function,
        0
    )


    if found_windows:
        return found_windows[0]

    return None, None


# ============================================================
# ACTIVATE CHROME; MAXIMIZE ONLY WHEN NOT ALREADY MAXIMIZED
# ============================================================

def activate_chrome():

    """Bring an EXISTING Chrome window to the foreground without launching another."""

    hwnd, title = find_chrome_window()

    if not hwnd:
        return False

    print("Google Chrome window found.")

    if user32.IsIconic(hwnd):
        print("Chrome is minimized. Restoring...")
        # SW_RESTORE is 9. (2 means SW_SHOWMINIMIZED and would keep/minimize it.)
        user32.ShowWindow(hwnd, SW_RESTORE)
        # Wait until Windows reports that the Chrome window is no longer minimized.
        for _ in range(20):
            time.sleep(0.1)
            if not user32.IsIconic(hwnd):
                break

        if user32.IsIconic(hwnd):
            print("Windows did not restore the Chrome window.")
            return False

    # Keep the browser at a consistent full-screen size for screenshot-based
    # login detection and mouse coordinates. Do not repeatedly resize it.
    if not user32.IsZoomed(hwnd):
        user32.ShowWindow(hwnd, SW_MAXIMIZE)
        time.sleep(0.25)

    current_hwnd = user32.GetForegroundWindow()
    current_pid = wintypes.DWORD(0)
    chrome_pid = wintypes.DWORD(0)

    current_thread = 0
    chrome_thread = user32.GetWindowThreadProcessId(
        hwnd, ctypes.byref(chrome_pid)
    )
    if current_hwnd:
        current_thread = user32.GetWindowThreadProcessId(
            current_hwnd, ctypes.byref(current_pid)
        )

    my_thread = kernel32.GetCurrentThreadId()
    attached = []

    # Windows may reject SetForegroundWindow when the caller and Chrome
    # belong to different input threads. Temporarily attach input queues.
    for thread_id in (current_thread, chrome_thread):
        if thread_id and thread_id != my_thread and thread_id not in attached:
            if user32.AttachThreadInput(my_thread, thread_id, True):
                attached.append(thread_id)

    try:
        for _ in range(8):
            # Do NOT call SW_RESTORE here: it unmaximizes Chrome on every
            # activation and causes the browser to visibly resize/dance.
            # Restore/minimize handling and the one-time maximize above are
            # sufficient; this loop should only request foreground focus.
            user32.BringWindowToTop(hwnd)
            user32.SetActiveWindow(hwnd)
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.15)

            foreground = int(user32.GetForegroundWindow() or 0)
            if foreground == int(hwnd):
                return True
    finally:
        for thread_id in reversed(attached):
            user32.AttachThreadInput(my_thread, thread_id, False)

    print("Chrome exists, but Windows refused foreground activation.")
    return False


# ============================================================
# READ ACTIVE CHROME TAB URL WITHOUT CLIPBOARD
# ============================================================

def get_active_chrome_url():

    """Read the Chrome address bar through UI Automation; never use Ctrl+C."""

    try:
        hwnd, _ = find_chrome_window()
        if not hwnd:
            return ""

        win = get_chrome_uia_window(hwnd)
        return get_url(win)

    except Exception as exc:
        print("Could not read Chrome address bar:", exc)
        return ""


def verify_address_bar_focus():

    """Confirm focus is in a Chrome omnibox/edit control after Ctrl+L."""

    try:
        hwnd, _ = find_chrome_window()
        if not hwnd:
            return False

        # Never accept a focused edit control from another application.
        if int(user32.GetForegroundWindow() or 0) != int(hwnd):
            return False

        focused = Desktop(backend="uia").get_focus()
        info = focused.element_info
        control_type = str(
            getattr(info, "control_type", "") or ""
        ).lower()
        name = str(getattr(info, "name", "") or "").lower()
        class_name = str(getattr(info, "class_name", "") or "").lower()

        if control_type not in ("edit", "combobox", "custom"):
            return False

        # Chrome 156/157 may expose an unnamed Edit control. Ctrl+L has
        # already been sent while Chrome is confirmed foreground, so allow
        # that control even when its accessible name is blank.
        known_omnibox = any(marker in (name + " " + class_name) for marker in (
            "address", "omnibox", "search or enter", "search google",
            "chrome_omnibox", "omniboxview"
        ))
        if known_omnibox:
            return True

        # For unnamed controls, ensure UIA reports this control under the
        # Chrome window's process. This avoids accepting another app's field.
        try:
            process_id = int(getattr(info, "process_id", 0) or 0)
            if process_id and process_id == int(chrome_pid_for_window(hwnd)):
                return True
        except Exception:
            pass

        # Last compatibility fallback: foreground is Chrome and Ctrl+L
        # moved keyboard focus to an editable control.
        return not name and control_type in ("edit", "combobox")

    except Exception:
        return False


def chrome_pid_for_window(hwnd):
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def focus_and_verify_address_bar():

    if not activate_chrome():
        raise RuntimeError("Google Chrome could not be activated safely.")

    for attempt in range(1, 5):
        # Ensure Chrome is still the foreground window before every attempt.
        if not activate_chrome():
            time.sleep(0.5)
            continue

        pyautogui.hotkey("ctrl", "l")

        # Allow Chrome UI Automation time to expose the focused omnibox.
        for _ in range(12):
            time.sleep(0.2)
            if verify_address_bar_focus():
                print("Chrome address bar verified and focused.")
                return True

        print(f"Address bar focus check {attempt}/4 failed; retrying...")

    raise RuntimeError(
        "Chrome address bar could not be verified. URL was NOT pasted. "
        "Please make sure Chrome is the active window and try again."
    )


def paste_url_into_chrome(url):

    """Use Chrome's Ctrl+L shortcut directly, then replace and paste the URL.

    Chrome 156/157 does not consistently expose the omnibox focus through
    Windows UI Automation. The old UIA focus test could therefore reject a
    correctly focused address bar before the clipboard paste ever happened.
    """

    if not activate_chrome():
        raise RuntimeError("Google Chrome could not be activated safely.")

    # Confirm the existing Chrome window is actually foreground before
    # sending any keyboard input.
    hwnd, _ = find_chrome_window()
    if not hwnd or int(user32.GetForegroundWindow() or 0) != int(hwnd):
        raise RuntimeError(
            "Google Chrome is not the active window; URL was not pasted."
        )

    # Ctrl+L is Chrome's built-in address-bar shortcut. Do not block the
    # paste based on UI Automation's unreliable omnibox-focus reporting.
    pyautogui.hotkey("ctrl", "l")
    time.sleep(0.1)
    pyautogui.hotkey("ctrl", "a")
    pyperclip.copy(url)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.1)

    # If UIA can read the omnibox, verify the pasted URL. If it cannot read
    # it on this Chrome version, proceed because Ctrl+L was sent to the
    # confirmed foreground Chrome window.
    try:
        pasted_value = get_url(get_chrome_uia_window(hwnd))
    except Exception:
        pasted_value = ""

    if pasted_value and pasted_value.strip().rstrip("/") != url.strip().rstrip("/"):
        pyautogui.press("esc")
        raise RuntimeError(
            "Address bar text did not match the Shoonya URL. "
            "Navigation cancelled for safety."
        )

    pyautogui.press("enter")
    return True


# ============================================================
# CHROME UI AUTOMATION HELPERS
# ============================================================

def get_chrome_uia_window(hwnd=None):

    if hwnd is None:
        hwnd, _ = find_chrome_window()

    if not hwnd:
        raise RuntimeError(
            "Google Chrome window not found."
        )

    return Desktop(
        backend="uia"
    ).window(
        handle=int(hwnd)
    ).wrapper_object()


def get_url(win):

    for element in win.descendants(
        control_type="Edit"
    ):

        try:
            value = str(
                element.get_value() or ""
            ).strip()
        except Exception:
            continue


        try:
            name = (
                element.window_text() or ""
            ).lower()
        except Exception:
            name = ""


        if not value:
            continue


        if "address" in name:
            return value


        parsed = urlparse(value)

        if (
            parsed.scheme.lower()
            in ("http", "https", "chrome", "edge")
            and parsed.netloc
        ):
            return value


    return ""


def is_shoonya_url(url):

    """Return True only when a URL belongs to the configured Shoonya host."""

    try:
        parsed = urlparse(str(url or "").strip())
        hostname = (parsed.hostname or "").lower().rstrip(".")
        expected = SHOONYA_HOST.lower().rstrip(".")

        # Accept the configured host and its genuine subdomains only.
        return bool(
            parsed.scheme.lower() in ("http", "https")
            and (hostname == expected or hostname.endswith("." + expected))
        )

    except Exception:
        return False


def get_chrome_tabs(win):

    return win.descendants(
        control_type="TabItem"
    )


def activate_chrome_tab(tab_number):

    if tab_number < 1:
        raise ValueError("Invalid Chrome tab number.")

    win = get_chrome_uia_window()

    tabs = get_chrome_tabs(win)

    if tab_number > len(tabs):

        raise RuntimeError(
            f"Chrome tab {tab_number} no longer exists. "
            f"Current tab count: {len(tabs)}."
        )

    win.set_focus()

    tabs[tab_number - 1].click_input()

    time.sleep(0.5)


# ============================================================
# EXTENSION TAB SCAN
# ============================================================

def scan_chrome_tabs(timeout=1.5):
    """Request one extension tab report; return quickly if the extension is unavailable."""
    global scan_requested, tab_snapshot

    if not find_chrome_window()[0]:
        raise RuntimeError("Google Chrome window not found.")
    if not activate_chrome():
        raise RuntimeError("Could not activate Google Chrome.")

    tab_snapshot = None
    tab_snapshot_event.clear()
    with scan_lock:
        scan_requested = True

    print("\nRequesting a quick Chrome tab report from the already-loaded extension...")
    if not tab_snapshot_event.wait(timeout):
        with scan_lock:
            scan_requested = False
        raise RuntimeError("No tab report received from the already-loaded Shoonya extension.")

    report = tab_snapshot or {}
    tabs = report.get("tabs", [])
    shoonya = report.get("shoonya")
    if not tabs:
        raise RuntimeError("The extension returned an empty Chrome tab list.")

    shoonya_matches = []
    for tab in tabs:
        url = str(tab.get("url", "") or "")
        if url.lower().startswith(("chrome://", "chrome-extension://", "devtools://")):
            continue
        if is_shoonya_url(url):
            shoonya_matches.append(tab)

    if shoonya_matches:
        shoonya_matches.sort(
            key=lambda item: (not bool(item.get("active")), int(item.get("number", 999999)))
        )
        positions = ", ".join(
            f"{item.get('number', '?')}" + (" (ACTIVE)" if item.get("active") else "")
            for item in shoonya_matches
        )
        print(f"Shoonya tabs found at Chrome tab positions: {positions}")

    if shoonya:
        print(f"Selected Shoonya tab position: {shoonya.get('number')}.")
        return len(tabs), int(shoonya.get("number"))
    return len(tabs), None


# ============================================================
# OPEN SHOONYA IN A NEW TAB
# ============================================================

def open_shoonya_new_tab():
    """Open a dedicated Shoonya tab and proceed after the requested 2-second wait."""
    print("\nOpening a NEW dedicated Shoonya tab; existing Chrome tabs remain untouched...")
    if not activate_chrome():
        raise RuntimeError("Chrome is not in the foreground; refusing to send keyboard shortcuts.")
    pyautogui.hotkey("ctrl", "t")
    time.sleep(0.2)
    paste_url_into_chrome(SHOONYA_URL)
    print("Shoonya URL pasted. Waiting exactly 2 seconds...")
    time.sleep(2.0)
    print("2 seconds completed. Login screenshot and form detection will start immediately.")


def open_google_chrome():
    """Reuse an existing Shoonya tab by its exact Chrome tab position.

    If several Shoonya tabs exist, the active one wins; otherwise the left-most
    Shoonya tab wins. A new tab is opened only when no Shoonya tab is reported.
    """
    chrome_path = find_chrome()
    print("\nGoogle Chrome:", chrome_path)
    hwnd, _ = find_chrome_window()
    if hwnd:
        if not activate_chrome():
            raise RuntimeError("A Google Chrome window exists but Windows would not activate it.")
        try:
            tab_count, tab_number = scan_chrome_tabs(timeout=1.5)
            if tab_number:
                print(
                    f"Reusing existing Shoonya tab {tab_number} of {tab_count}; "
                    "no duplicate Shoonya tab will be opened."
                )
                activate_chrome_tab(tab_number)
                return
        except Exception as exc:
            print(
                "Could not get the existing tab-position report yet: "
                f"{type(exc).__name__}. Continuing with the normal tab-opening fallback."
            )
        open_shoonya_new_tab()
        return
    print("No existing Chrome window found. Opening Google Chrome...")
    subprocess.Popen([chrome_path, "--new-window", "about:blank"])
    deadline = time.time() + 35
    while time.time() < deadline:
        time.sleep(0.5)
        hwnd, _ = find_chrome_window()
        if hwnd and activate_chrome():
            open_shoonya_new_tab()
            return
    raise RuntimeError("No Chrome window appeared after launch.")


# ============================================================
# JKEY AND TAB RECEIVER
# ============================================================

class JKeyHandler(BaseHTTPRequestHandler):

    def end_headers(self):
        # Only browser-extension origins are allowed to call this loopback receiver.
        origin = self.headers.get("Origin", "")
        if origin.startswith("chrome-extension://"):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_GET(self):

        global scan_requested
        global reload_requested


        if self.path != "/scan":

            self.send_response(404)
            self.end_headers()

            return


        with scan_lock:

            requested_scan = scan_requested
            requested_reload = reload_requested

            if requested_scan:
                scan_requested = False

            if requested_reload:
                reload_requested = False


        payload = json.dumps({

            "scan": requested_scan,

            "reload": requested_reload

        }).encode("utf-8")


        self.send_response(200)

        self.send_header(
            "Content-Type",
            "application/json"
        )

        self.send_header(
            "Content-Length",
            str(len(payload))
        )

        self.end_headers()

        self.wfile.write(payload)


    def do_POST(self):

        global captured_jkey
        global extension_heartbeat_info
        global tab_snapshot
        global reload_result
        global candidate_request_count
        global jkey_diagnostics

        # ----------------------------------------------------
        # EXTENSION HEARTBEAT
        # ----------------------------------------------------

        if self.path == "/heartbeat":
            try:
                length = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                extension_heartbeat_info = {
                    "extension": str(data.get("extension", ""))[:100],
                    "version": str(data.get("version", ""))[:30],
                    "time": str(data.get("time", ""))[:50]
                }
                extension_heartbeat_event.set()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OK")
            except Exception:
                self.send_response(400)
                self.end_headers()
            return

        # ----------------------------------------------------
        # TAB REPORT AND RELOAD RESULT
        # ----------------------------------------------------

        if self.path == "/tabs":

            try:
                length = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(length).decode("utf-8"))

                if (
                    not isinstance(data, dict)
                    or not isinstance(data.get("tabs"), list)
                ):
                    raise ValueError("Invalid tab report")

                tab_snapshot = data
                reload_result = data.get("reload_status", "NOT_REPORTED")
                tab_snapshot_event.set()
                reload_event.set()

                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OK")

            except Exception:
                self.send_response(400)
                self.end_headers()

            return

        # ----------------------------------------------------
        # SAFE REQUEST DIAGNOSTICS
        # ----------------------------------------------------

        if self.path == "/candidate":

            try:
                length = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(length).decode("utf-8"))

                if not isinstance(data, dict):
                    raise ValueError("Invalid diagnostic")

                safe = {
                    "host": str(data.get("host", ""))[:200],
                    "path": str(data.get("path", ""))[:500],
                    "method": str(data.get("method", ""))[:20],
                    "resource_type": str(data.get("resource_type", ""))[:40],
                    "form_keys": [
                        str(x)[:40] for x in data.get("form_keys", [])[:30]
                    ] if isinstance(data.get("form_keys", []), list) else [],
                    "has_raw_body": bool(data.get("has_raw_body", False)),
                    "time": str(data.get("time", ""))[:50],
                    "jkey_found": False
                }

                candidate_request_count += 1
                jkey_diagnostics.append(safe)
                jkey_diagnostics = jkey_diagnostics[-30:]

                print(
                    "Shoonya request observed (no jKey found): "
                    f"{safe['method']} {safe['host']}{safe['path']} | "
                    f"form fields={safe['form_keys']} | "
                    f"raw body={safe['has_raw_body']}"
                )

                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OK")

            except Exception:
                self.send_response(400)
                self.end_headers()

            return

        # ----------------------------------------------------
        # JKEY REPORT
        # ----------------------------------------------------

        if self.path != "/jkey":
            self.send_response(404)
            self.end_headers()
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > 65536:
                self.send_response(400)
                self.end_headers()
                return
            body = self.rfile.read(length)
            data = json.loads(body.decode("utf-8"))

            jkey = str(data.get("jkey", ""))
            request_host = str(data.get("request_host", "")).lower().strip()
            request_path = str(data.get("request_path", ""))

            valid_shoonya_host = (
                request_host == "shoonya.com"
                or request_host.endswith(".shoonya.com")
            )
            if not valid_shoonya_host:
                self.send_response(400)
                self.end_headers()
                return

            # Host ownership is validated above. Different Shoonya web builds
            # may use different Noren paths, so do not reject a real jKey solely
            # because the endpoint path name changed.
            if len(jkey) < 12:
                self.send_response(400)
                self.end_headers()
                return

            captured_jkey = jkey
            jkey_event.set()

            print(
                "Valid jKey captured from Shoonya request. "
                               "Value hidden."
            )

            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

        except Exception:
            self.send_response(400)
            self.end_headers()

    def log_message(self, format, *args):
        pass


# ============================================================
# START LOCAL RECEIVER
# ============================================================

def start_jkey_receiver():

    server = ThreadingHTTPServer(
        (RECEIVER_HOST, RECEIVER_PORT),
        JKeyHandler
    )

    thread = threading.Thread(
        target=server.serve_forever,
        daemon=True
    )

    thread.start()

    print("jKey receiver started.")

    return server


# ============================================================
# READ EXCEL CREDENTIALS
# ============================================================

def open_excel_workbook_first():

    """Open/attach to symbols.xlsx in desktop Excel before doing anything else."""

    global excel_book

    if not EXCEL_FILE.exists():
        raise FileNotFoundError(f"Excel file not found: {EXCEL_FILE}")

    print("Opening Excel workbook first...")
    excel_book = xw.Book(str(EXCEL_FILE))
    try:
        excel_book.app.visible = True
        excel_book.app.screen_updating = True
        ws = excel_book.sheets[SHEET_NAME]
        ws.activate()
        print("Excel workbook opened:", EXCEL_FILE.name)
        print("Reading credentials and stored jKey directly from the open workbook.")
    except Exception:
        raise

    return excel_book


def read_credentials():

    """Read credentials from the already-open Excel workbook."""

    global excel_book
    if excel_book is None:
        open_excel_workbook_first()

    ws = excel_book.sheets[SHEET_NAME]
    user_id = ws.range("B3").value
    password = ws.range("B4").value
    totp_secret = ws.range("B5").value

    if user_id is None or password is None or totp_secret is None:
        raise ValueError("Check LOGIN!B3, B4 and B5 in the open Excel workbook.")

    user_id = str(user_id).strip()
    password = str(password)
    totp_secret = str(totp_secret).strip().replace(" ", "")

    if not user_id or not password or not totp_secret:
        raise ValueError("LOGIN!B3, B4 and B5 must not be blank.")

    return user_id, password, totp_secret


# ============================================================
# DETECT LOGIN RECTANGLES
# ============================================================

def detect_rectangles(image):

    gray = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2GRAY
    )

    edges = cv2.Canny(
        gray,
        40,
        130
    )

    contours, _ = cv2.findContours(
        edges,
        cv2.RETR_LIST,
        cv2.CHAIN_APPROX_SIMPLE
    )

    screen_height, screen_width = gray.shape

    rectangles = []


    for contour in contours:

        x, y, w, h = cv2.boundingRect(contour)

        if not (180 <= w <= 700):
            continue

        if not (25 <= h <= 85):
            continue

        if not (
            0.15 * screen_height
            <= y
            <= 0.90 * screen_height
        ):
            continue

        ratio = w / float(h)

        if not (3 <= ratio <= 15):
            continue

        rectangles.append(
            (x, y, w, h)
        )


    rectangles.sort(
        key=lambda r: r[2] * r[3],
        reverse=True
    )

    filtered = []


    for rect in rectangles:

        x, y, w, h = rect

        duplicate = False


        for old in filtered:

            ox, oy, ow, oh = old

            ix1 = max(x, ox)
            iy1 = max(y, oy)

            ix2 = min(x + w, ox + ow)
            iy2 = min(y + h, oy + oh)


            intersection = (
                max(0, ix2 - ix1)
                * max(0, iy2 - iy1)
            )


            union = (
                w * h
                + ow * oh
                - intersection
            )


            iou = (
                intersection / union
                if union
                else 0
            )


            if iou > 0.55:

                duplicate = True

                break


        if not duplicate:
            filtered.append(rect)


    filtered.sort(
        key=lambda r: r[1]
    )

    return filtered


# ============================================================
# VALIDATE LOGIN POSITIONS
# ============================================================

def validate_login_positions(rectangles):

    if len(rectangles) == 3:
        # v4: the Login button was missed. Keep the 3 input boxes (sorted top
        # to bottom) and place the Login button just below the TOTP box.
        rectangles = sorted(rectangles, key=lambda r: r[1])
        x, y, w, h = rectangles[2]
        gap = rectangles[2][1] - rectangles[1][1]
        gap = gap if 40 <= gap <= 140 else 85
        rectangles = list(rectangles) + [(x, y + gap, w, h)]
        print("v4: only 3 boxes found; Login button position estimated.")

    if len(rectangles) != 4:
        return None


    positions = []


    for x, y, w, h in rectangles:

        positions.append(
            (x + w // 2, y + h // 2)
        )


    field_x_positions = [
        positions[i][0]
        for i in range(3)
    ]


    if (
        max(field_x_positions)
        - min(field_x_positions)
        > 30
    ):

        return None


    if not (
        positions[0][1]
        < positions[1][1]
        < positions[2][1]
        < positions[3][1]
    ):

        return None


    return positions


def create_screenshot_folder():
    """Use one reusable Desktop folder; remove stale folders from older runs."""
    global SCREENSHOT_DIR

    if SCREENSHOT_DIR is not None and SCREENSHOT_DIR.exists():
        return SCREENSHOT_DIR

    desktop = Path.home() / "Desktop"
    if not desktop.exists():
        desktop = BASE_DIR

    # Remove only this script's previous timestamped debug folders. They
    # contain temporary screenshots, not user documents or workbook data.
    for old_folder in desktop.glob("Shoonya_JKEY_Debug_*"):
        if old_folder.is_dir():
            try:
                shutil.rmtree(old_folder)
            except Exception:
                pass

    folder = desktop / "Shoonya_JKEY_Debug"
    if folder.exists():
        try:
            shutil.rmtree(folder)
        except Exception:
            pass

    folder.mkdir(parents=True, exist_ok=True)
    SCREENSHOT_DIR = folder

    print("\nSingle temporary screenshot folder:")
    print(SCREENSHOT_DIR)
    return SCREENSHOT_DIR


def screenshot_file(filename):
    """Return a path inside the current run's screenshot folder."""
    folder = SCREENSHOT_DIR if SCREENSHOT_DIR is not None else BASE_DIR
    folder.mkdir(parents=True, exist_ok=True)
    return folder / filename


def delete_screenshot_folder_after_success():
    """Delete only this run's evidence folder after a VALID jKey is saved."""
    global SCREENSHOT_DIR

    if SCREENSHOT_DIR is None:
        return

    folder = SCREENSHOT_DIR
    try:
        if folder.exists() and folder.is_dir():
            shutil.rmtree(folder)
            print("\\nSUCCESS: temporary screenshot folder deleted:")
            print(folder)
        SCREENSHOT_DIR = None
    except Exception as exc:
        print("WARNING: could not delete temporary screenshot folder:",
              type(exc).__name__)
        print("You can remove this folder manually after checking it:", folder)


# ============================================================
# SCREENSHOT AND LOGIN DETECTION
# ============================================================

def capture_and_detect():

    global login_field_rectangles

    print("\nChecking Shoonya login form...")


    screenshot_path = (
        screenshot_file("shoonya_login_screen.png")
    )


    for attempt in range(
        1,
        DETECTION_ATTEMPTS + 1
    ):

        print(
            f"Detection attempt {attempt}/{DETECTION_ATTEMPTS}..."
        )


        if not activate_chrome():

            raise RuntimeError(
                "Chrome window not found."
            )


        time.sleep(0.1)


        screenshot = pyautogui.screenshot()

        screenshot.save(screenshot_path)


        image = cv2.imread(
            str(screenshot_path)
        )


        if image is None:

            raise RuntimeError(
                "Could not read screenshot."
            )


        rectangles = detect_rectangles(image)


        print(
            "Detected rectangles:",
            len(rectangles)
        )


        positions = validate_login_positions(
            rectangles
        )


        if positions:

            login_field_rectangles = list(rectangles[:3])
            print("\nLOGIN FORM DETECTED.")


            annotated = image.copy()


            names = [
                "USER ID",
                "PASSWORD",
                "TOTP",
                "LOGIN BUTTON"
            ]


            report_lines = []


            for name, rect, center in zip(
                names,
                rectangles,
                positions
            ):

                x, y, w, h = rect

                cx, cy = center


                print(
                    f"{name}: center=({cx}, {cy}), "
                    f"box=({x}, {y}, {w}, {h})"
                )


                report_lines.append(
                    f"{name}: center=({cx}, {cy}), "
                    f"box=({x}, {y}, {w}, {h})"
                )


                cv2.rectangle(
                    annotated,
                    (x, y),
                    (x + w, y + h),
                    (0, 255, 0),
                    2
                )


                cv2.putText(
                    annotated,
                    name,
                    (x, max(20, y - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0, 0, 255),
                    2
                )


            detected_path = screenshot_file("shoonya_detected_boxes.png")
            cv2.imwrite(str(detected_path), annotated)
            print("Annotated login screenshot saved:", detected_path)
            print("Field order identified top-to-bottom: USER ID, PASSWORD, TOTP, LOGIN BUTTON.")


            with open(
                screenshot_file("shoonya_login_positions.txt"),
                "w",
                encoding="utf-8"
            ) as f:

                f.write(
                    "\n".join(report_lines)
                )


            return positions


        print("Login form not detected.")


        if attempt < DETECTION_ATTEMPTS:

            time.sleep(
                DETECTION_INTERVAL
            )


    print("\nLogin form not detected.")

    return None


# ============================================================
# CLEAR AND PASTE
# ============================================================

def _chrome_foreground_handle():

    hwnd, _ = find_chrome_window()
    foreground = int(user32.GetForegroundWindow() or 0)
    if not hwnd or foreground != int(hwnd):
        raise RuntimeError("Safety stop: Chrome is not the active foreground window.")
    return int(hwnd)


def _wait_until_resumed(reason="User interruption"):

    """Wait safely; automatically restore Chrome if it was minimized mid-run.

    If another application merely has focus, preserve the old safe behaviour
    and wait for Chrome to be returned to the foreground. If Chrome itself is
    minimized, restore/maximize it once and then continue automatically.
    """

    global last_expected_mouse

    print(f"\nPAUSED: {reason}")
    print("Checking Chrome state and waiting for a safe resume...")

    stable_position = None
    stable_since = None
    restore_notice_shown = False

    while True:
        try:
            esc_down = bool(user32.GetAsyncKeyState(0x1B) & 0x8000)

            hwnd, _ = find_chrome_window()
            if hwnd and user32.IsIconic(hwnd):
                if not restore_notice_shown:
                    print("Chrome became minimized during the process. Auto-restoring Chrome...")
                    restore_notice_shown = True
                # activate_chrome restores only when iconic and does not
                # repeatedly SW_RESTORE an already-maximized window.
                activate_chrome()
                stable_position = None
                stable_since = None

            foreground_ok = False
            try:
                _chrome_foreground_handle()
                foreground_ok = True
            except Exception:
                foreground_ok = False

            try:
                pos = pyautogui.position()
                current = (int(pos.x), int(pos.y))
            except Exception:
                current = None

            if current is not None and current == stable_position:
                if stable_since is None:
                    stable_since = time.monotonic()
            else:
                stable_position = current
                stable_since = time.monotonic() if current is not None else None

            mouse_stable = (
                stable_since is not None
                and time.monotonic() - stable_since >= 0.7
            )
            at_failsafe_corner = (
                current is not None
                and current in getattr(pyautogui, "FAILSAFE_POINTS", [])
            )

            if not esc_down and foreground_ok and mouse_stable and not at_failsafe_corner:
                last_expected_mouse = current
                print("RESUMED: Chrome is active; continuing with URL/tab verification.")
                time.sleep(0.15)
                return current

        except pyautogui.FailSafeException:
            pass
        except Exception:
            pass

        time.sleep(0.12)


def _check_user_interrupt(expected_mouse=None):

    """Return True after an interruption has been resolved; never raise for it."""

    global last_expected_mouse

    try:
        _chrome_foreground_handle()
    except Exception:
        _wait_until_resumed("Chrome lost foreground focus")
        return True

    if user32.GetAsyncKeyState(0x1B) & 0x8000:
        _wait_until_resumed("ESC pressed")
        return True

    if expected_mouse is None:
        expected_mouse = last_expected_mouse

    try:
        current = pyautogui.position()
    except Exception:
        _wait_until_resumed("Mouse state could not be checked")
        return True

    if expected_mouse is not None:
        dx = abs(current.x - expected_mouse[0])
        dy = abs(current.y - expected_mouse[1])
        if dx > 12 or dy > 12:
            _wait_until_resumed("Mouse moved during protected entry")
            return True

    return False


def save_login_checkpoint(label, suffix):

    """Save a visual checkpoint while blurring all credential input areas."""

    from PIL import ImageFilter

    shot = pyautogui.screenshot()
    for x, y, w, h in login_field_rectangles:
        left = max(0, x - 4)
        top = max(0, y - 4)
        right = min(shot.width, x + w + 4)
        bottom = min(shot.height, y + h + 4)
        if right > left and bottom > top:
            region = shot.crop((left, top, right, bottom))
            region = region.filter(ImageFilter.GaussianBlur(radius=10))
            shot.paste(region, (left, top))

    path = screenshot_file(f"shoonya_{suffix}.png")
    shot.save(str(path))
    print(f"Screenshot saved ({label}); credential fields blurred: {path.name}")


def ensure_verified_shoonya_tab_for_input(label):
    """Verify the exact Shoonya URL once before the fast credential sequence."""
    print(f"\nFAST URL/TAB verification before {label}...")
    last_error = None

    while True:
        try:
            tab_count, tab_number = scan_chrome_tabs(timeout=2.5)
            report = tab_snapshot or {}
            tabs = report.get("tabs", [])
            matches = []
            for tab in tabs:
                raw_url = str(tab.get("url", "")).strip()
                try:
                    parsed = urlparse(raw_url)
                    if (
                        parsed.scheme.lower() == "https"
                        and (parsed.hostname or "").lower().rstrip(".") == SHOONYA_HOST
                    ):
                        matches.append((int(tab.get("number", 0)), raw_url))
                except Exception:
                    continue

            if not matches:
                raise RuntimeError("No exact HTTPS Shoonya tab was reported.")

            match_details = []
            for tab in tabs:
                try:
                    parsed = urlparse(str(tab.get("url", "")).strip())
                    if parsed.scheme.lower() == "https" and (parsed.hostname or "").lower().rstrip(".") == SHOONYA_HOST:
                        match_details.append((
                            int(tab.get("number", 0)),
                            str(tab.get("url", "")),
                            bool(tab.get("active"))
                        ))
                except Exception:
                    continue
            match_details.sort(key=lambda item: (not item[2], item[0]))
            chosen = (match_details[0][0], match_details[0][1]) if match_details else next(
                (item for item in matches if item[0] == int(tab_number or 0)),
                matches[0]
            )
            verified_number, verified_url = chosen
            _chrome_foreground_handle()
            print(f"TARGET VERIFIED: Chrome tab {verified_number}/{tab_count}")
            print(f"Verified URL: {verified_url}")
            return verified_number, verified_url

        except Exception as exc:
            last_error = exc
            print(
                f"URL verification temporarily unavailable for {label}: "
                f"{type(exc).__name__}; retrying safely."
            )
            # A minimized Chrome window is restored by activate_chrome() on
            # the next scan. Do not wait for a manual resume for a scan timeout.
            time.sleep(0.25)


def clear_and_paste(label, x, y, value, tab_already_verified=False):
    """Paste a field quickly; reuse the verified Shoonya tab for this form."""
    global last_expected_mouse
    tab_verified = bool(tab_already_verified)

    while True:
        print(f"\nEntering {label}...")
        if _check_user_interrupt():
            tab_verified = bool(tab_already_verified)
            continue

        try:
            if not tab_verified:
                ensure_verified_shoonya_tab_for_input(label)
                tab_verified = True
            else:
                # No repeated extension round-trip between USER ID/PASSWORD/TOTP.
                # Still refuse keyboard input unless Chrome remains foreground.
                try:
                    _chrome_foreground_handle()
                except Exception:
                    _wait_until_resumed(f"Chrome focus lost before {label}")
                    if not tab_already_verified:
                        ensure_verified_shoonya_tab_for_input(label)

            if _check_user_interrupt():
                tab_verified = bool(tab_already_verified)
                continue

            pyautogui.click(x, y)
            time.sleep(0.1)
            expected_mouse = (x, y)
            if _check_user_interrupt(expected_mouse):
                tab_verified = bool(tab_already_verified)
                continue

            pyautogui.hotkey("ctrl", "a")
            time.sleep(0.1)
            pyautogui.press("backspace")
            time.sleep(0.1)

            field_value = value() if callable(value) else value
            if _check_user_interrupt(expected_mouse):
                tab_verified = bool(tab_already_verified)
                continue

            pyperclip.copy(str(field_value))
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.1)
            if _check_user_interrupt(expected_mouse):
                tab_verified = bool(tab_already_verified)
                continue

            # The login-form screenshot was already saved during detection.
            # Skip slow per-field screenshots so the three credentials are entered consecutively.
            last_expected_mouse = expected_mouse
            print(f"{label}: entered; value is hidden.")
            return

        except pyautogui.FailSafeException:
            tab_verified = bool(tab_already_verified)
            _wait_until_resumed("PyAutoGUI safety corner reached")
            continue
        except Exception as exc:
            tab_verified = bool(tab_already_verified)
            print(f"Temporary {label} entry issue: {type(exc).__name__}")
            _wait_until_resumed(f"Temporary problem while entering {label}")
            continue


# ============================================================
# REQUEST REAL SHOONYA TAB RELOAD THROUGH EXTENSION
# ============================================================

def refresh_logged_in_shoonya():

    """Ask the extension to reload the exact Shoonya tab after URL verification.

    Never use Ctrl+R here: Chrome focus can move to another tab (for example,
    Chrome extensions settings page). The extension reads chrome.tabs URLs, selects the
    exact trade.shoonya.com tab by tab ID, reloads that tab, and reports the
    resulting URL/status back to Python.
    """

    global scan_requested
    global reload_requested
    global tab_snapshot
    global reload_result

    print("\nShoonya login form was not detected.")
    print("Checking the actual Chrome tab URL before requesting a refresh...")

    if jkey_event.is_set():
        print("A jKey request was already captured.")
        return

    # First request a fresh extension tab scan and inspect its exact URL report.
    try:
        scan_chrome_tabs()
    except Exception as exc:
        print("Could not verify Chrome tab URLs yet:", type(exc).__name__)
        print("Will retry URL verification; no other tab will be refreshed.")
        return

    report = tab_snapshot or {}
    tabs = report.get("tabs", [])
    shoonya_tabs = [tab for tab in tabs if is_shoonya_url(tab.get("url", ""))]

    if not shoonya_tabs:
        print("URL CHECK FAILED: no tab currently has the exact Shoonya host.")
        print("No refresh sent. The next retry cycle will scan all tab URLs again.")
        return

    # The extension's current matching rule is trade.shoonya.com. Refuse a
    # look-alike domain and require the exact expected hostname here too.
    verified = []
    for tab in shoonya_tabs:
        try:
            parsed = urlparse(str(tab.get("url", "")))
            if parsed.scheme.lower() == "https" and (parsed.hostname or "").lower().rstrip(".") == SHOONYA_HOST:
                verified.append(tab)
        except Exception:
            continue

    if not verified:
        print("URL CHECK FAILED: tab URL is not https://trade.shoonya.com.")
        print("No refresh sent; waiting for a verified Shoonya URL.")
        return

    verified.sort(
        key=lambda item: (not bool(item.get("active")), int(item.get("number", 999999)))
    )
    target = verified[0]
    print(
        f"Selected Shoonya tab position {target.get('number')} "
        f"(active={bool(target.get('active'))})."
    )
    print("Verified Shoonya tab URL:", target.get("url", ""))
    print("Requesting extension to reload this exact tab by Chrome tab ID...")

    # Ask the extension's chrome.tabs API to reload the verified Shoonya tab.
    # The extension independently checks the hostname again before reload.
    tab_snapshot_event.clear()
    reload_event.clear()
    reload_result = None
    with scan_lock:
        reload_requested = True
        scan_requested = True

    if not reload_event.wait(12):
        print("No reload confirmation received from the extension yet.")
        print("Will rescan and retry. No Ctrl+R fallback is used.")
        return

    result = tab_snapshot or {}
    status = str(result.get("reload_status", "NOT_REPORTED"))
    refreshed_tabs = result.get("tabs", [])
    refreshed_urls = [
        str(tab.get("url", "")) for tab in refreshed_tabs
        if is_shoonya_url(tab.get("url", ""))
    ]

    print("Extension reload status:", status)
    for refreshed_url in refreshed_urls:
        print("Shoonya URL after reload:", refreshed_url)

    if status == "RELOAD_COMPLETE" and refreshed_urls:
        print("URL-verified Shoonya tab refreshed successfully.")
        print("Waiting for fresh Shoonya API requests...")
    else:
        print("Shoonya tab reload was not confirmed. Keeping the retry loop active.")


# ============================================================
# READ EXISTING ACTIVE JKEY FROM EXCEL
# ============================================================

def read_existing_jkey_from_excel():

    """Read LOGIN!B20 from the same open Excel workbook, including unsaved edits."""

    global excel_book
    if excel_book is None:
        open_excel_workbook_first()

    value = excel_book.sheets[SHEET_NAME].range("B20").value
    return str(value).strip() if value is not None else ""


# ============================================================
# VALIDATE WEB JKEY USING SHOONYA WEB TERMINAL SESSION
# ============================================================

def validate_web_jkey(user_id, password, jkey):

    """Validate the captured browser jKey using a read-only Order Book call.

    Important: NorenRestApiPy.get_order_book() may return None for a
    non-list/error response. None is UNKNOWN, not proof of an expired key.
    A list response means the web session was accepted by Shoonya.
    """

    print("\nChecking captured web jKey with Shoonya Order Book...")

    try:
        api = NorenApi(
            host="https://trade.shoonya.com/NorenWClientWeb/",
            websocket="wss://trade.shoonya.com/NorenWSWeb/"
        )

        api.set_session(user_id, password, jkey)

        # The SDK can occasionally return None while the server/session is
        # still responding. Retry a small number of times, but never call
        # None an invalid session.
        last_response_type = "No response"
        for attempt in range(1, 4):
            result = api.get_order_book()
            last_response_type = type(result).__name__

            if isinstance(result, list):
                print("RESULT: VALID")
                print("Shoonya returned an Order Book list response.")
                return "VALID", "Order Book list response received"

            if isinstance(result, dict):
                stat = str(result.get("stat", "")).strip().upper()
                message = str(
                    result.get("emsg") or result.get("message") or result
                )
                message_upper = message.upper()

                if stat == "OK":
                    print("RESULT: VALID")
                    return "VALID", message[:500]

                invalid_markers = (
                    "INVALID SESSION", "SESSION EXPIRED",
                    "INVALID SESSION KEY", "SESSION NOT FOUND",
                    "INVALID TOKEN", "TOKEN EXPIRED"
                )
                if any(marker in message_upper for marker in invalid_markers):
                    print("RESULT: INVALID / EXPIRED")
                    return "INVALID", message[:500]

                print("Shoonya returned a response that does not confirm validity.")
                print("Response:", message[:500])
                return "UNKNOWN", message[:500]

            if result is None:
                print(
                    f"Order Book returned None (attempt {attempt}/3). "
                    "This is UNKNOWN, not automatically invalid."
                )
                if attempt < 3:
                    time.sleep(2)
                continue

            print("Unexpected response type:", last_response_type)
            return "UNKNOWN", f"Unexpected response type: {last_response_type}"

        return "UNKNOWN", "Order Book returned None after 3 attempts"

    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        message_upper = message.upper()
        invalid_markers = (
            "INVALID SESSION", "SESSION EXPIRED", "INVALID SESSION KEY",
            "SESSION NOT FOUND", "INVALID TOKEN", "TOKEN EXPIRED"
        )
        if any(marker in message_upper for marker in invalid_markers):
            print("RESULT: INVALID / EXPIRED")
            return "INVALID", message[:500]

        print("RESULT: UNKNOWN - VALIDATION ERROR")
        print(message)
        return "UNKNOWN", message[:500]


# ============================================================
# SAVE LAST CAPTURED JKEY AND VALIDATION RESULT TO EXCEL
# ============================================================

def save_jkey_validation_to_excel(jkey, status, detail):

    print("\nSaving jKey validation result to Excel...")

    global excel_book
    if excel_book is None:
        open_excel_workbook_first()
    wb = excel_book
    try:
        ws = wb.sheets[SHEET_NAME]

        # Keep the last captured candidate for audit/troubleshooting.
        ws.range("A20").value = "WEB_JKEY"
        ws.range("A21").value = "LAST_CAPTURED_WEB_JKEY"
        ws.range("A22").value = "WEB_JKEY_STATUS"
        ws.range("A23").value = "WEB_JKEY_CHECKED_AT"
        ws.range("A24").value = "WEB_JKEY_VALIDATION_DETAILS"

        ws.range("B21").value = jkey
        ws.range("B22").value = status
        ws.range("B23").value = time.strftime("%Y-%m-%d %H:%M:%S")
        ws.range("B24").value = str(detail)[:500]

        if status == "VALID":
            ws.range("B20").value = jkey
            print("Verified jKey saved to LOGIN!B20.")
        elif status == "INVALID":
            # Do not leave a known-invalid key in the active-key cell.
            ws.range("B20").clear_contents()
            print("Known-invalid jKey cleared from LOGIN!B20.")
        else:
            # UNKNOWN must not overwrite or clear the previously stored key.
            print("Validation UNKNOWN: LOGIN!B20 left unchanged.")

        wb.save()
    finally:
        pass

    print("Last captured jKey and validation status saved in LOGIN!B21:B24.")
    print("jKey values are hidden in the console.")


# ============================================================
# MAIN
# ============================================================

def main():

    global captured_jkey
    global last_expected_mouse

    server = None


    print("=" * 60)

    print(
        "SHOONYA AUTO LOGIN + POSITION-AWARE MULTI-TAB SELECTION + FAST LOGIN + INDEFINITE JKEY RETRY (V33)"
    )

    print("=" * 60)


    try:

        # Reset state for a fresh run.
        captured_jkey = None

        jkey_event.clear()

        tab_snapshot_event.clear()

        reload_event.clear()


        print("\nSTEP 1: OPEN EXCEL AND CHECK STORED JKEY FIRST")
        open_excel_workbook_first()

        print("\nReading Excel credentials...")
        user_id, password, totp_secret = read_credentials()


        print(
            "Excel credentials loaded."
        )

        # ----------------------------------------------------
        # FIRST: VALIDATE THE JKEY ALREADY STORED IN LOGIN!B20
        # ----------------------------------------------------
        print("\nChecking existing jKey in Excel LOGIN!B20 before browser automation...")
        existing_jkey = read_existing_jkey_from_excel()

        if existing_jkey:
            print("Existing jKey found. Checking it with Shoonya web Order Book...")
            existing_status, existing_detail = validate_web_jkey(
                user_id,
                password,
                existing_jkey
            )

            save_jkey_validation_to_excel(
                existing_jkey,
                existing_status,
                existing_detail
            )

            if existing_status == "VALID":
                print("\nExisting jKey is VALID. No browser automation is needed.")
                print("Chrome will not be opened, refreshed, or changed.")
                print("No screenshots, login form detection, TOTP, or new jKey capture.")
                return

            if existing_status == "INVALID":
                print("\nExisting jKey is INVALID/EXPIRED. Starting browser login and fresh jKey capture...")
            else:
                print("\nExisting jKey status is UNKNOWN. It is not confirmed valid.")
                print("Continuing with browser automation to capture and validate a fresh jKey...")
        else:
            print("No existing jKey found in LOGIN!B20. Starting browser login and capture...")

        # Only create/update the extension and start Chrome automation
        # after the stored jKey is missing, invalid, expired, or unconfirmed.
        create_screenshot_folder()
        write_extension_files()

        print("\nShoonya extension folder checked:")
        print(EXT_DIR)
        print("Existing extension files are preserved when already present.")
        print("The script will NOT open or change Chrome extension settings.")

        # Start receiver only when a new browser capture is required.
        server = start_jkey_receiver()


        # Open or reuse Chrome.
        open_google_chrome()

        # Never open Chrome extension-management screens or click extension controls.
        # An already-installed extension observes Shoonya web requests directly.
        print("Chrome extension management pages will NOT be opened or modified.")

        # ----------------------------------------------------
        # INDEFINITE LOGIN + JKEY CAPTURE LOOP
        # ----------------------------------------------------
        # Do not exit merely because the first login attempt produced no jKey.
        # Each cycle re-detects the page, re-enters the fields when the login
        # form is present, generates a fresh TOTP, submits, and waits again.
        # If already logged in, it refreshes the Shoonya tab to provoke fresh
        # web-session requests for the extension to observe.

        print("Proceeding directly to login. No extension settings page or extension-control button will be opened.")

        login_cycle = 0
        totp_generated_at = 0.0

        def make_fresh_totp():
            nonlocal totp_generated_at
            code = pyotp.TOTP(totp_secret).now()
            totp_generated_at = time.time()
            print("Fresh TOTP generated for this login attempt; value hidden.")
            return code

        def totp_near_expiry():
            age = time.time() - totp_generated_at
            seconds_left = 30 - (time.time() % 30)
            return age >= 22 or seconds_left <= 8

        def submit_login_form(found_positions):
            """Fill detected controls; restart individual fields after interruptions."""
            global last_expected_mouse

            while not activate_chrome():
                _wait_until_resumed("Waiting for Chrome before credential entry")

            # The script itself just opened this Shoonya tab and pasted its URL.
            # Do not wait for an extension URL scan before or between fields.
            # Chrome's active tab is retained if the window is minimized/restored.
            _chrome_foreground_handle()
            p0 = pyautogui.position()
            last_expected_mouse = (p0.x, p0.y)

            print("\nFAST LOGIN SEQUENCE: USER ID -> 0.5 sec -> PASSWORD -> 0.5 sec -> TOTP -> LOGIN")
            clear_and_paste("USER ID", found_positions[0][0], found_positions[0][1], user_id, tab_already_verified=True)
            time.sleep(0.5)
            clear_and_paste("PASSWORD", found_positions[1][0], found_positions[1][1], password, tab_already_verified=True)
            time.sleep(0.5)

            # TOTP is passed as a callable: if the field-entry routine pauses
            # and retries, it generates a new code after the pause.
            clear_and_paste(
                "TOTP",
                found_positions[2][0],
                found_positions[2][1],
                make_fresh_totp,
                tab_already_verified=True
            )
            # Allow the pasted TOTP to settle in the browser before clicking Login.
            time.sleep(0.5)

            while True:
                if _check_user_interrupt():
                    # If a pause consumed most of the TOTP validity window,
                    # replace it before clicking Login.
                    if totp_near_expiry():
                        print("TOTP aged during interruption; entering a fresh code...")
                        clear_and_paste(
                            "TOTP",
                            found_positions[2][0],
                            found_positions[2][1],
                            make_fresh_totp,
                            tab_already_verified=True
                        )
                        time.sleep(0.5)
                    continue

                if totp_near_expiry():
                    print("TOTP is close to expiry; refreshing it before Login...")
                    clear_and_paste(
                        "TOTP",
                        found_positions[2][0],
                        found_positions[2][1],
                        make_fresh_totp,
                        tab_already_verified=True
                    )
                    time.sleep(0.5)
                    continue

                try:
                    try:
                        _chrome_foreground_handle()
                    except Exception:
                        _wait_until_resumed("Chrome focus lost before Login click")
                        _chrome_foreground_handle()
                    if _check_user_interrupt():
                        continue
                    print("Clicking Login button immediately...")
                    pyautogui.click(found_positions[3][0], found_positions[3][1])
                    print("Login submitted.")
                    return
                except pyautogui.FailSafeException:
                    _wait_until_resumed("Login-button click interrupted")
                except Exception as click_error:
                    print("Temporary Login-button issue:", type(click_error).__name__)
                    _wait_until_resumed("Waiting to retry Login-button click")

        while True:
            # A captured key is not accepted until the read-only Order Book
            # check confirms it. UNKNOWN is retried; INVALID starts a new login.
            if jkey_event.is_set() and captured_jkey:
                print("\nA jKey candidate has arrived. Validating it with Order Book...")
                status, detail = validate_web_jkey(user_id, password, captured_jkey)
                save_jkey_validation_to_excel(captured_jkey, status, detail)

                if status == "VALID":
                    print("\nSUCCESS: jKey is VALID and saved in Excel LOGIN!B20.")
                    delete_screenshot_folder_after_success()
                    break

                if status == "INVALID":
                    print("Captured jKey was rejected/expired. Starting a fresh login/capture cycle...")
                    captured_jkey = None
                    jkey_event.clear()
                    continue

                print("jKey validation is UNKNOWN. Keeping the candidate and retrying validation in 5 seconds...")
                time.sleep(2)
                continue

            login_cycle += 1
            print(f"\n================ LOGIN / CAPTURE RETRY CYCLE {login_cycle} ================")

            try:
                positions = None
                try:
                    positions = capture_and_detect()
                except pyautogui.FailSafeException:
                    _wait_until_resumed("Login screenshot/detection interrupted")
                except Exception as detect_error:
                    print("Temporary login detection error:", type(detect_error).__name__)
                    _wait_until_resumed("Waiting before retrying login-form detection")

                if jkey_event.is_set() and captured_jkey:
                    continue

                if positions:
                    print("Login form detected. Re-entering credentials with a fresh TOTP.")
                    submit_login_form(positions)
                else:
                    print("Login form is not visible. Treating page as an existing/logged-in session and refreshing it for a fresh jKey request.")
                    refresh_logged_in_shoonya()

            except pyautogui.FailSafeException:
                _wait_until_resumed("PyAutoGUI interruption; retrying current login cycle")
            except Exception as cycle_error:
                print("Temporary login/capture cycle error:", type(cycle_error).__name__, str(cycle_error)[:250])
                _wait_until_resumed("Temporary error; waiting and retrying the login cycle")

            print(f"Waiting up to {JKEY_TIMEOUT} seconds for jKey before the next retry...")
            if jkey_event.wait(JKEY_TIMEOUT) and captured_jkey:
                continue

            print("No jKey yet. Keeping Excel B20 unchanged; preparing another automatic retry.")
            print(f"Shoonya requests observed by extension so far: {candidate_request_count}")
            if jkey_diagnostics:
                print("Latest safe request diagnostics:")
                for item in jkey_diagnostics[-3:]:
                    print(
                        f"  {item.get('method')} {item.get('host')}{item.get('path')} | "
                        f"fields={item.get('form_keys')} | raw_body={item.get('has_raw_body')}"
                    )
            elif not extension_heartbeat_event.is_set():
                print("The extension has not confirmed it is running yet; the script will keep waiting and retrying.")
            else:
                print("Extension is alive but has not observed a Shoonya request yet. Refreshing and retrying.")

            # If the user is logged in, a page refresh should trigger the web
            # client's normal session/API requests. If still at login, the next
            # cycle re-detects all fields and enters a newly generated TOTP.
            try:
                refresh_logged_in_shoonya()
            except Exception as refresh_error:
                print("Temporary refresh issue:", type(refresh_error).__name__)
                _wait_until_resumed("Waiting before retrying Shoonya refresh")

            time.sleep(1)

        print("\nGoogle Chrome will remain open. Excel contains the verified jKey.")

    except KeyboardInterrupt:
        print("\nKeyboard interrupt received. The script is paused safely; close the console window to terminate it.")
        while True:
            time.sleep(1)

    except Exception as e:

        print("\nUNEXPECTED MAIN ERROR:")
        print(type(e).__name__, str(e)[:500])
        print("Chrome has not been closed. Waiting and retrying the main workflow...")
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
            except Exception:
                pass
        time.sleep(3)
        main()
        return


    finally:

        if server is not None:

            server.shutdown()

            server.server_close()


        print(
            "\nScript finished. Chrome has not been closed."
        )


if __name__ == "__main__":

    main()
