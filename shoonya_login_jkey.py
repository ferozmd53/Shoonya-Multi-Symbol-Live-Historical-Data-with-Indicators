
import time
import json
import threading
import os
import shutil
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

SHOONYA_URL = "https://trade.shoonya.com/#/"
SHOONYA_HOST = "trade.shoonya.com"

RECEIVER_HOST = "127.0.0.1"
RECEIVER_PORT = 8765

# Maximum wait for a fresh jKey request after refreshing Shoonya.
JKEY_TIMEOUT = 5

# Chrome may take up to 30 seconds to poll Python, then up to 45 seconds
# to finish reloading the Shoonya tab.
RELOAD_TIMEOUT = 0.5

LOGIN_WAIT = 0.2

DETECTION_ATTEMPTS = 2
DETECTION_INTERVAL = 1

jkey_event = threading.Event()
captured_jkey = None

# Safe metadata only; never store or print credentials or the jKey.
jkey_diagnostics = []
candidate_request_count = 0

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


# ============================================================
# CHROME EXTENSION FILES
# ============================================================

MANIFEST = r"""{
  "manifest_version": 3,
  "name": "Shoonya JKEY Capture + Tab Scanner",
  "version": "1.6.0",
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
    try {
        const url = new URL(urlText);
        const host = url.hostname.toLowerCase();
        const isShoonyaHost =
            host === SHOONYA_DOMAIN || host.endsWith('.' + SHOONYA_DOMAIN);
        return isShoonyaHost &&
            url.pathname.toLowerCase().includes('norenwclientweb');
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

async function checkForPythonScan() {
    if (scanBusy) return;
    scanBusy = true;

    try {
        const response = await fetch(LOCAL + '/scan', {cache: 'no-store'});
        if (!response.ok) return;

        const request = await response.json();
        if (!request.scan && !request.reload) return;

        const allTabs = await chrome.tabs.query({currentWindow: true});
        const reportTabs = allTabs.map((tab, index) => ({
            number: index + 1,
            id: tab.id,
            windowId: tab.windowId,
            url: tab.url || ''
        }));

        let found = null;
        for (const tab of reportTabs) {
            try {
                if (new URL(tab.url).hostname.toLowerCase() === 'trade.shoonya.com') {
                    found = tab;
                    break;
                }
            } catch (_) {}
        }

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

    EXT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    files = {
        "manifest.json": MANIFEST,
        "service_worker.js": SERVICE_WORKER_JS
    }

    for filename, content in files.items():

        path = EXT_DIR / filename

        if (
            not path.exists()
            or path.read_text(encoding="utf-8") != content
        ):

            path.write_text(
                content,
                encoding="utf-8"
            )


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
    time.sleep(0.25)
    pyautogui.hotkey("ctrl", "a")
    pyperclip.copy(url)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.6)

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

def scan_chrome_tabs():

    global scan_requested, tab_snapshot

    if not find_chrome_window()[0]:

        raise RuntimeError(
            "Google Chrome window not found."
        )

    if not activate_chrome():

        raise RuntimeError(
            "Could not activate Google Chrome."
        )


    tab_snapshot = None

    tab_snapshot_event.clear()


    with scan_lock:
        scan_requested = True


    print(
        "\nChecking ALL Chrome tabs using the generated extension..."
    )

    print(
        "Method: chrome.tabs API reads each tab URL directly."
    )

    print(
        "No Ctrl+L, clipboard URL reading, or tab-by-tab switching."
    )


    if not tab_snapshot_event.wait(45):

        with scan_lock:
            scan_requested = False

        raise RuntimeError(
            "No tab report received from the extension. "
            "Open chrome://extensions, make sure the extension is "
            "enabled, and click Reload."
        )


    report = tab_snapshot or {}

    tabs = report.get("tabs", [])

    shoonya = report.get("shoonya")


    if not tabs:

        raise RuntimeError(
            "The extension returned an empty Chrome tab list."
        )


    print(
        f"\nTotal Chrome tabs detected: {len(tabs)}"
    )


    for tab in tabs:

        number = tab.get("number", "?")

        url = tab.get("url", "")

        if is_shoonya_url(url):

            print(
                f"Chrome tab {number}: Shoonya found"
            )

        else:

            host = (
                urlparse(url).hostname or "URL unavailable"
                if url
                else "URL unavailable"
            )

            print(
                f"Chrome tab {number}: {host}"
            )


    if shoonya:

        print(
            f"Shoonya is present in Chrome tab: "
            f"{shoonya.get('number')}"
        )

        print(
            "Shoonya tab activated by extension."
        )

        return len(tabs), int(
            shoonya.get("number")
        )


    print(
        "Shoonya was not found in any tab in the current Chrome window."
    )

    return len(tabs), None


# ============================================================
# OPEN SHOONYA IN A NEW TAB
# ============================================================

def open_shoonya_new_tab():

    print("\nOpening a NEW Chrome tab...")

    if not activate_chrome():
        raise RuntimeError("Chrome is not in the foreground; refusing to send keyboard shortcuts.")

    pyautogui.hotkey("ctrl", "t")
    time.sleep(0.8)

    paste_url_into_chrome(SHOONYA_URL)
    print("Shoonya URL pasted. Waiting for the page to load...")

    # Take a screenshot immediately after submitting the URL. This is kept
    # separate from the later login-form detection screenshot so the user
    # has a record of the page state after navigation.
    time.sleep(1)
    navigation_screenshot = BASE_DIR / "shoonya_after_url_paste.png"
    try:
        pyautogui.screenshot().save(str(navigation_screenshot))
        print("Screenshot saved after URL paste:", navigation_screenshot)
    except Exception as exc:
        print("Warning: could not save post-navigation screenshot:", exc)

    # Chrome's address bar is not consistently readable through UI Automation
    # on newer Chrome versions. Verify the actual tab URL through our extension
    # instead; it reads chrome.tabs URLs directly and does not use the address bar.
    for attempt in range(1, 4):
        tab_count, shoonya_tab = scan_chrome_tabs()
        if shoonya_tab is not None:
            print(
                f"Shoonya navigation verified by extension in tab "
                f"{shoonya_tab} of {tab_count}."
            )
            return

        if attempt < 3:
            print(
                f"Shoonya page is not visible in the extension tab report yet; "
                f"retrying ({attempt}/3)..."
            )
            time.sleep(2)

    raise RuntimeError(
        "The URL was pasted and a screenshot was saved, but the Chrome "
        "extension did not detect trade.shoonya.com in the tab list. "
        "Check shoonya_after_url_paste.png and try again."
    )


# ============================================================
# OPEN OR REUSE CHROME
# ============================================================

def open_google_chrome():

    chrome_path = find_chrome()
    print("\nGoogle Chrome:", chrome_path)

    hwnd, _ = find_chrome_window()

    if hwnd:
        # IMPORTANT: if Chrome exists but cannot be activated, do NOT launch
        # another Chrome window/profile. Stop safely and ask the user to focus it.
        if not activate_chrome():
            raise RuntimeError(
                "A Google Chrome window already exists, but Windows would not "
                "activate it. No separate browser was opened. Click the existing "
                "Chrome window once and run this script again."
            )

        print("\nChecking ALL tabs in the current Google Chrome window...")
        tab_count, shoonya_tab = scan_chrome_tabs()

        if shoonya_tab is not None:
            print(f"Using existing Shoonya tab {shoonya_tab} of {tab_count}.")
            return

        print("Shoonya is not open in any existing Chrome tab.")
        print("Existing tabs will remain open and untouched.")
        open_shoonya_new_tab()
        return

    # Launch Chrome ONLY when no visible Chrome browser window exists.
    print("No existing Chrome window found. Opening Google Chrome now...")
    subprocess.Popen([chrome_path, "--new-window", "about:blank"])

    deadline = time.time() + 10
    while time.time() < deadline:
        time.sleep(0.7)
        hwnd, _ = find_chrome_window()
        if hwnd and activate_chrome():
            open_shoonya_new_tab()
            return

    raise RuntimeError(
        "No Chrome window appeared after launch. Check whether Chrome is "
        "showing a startup/profile dialog, then run the script again."
    )


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
        global tab_snapshot
        global reload_result
        global candidate_request_count
        global jkey_diagnostics

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
                    "Shoonya API request detected (no jKey found): "
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

            if "norenwclientweb" not in request_path.lower():
                self.send_response(400)
                self.end_headers()
                return

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

def read_credentials():

    if not EXCEL_FILE.exists():

        raise FileNotFoundError(
            f"Excel file not found: {EXCEL_FILE}"
        )


    wb = load_workbook(
        EXCEL_FILE,
        data_only=True,
        read_only=True
    )


    try:

        ws = wb[SHEET_NAME]

        user_id = ws["B3"].value

        password = ws["B4"].value

        totp_secret = ws["B5"].value


    finally:

        wb.close()


    if (
        user_id is None
        or password is None
        or totp_secret is None
    ):

        raise ValueError(
            "Check LOGIN!B3, B4 and B5."
        )


    user_id = str(user_id)

    password = str(password)

    totp_secret = (
        str(totp_secret)
        .strip()
        .replace(" ", "")
    )


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


# ============================================================
# SCREENSHOT AND LOGIN DETECTION
# ============================================================

def capture_and_detect():

    print("\nChecking Shoonya login form...")


    screenshot_path = (
        BASE_DIR / "shoonya_login_screen.png"
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


        time.sleep(1)


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


            cv2.imwrite(
                str(
                    BASE_DIR / "shoonya_detected_boxes.png"
                ),
                annotated
            )


            with open(
                BASE_DIR / "shoonya_login_positions.txt",
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

def clear_and_paste(label, x, y, value):

    print(f"\nFilling {label}...")


    pyautogui.click(x, y)

    time.sleep(0.3)


    pyautogui.hotkey("ctrl", "a")

    time.sleep(0.2)


    pyautogui.press("backspace")

    time.sleep(0.3)


    pyperclip.copy(value)

    pyautogui.hotkey("ctrl", "v")

    time.sleep(0.5)


    print(
        f"{label}: cleared and pasted."
    )


# ============================================================
# REQUEST REAL SHOONYA TAB RELOAD THROUGH EXTENSION
# ============================================================

def refresh_logged_in_shoonya():

    global reload_requested
    global scan_requested
    global reload_result

    print("\nShoonya login form was not detected.")
    print("v4: NOT logged in yet maybe; refreshing only to catch requests.")

    if jkey_event.is_set():
        print("A jKey request was already captured.")
        return

    # The extension has already found and activated the Shoonya tab
    # during scan_chrome_tabs(). Refresh it directly from Python so
    # this step does not depend on the extension's alarm/polling cycle.
    print("\nRefreshing the ACTIVE Shoonya tab directly...")
    print("Using Ctrl+R in Google Chrome; no other tabs are selected.")

    if not activate_chrome():
        print("Could not bring Google Chrome to the foreground.")
        print("Cannot safely send Ctrl+R to the Shoonya tab.")
        return

    time.sleep(0.5)
    pyautogui.hotkey("ctrl", "r")
    print("Refresh command sent to the active Shoonya tab.")

    # Give Chrome time to begin navigation and the extension time
    # to observe the NorenWClientWeb requests generated by the page.
    time.sleep(1)

    print("Shoonya refresh requested. Waiting for fresh API requests...")


# ============================================================
# READ EXISTING ACTIVE JKEY FROM EXCEL
# ============================================================

def read_existing_jkey_from_excel():

    """Read LOGIN!B20 without changing the workbook."""

    if not EXCEL_FILE.exists():
        return ""

    wb = load_workbook(
        EXCEL_FILE,
        data_only=True,
        read_only=True
    )

    try:
        if SHEET_NAME not in wb.sheetnames:
            return ""
        value = wb[SHEET_NAME]["B20"].value
        return str(value).strip() if value is not None else ""
    finally:
        wb.close()


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
                    time.sleep(1)
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

    wb = xw.Book(str(EXCEL_FILE))
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

    server = None


    print("=" * 60)

    print(
        "SHOONYA AUTO LOGIN + CHECK STORED JKEY FIRST (V19)"
    )

    print("=" * 60)


    try:

        # Reset state for a fresh run.
        captured_jkey = None

        jkey_event.clear()

        tab_snapshot_event.clear()

        reload_event.clear()


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
        write_extension_files()

        print("\nChrome extension files created/updated automatically:")
        print(EXT_DIR)
        print(
            "If the extension source was updated, reload it once at "
            "chrome://extensions before running this script."
        )

        # Start receiver only when a new browser capture is required.
        server = start_jkey_receiver()


        # Open or reuse Chrome.
        open_google_chrome()

        # The extension can capture jKey while Chrome tabs are being scanned.
        # If that has already happened, avoid unnecessary screenshots,
        # rectangle detection, and login-form retries.
        if jkey_event.is_set() and captured_jkey:
            print("\nValid jKey was already captured during browser startup.")
            print("Skipping login-form screenshots and detection.")
            positions = None
        else:
            # Detect login form only when a jKey has not already arrived.
            positions = capture_and_detect()


        if positions:

            # ------------------------------------------------
            # NORMAL LOGIN PATH
            # ------------------------------------------------

            clear_and_paste(
                "USER ID",
                positions[0][0],
                positions[0][1],
                user_id
            )


            clear_and_paste(
                "PASSWORD",
                positions[1][0],
                positions[1][1],
                password
            )


            print(
                "\nGenerating fresh TOTP..."
            )


            totp_code = pyotp.TOTP(
                totp_secret
            ).now()


            clear_and_paste(
                "TOTP",
                positions[2][0],
                positions[2][1],
                totp_code
            )


            print(
                "\nClicking Login button..."
            )


            pyautogui.click(
                positions[3][0],
                positions[3][1]
            )


            print(
                "Login submitted."
            )


        else:

            # ------------------------------------------------
            # EXISTING SESSION PATH
            # ------------------------------------------------

            refresh_logged_in_shoonya()


        print(
            "\nGoogle Chrome will remain open."
        )


        print(
            f"\nWaiting up to {JKEY_TIMEOUT} seconds for jKey..."
        )


        if jkey_event.wait(JKEY_TIMEOUT):

            if captured_jkey:

                status, detail = validate_web_jkey(
                    user_id,
                    password,
                    captured_jkey
                )

                save_jkey_validation_to_excel(
                    captured_jkey,
                    status,
                    detail
                )

            else:

                print(
                    "No jKey was captured."
                )


        else:

            print(
                "\nNo jKey request was captured."
            )


            print(
                "Excel B20 has not been cleared or overwritten."
            )


            print(
                "If Shoonya is logged in but no jKey arrives, "
                "check whether the extension is observing "
                "the relevant NorenWClientWeb request."
            )


            print(
                "The extension must be enabled in the "
                "Chrome profile currently running Shoonya."
            )

            print(
                f"Matching NorenWClientWeb requests observed: "
                f"{candidate_request_count}"
            )

            if jkey_diagnostics:
                print("Latest safe request diagnostics:")
                for item in jkey_diagnostics[-5:]:
                    print(
                        f"  {item.get('method')} "
                        f"{item.get('host')}{item.get('path')} | "
                        f"fields={item.get('form_keys')} | "
                        f"raw_body={item.get('has_raw_body')}"
                    )
            else:
                print(
                    "No NorenWClientWeb request matched the extension "
                    "URL filter during this run."
                )


    except Exception as e:

        print("\nERROR:")

        print(str(e))

        print(
            "Chrome has not been closed."
        )


    finally:

        if server is not None:

            server.shutdown()

            server.server_close()


        print(
            "\nScript finished. Chrome has not been closed."
        )


if __name__ == "__main__":

    main()
