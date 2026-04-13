#!/usr/bin/env python3
"""
Math Equation Scanner & Solver — Web UI
Takes a screenshot, shows it in browser for region selection, OCRs, and solves.
"""

import http.server
import json
import subprocess
import tempfile
import os
import re
import sys
import webbrowser
import base64
import threading
import signal

from PIL import Image
import io

import sympy
from sympy.parsing.sympy_parser import (
    parse_expr,
    standard_transformations,
    implicit_multiplication_application,
    convert_xor,
)
from sympy import (
    symbols, solve, simplify, Eq, oo,
    sin, cos, tan, log, ln, sqrt, pi, E,
    diff, integrate, Abs,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OCR_HELPER = os.path.join(SCRIPT_DIR, "ocr_helper")
PORT = 8347

TRANSFORMATIONS = standard_transformations + (
    implicit_multiplication_application,
    convert_xor,
)

LOCAL_DICT = {
    "pi": pi, "e": E, "sin": sin, "cos": cos, "tan": tan,
    "log": log, "ln": ln, "sqrt": sqrt, "abs": Abs,
    "inf": oo, "oo": oo,
}


# ── OCR ─────────────────────────────────────────────────────────────────────

def ocr_image(image_path):
    try:
        result = subprocess.run(
            [OCR_HELPER, image_path],
            capture_output=True, text=True, timeout=15,
        )
        return result.stdout.strip()
    except Exception as exc:
        return f"[OCR error: {exc}]"


# ── Math parsing & solving ──────────────────────────────────────────────────

def clean_text(text):
    text = text.replace("\u00d7", "*").replace("\u00f7", "/").replace("\u2212", "-")
    text = text.replace("\u00b7", "*").replace("\u2014", "-").replace("\u2013", "-")
    text = text.replace("\u00b2", "**2").replace("\u00b3", "**3")
    text = text.replace("\u221a", "sqrt")
    text = text.replace("^", "**")
    text = text.replace("{", "(").replace("}", ")")
    text = re.sub(r"(?<=\d) +(?=\d{1,2}(?!\d))", "", text)
    return text


def try_parse(expr_str):
    expr_str = clean_text(expr_str.strip())
    if not expr_str:
        return None
    if expr_str.endswith("="):
        expr_str = expr_str[:-1].strip()
    try:
        return parse_expr(expr_str, transformations=TRANSFORMATIONS, local_dict=LOCAL_DICT)
    except Exception:
        return None


def solve_line(line):
    line = line.strip()
    if not line or len(line) < 2:
        return None
    if not re.search(r"[\d+\-*/=^xyzXYZ]", line):
        return None

    if "=" in line and line.count("=") == 1:
        lhs_str, rhs_str = line.split("=", 1)
        lhs = try_parse(lhs_str)
        rhs = try_parse(rhs_str)
        if lhs is not None and rhs is not None:
            free = (lhs - rhs).free_symbols
            if free:
                try:
                    sol = solve(Eq(lhs, rhs), list(free))
                    return {"original": line, "type": "equation", "result": format_solution(free, sol)}
                except Exception:
                    pass
            else:
                try:
                    is_true = simplify(lhs - rhs) == 0
                    return {"original": line, "type": "verify", "result": "True" if is_true else "False"}
                except Exception:
                    pass

    deriv_match = re.match(r"d/d([a-z])\s+(.+)", line, re.IGNORECASE)
    if deriv_match:
        var_name, expr_str = deriv_match.groups()
        var = symbols(var_name)
        expr = try_parse(expr_str)
        if expr is not None:
            try:
                return {"original": line, "type": "derivative", "result": str(diff(expr, var))}
            except Exception:
                pass

    int_match = re.match(r"(?:\u222b|integral)\s*(.+?)\s*d([a-z])", line, re.IGNORECASE)
    if int_match:
        expr_str, var_name = int_match.groups()
        var = symbols(var_name)
        expr = try_parse(expr_str)
        if expr is not None:
            try:
                return {"original": line, "type": "integral", "result": f"{integrate(expr, var)} + C"}
            except Exception:
                pass

    expr = try_parse(line)
    if expr is not None:
        free = expr.free_symbols
        if free:
            try:
                return {"original": line, "type": "simplify", "result": str(simplify(expr))}
            except Exception:
                return {"original": line, "type": "expression", "result": str(expr)}
        else:
            try:
                exact = simplify(expr)
                val = expr.evalf()
                if val == int(val):
                    return {"original": line, "type": "evaluate", "result": str(int(val))}
                elif exact != val and str(exact) != str(val):
                    return {"original": line, "type": "evaluate", "result": f"{exact} \u2248 {val}"}
                else:
                    return {"original": line, "type": "evaluate", "result": str(val)}
            except Exception:
                pass
    return None


def format_solution(free_vars, sol):
    if isinstance(sol, dict):
        return ",  ".join(f"{k} = {v}" for k, v in sol.items())
    elif isinstance(sol, list):
        var_name = list(free_vars)[0] if len(free_vars) == 1 else "x"
        if len(sol) == 1:
            return f"{var_name} = {sol[0]}"
        return f"{var_name} = {', '.join(str(s) for s in sol)}"
    return str(sol)


def process_text(text):
    results = []
    for line in text.split("\n"):
        r = solve_line(line)
        if r:
            results.append(r)
    return results


# ── Screenshot ──────────────────────────────────────────────────────────────

def take_full_screenshot():
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.close()
    result = subprocess.run(["screencapture", "-x", tmp.name], capture_output=True)
    if result.returncode != 0 or not os.path.exists(tmp.name) or os.path.getsize(tmp.name) == 0:
        return None
    return tmp.name


# ── Web server ──────────────────────────────────────────────────────────────

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Math Scanner</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', sans-serif;
    background: #1e1e2e; color: #cdd6f4;
  }

  .header {
    padding: 20px 32px 0;
    display: flex; align-items: baseline; gap: 12px;
  }
  .header h1 { font-size: 28px; font-weight: 700; }
  .header span { font-size: 14px; color: #6c7086; }

  .controls {
    padding: 16px 32px;
    display: flex; align-items: center; gap: 16px;
  }

  .scan-btn {
    background: #89b4fa; color: #1e1e2e;
    border: none; border-radius: 10px;
    padding: 12px 28px; font-size: 15px; font-weight: 600;
    cursor: pointer; transition: all 0.15s;
  }
  .scan-btn:hover { background: #b4d0fb; }
  .scan-btn:disabled { opacity: 0.5; cursor: default; }

  .status { font-size: 13px; color: #6c7086; }
  .status.success { color: #a6e3a1; }
  .status.error { color: #f38ba8; }
  .status.working { color: #f9e2af; }

  /* Screenshot overlay */
  .overlay {
    display: none; position: fixed; inset: 0; z-index: 1000;
    background: rgba(0,0,0,0.85); cursor: crosshair;
  }
  .overlay.active { display: flex; flex-direction: column; }
  .overlay-hint {
    text-align: center; padding: 16px;
    font-size: 18px; color: white; background: rgba(0,0,0,0.7);
  }
  .overlay canvas { flex: 1; cursor: crosshair; }

  /* Text area */
  .section-label {
    padding: 8px 32px 4px;
    font-size: 11px; color: #6c7086;
    text-transform: uppercase; letter-spacing: 1px;
  }
  textarea {
    display: block; width: calc(100% - 64px); margin: 0 32px;
    background: #313244; color: #cdd6f4; border: none; border-radius: 8px;
    padding: 12px 14px; font-family: 'SF Mono', monospace; font-size: 14px;
    resize: vertical; min-height: 100px; outline: none;
  }
  textarea:focus { box-shadow: 0 0 0 2px #89b4fa; }

  .hint-row {
    display: flex; justify-content: space-between; align-items: center;
    padding: 6px 32px;
  }
  .hint-row span { font-size: 12px; color: #6c7086; }
  .solve-btn {
    background: #313244; color: #89b4fa; border: 1px solid #45475a;
    border-radius: 6px; padding: 6px 16px; font-size: 13px;
    cursor: pointer; transition: all 0.15s;
  }
  .solve-btn:hover { background: #45475a; }

  /* Results */
  .results { padding: 0 32px 32px; }

  .result-card {
    background: #313244; border-radius: 10px;
    padding: 14px 18px; margin-bottom: 10px;
  }
  .result-type {
    font-size: 11px; text-transform: uppercase;
    letter-spacing: 1px; margin-bottom: 4px;
  }
  .result-original {
    font-family: 'SF Mono', monospace; font-size: 15px;
    margin-bottom: 6px;
  }
  .result-answer {
    display: flex; align-items: center; gap: 8px;
  }
  .result-answer .arrow { color: #6c7086; }
  .result-answer .value {
    font-family: 'SF Mono', monospace; font-size: 15px; font-weight: 600;
  }
  .copy-btn {
    margin-left: auto;
    background: #45475a; color: #cdd6f4; border: none; border-radius: 4px;
    padding: 3px 10px; font-size: 12px; cursor: pointer;
  }
  .copy-btn:hover { background: #585b70; }

  .type-equation .result-type, .type-verify .result-type { color: #89b4fa; }
  .type-equation .value, .type-verify .value { color: #89b4fa; }
  .type-evaluate .result-type { color: #a6e3a1; }
  .type-evaluate .value { color: #a6e3a1; }
  .type-simplify .result-type, .type-derivative .result-type, .type-integral .result-type { color: #f9e2af; }
  .type-simplify .value, .type-derivative .value, .type-integral .value { color: #f9e2af; }
</style>
</head>
<body>

<div class="header">
  <h1>Math Scanner</h1>
  <span>Scan your screen to solve equations</span>
</div>

<div class="controls">
  <button class="scan-btn" id="scanBtn" onclick="startScan()">Scan Screen Region</button>
  <div class="status" id="status">Ready &mdash; click to scan</div>
</div>

<div class="section-label">Detected Text</div>
<textarea id="ocrText" placeholder="Detected text will appear here, or type math directly..."></textarea>

<div class="hint-row">
  <span>You can edit the text above and click Solve</span>
  <button class="solve-btn" onclick="solveText()">Solve</button>
</div>

<div class="section-label">Solutions</div>
<div class="results" id="results"></div>

<!-- Fullscreen overlay for region selection -->
<div class="overlay" id="overlay">
  <div class="overlay-hint">Drag to select the math area &bull; Press Esc to cancel</div>
  <canvas id="overlayCanvas"></canvas>
</div>

<script>
const status = document.getElementById('status');
const scanBtn = document.getElementById('scanBtn');
const ocrText = document.getElementById('ocrText');
const resultsDiv = document.getElementById('results');
const overlay = document.getElementById('overlay');
const overlayCanvas = document.getElementById('overlayCanvas');
const ctx = overlayCanvas.getContext('2d');

let screenshotImg = null;
let dragging = false;
let startX = 0, startY = 0, endX = 0, endY = 0;
let imgScale = 1;

function setStatus(msg, type) {
  status.textContent = msg;
  status.className = 'status ' + (type || '');
}

async function startScan() {
  scanBtn.disabled = true;
  scanBtn.textContent = 'Capturing...';
  setStatus('Taking screenshot...', 'working');

  try {
    const resp = await fetch('/api/screenshot', {method: 'POST'});
    const data = await resp.json();
    if (!data.ok) {
      setStatus('Screenshot failed: ' + data.error, 'error');
      scanBtn.disabled = false;
      scanBtn.textContent = 'Scan Screen Region';
      return;
    }

    // Load screenshot into overlay
    screenshotImg = new Image();
    screenshotImg.onload = () => showOverlay();
    screenshotImg.src = 'data:image/png;base64,' + data.image;
  } catch(e) {
    setStatus('Error: ' + e.message, 'error');
    scanBtn.disabled = false;
    scanBtn.textContent = 'Scan Screen Region';
  }
}

function showOverlay() {
  overlay.classList.add('active');
  overlayCanvas.width = window.innerWidth;
  overlayCanvas.height = window.innerHeight - 50; // minus hint bar

  imgScale = Math.min(
    overlayCanvas.width / screenshotImg.width,
    overlayCanvas.height / screenshotImg.height
  );
  const dw = screenshotImg.width * imgScale;
  const dh = screenshotImg.height * imgScale;
  const ox = (overlayCanvas.width - dw) / 2;
  const oy = (overlayCanvas.height - dh) / 2;

  ctx.drawImage(screenshotImg, ox, oy, dw, dh);
  // Store offset for crop calculation
  overlayCanvas.dataset.ox = ox;
  overlayCanvas.dataset.oy = oy;
  overlayCanvas.dataset.dw = dw;
  overlayCanvas.dataset.dh = dh;
}

function redrawOverlay() {
  const ox = parseFloat(overlayCanvas.dataset.ox);
  const oy = parseFloat(overlayCanvas.dataset.oy);
  const dw = parseFloat(overlayCanvas.dataset.dw);
  const dh = parseFloat(overlayCanvas.dataset.dh);

  ctx.clearRect(0, 0, overlayCanvas.width, overlayCanvas.height);
  ctx.drawImage(screenshotImg, ox, oy, dw, dh);

  // Darken everything outside the selection
  if (dragging) {
    const sx = Math.min(startX, endX), sy = Math.min(startY, endY);
    const sw = Math.abs(endX - startX), sh = Math.abs(endY - startY);

    ctx.fillStyle = 'rgba(0,0,0,0.5)';
    ctx.fillRect(0, 0, overlayCanvas.width, overlayCanvas.height);
    ctx.clearRect(sx, sy, sw, sh);
    ctx.drawImage(screenshotImg, ox, oy, dw, dh);
    ctx.fillStyle = 'rgba(0,0,0,0.5)';

    // Top
    ctx.fillRect(0, 0, overlayCanvas.width, sy);
    // Bottom
    ctx.fillRect(0, sy + sh, overlayCanvas.width, overlayCanvas.height - sy - sh);
    // Left
    ctx.fillRect(0, sy, sx, sh);
    // Right
    ctx.fillRect(sx + sw, sy, overlayCanvas.width - sx - sw, sh);

    // Green border
    ctx.strokeStyle = '#00ff00';
    ctx.lineWidth = 2;
    ctx.strokeRect(sx, sy, sw, sh);
  }
}

overlayCanvas.addEventListener('mousedown', (e) => {
  dragging = true;
  startX = e.offsetX; startY = e.offsetY;
  endX = startX; endY = startY;
});

overlayCanvas.addEventListener('mousemove', (e) => {
  if (!dragging) return;
  endX = e.offsetX; endY = e.offsetY;
  redrawOverlay();
});

overlayCanvas.addEventListener('mouseup', (e) => {
  if (!dragging) return;
  dragging = false;
  endX = e.offsetX; endY = e.offsetY;

  const ox = parseFloat(overlayCanvas.dataset.ox);
  const oy = parseFloat(overlayCanvas.dataset.oy);

  // Convert canvas coords to image coords
  const x1 = Math.round((Math.min(startX, endX) - ox) / imgScale);
  const y1 = Math.round((Math.min(startY, endY) - oy) / imgScale);
  const x2 = Math.round((Math.max(startX, endX) - ox) / imgScale);
  const y2 = Math.round((Math.max(startY, endY) - oy) / imgScale);

  overlay.classList.remove('active');

  if (x2 - x1 < 10 || y2 - y1 < 10) {
    setStatus('Selection too small', 'error');
    scanBtn.disabled = false;
    scanBtn.textContent = 'Scan Screen Region';
    return;
  }

  cropAndSolve(x1, y1, x2, y2);
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && overlay.classList.contains('active')) {
    overlay.classList.remove('active');
    setStatus('Cancelled', 'error');
    scanBtn.disabled = false;
    scanBtn.textContent = 'Scan Screen Region';
  }
});

async function cropAndSolve(x1, y1, x2, y2) {
  setStatus('Running OCR...', 'working');
  try {
    const resp = await fetch('/api/crop_and_solve', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({x1, y1, x2, y2}),
    });
    const data = await resp.json();
    ocrText.value = data.text || '';

    if (data.results && data.results.length > 0) {
      showResults(data.results);
      setStatus('Solved ' + data.results.length + ' equation(s)', 'success');
    } else {
      resultsDiv.innerHTML = '<div style="color:#6c7086;padding:8px;">No solvable math expressions found. Try editing the text above.</div>';
      setStatus('No math found', 'error');
    }
  } catch(e) {
    setStatus('Error: ' + e.message, 'error');
  }
  scanBtn.disabled = false;
  scanBtn.textContent = 'Scan Screen Region';
}

async function solveText() {
  const text = ocrText.value.trim();
  if (!text) { setStatus('No text to solve', 'error'); return; }
  setStatus('Solving...', 'working');
  try {
    const resp = await fetch('/api/solve', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text}),
    });
    const data = await resp.json();
    if (data.results && data.results.length > 0) {
      showResults(data.results);
      setStatus('Solved ' + data.results.length + ' equation(s)', 'success');
    } else {
      resultsDiv.innerHTML = '<div style="color:#6c7086;padding:8px;">No solvable math expressions found.</div>';
      setStatus('No math found', 'error');
    }
  } catch(e) {
    setStatus('Error: ' + e.message, 'error');
  }
}

function showResults(results) {
  resultsDiv.innerHTML = '';
  results.forEach(r => {
    const card = document.createElement('div');
    card.className = 'result-card type-' + r.type;
    card.innerHTML = `
      <div class="result-type">${r.type.toUpperCase()}</div>
      <div class="result-original">${escHtml(r.original)}</div>
      <div class="result-answer">
        <span class="arrow">&rarr;</span>
        <span class="value">${escHtml(r.result)}</span>
        <button class="copy-btn" onclick="copyResult('${escAttr(r.result)}')">Copy</button>
      </div>
    `;
    resultsDiv.appendChild(card);
  });
}

function escHtml(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function escAttr(s) { return s.replace(/\\/g,'\\\\').replace(/'/g,"\\'"); }

function copyResult(text) {
  navigator.clipboard.writeText(text);
  setStatus('Copied!', 'success');
}
</script>
</body>
</html>
"""


class MathHandler(http.server.BaseHTTPRequestHandler):
    screenshot_path = None

    def log_message(self, format, *args):
        pass  # Suppress request logs

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""

        if self.path == "/api/screenshot":
            self._handle_screenshot()
        elif self.path == "/api/crop_and_solve":
            data = json.loads(body)
            self._handle_crop_and_solve(data)
        elif self.path == "/api/solve":
            data = json.loads(body)
            self._handle_solve(data)
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_screenshot(self):
        path = take_full_screenshot()
        if not path:
            self._json_response({"ok": False, "error": "screencapture failed"})
            return

        MathHandler.screenshot_path = path
        with open(path, "rb") as f:
            img_data = base64.b64encode(f.read()).decode()

        self._json_response({"ok": True, "image": img_data})

    def _handle_crop_and_solve(self, data):
        if not MathHandler.screenshot_path or not os.path.exists(MathHandler.screenshot_path):
            self._json_response({"text": "", "results": []})
            return

        x1, y1 = data["x1"], data["y1"]
        x2, y2 = data["x2"], data["y2"]

        # Crop region
        img = Image.open(MathHandler.screenshot_path)
        x1 = max(0, min(x1, img.width))
        y1 = max(0, min(y1, img.height))
        x2 = max(0, min(x2, img.width))
        y2 = max(0, min(y2, img.height))

        cropped = img.crop((x1, y1, x2, y2))
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.close()
        cropped.save(tmp.name)

        # OCR
        text = ocr_image(tmp.name)
        os.unlink(tmp.name)

        # Cleanup screenshot
        try:
            os.unlink(MathHandler.screenshot_path)
            MathHandler.screenshot_path = None
        except OSError:
            pass

        results = process_text(text)
        self._json_response({"text": text, "results": results})

    def _handle_solve(self, data):
        text = data.get("text", "")
        results = process_text(text)
        self._json_response({"text": text, "results": results})

    def _json_response(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())


def main():
    if not os.path.isfile(OCR_HELPER):
        print(f"Error: OCR helper not found at {OCR_HELPER}")
        print("Compile: swiftc -o ocr_helper ocr_helper.swift -framework Vision -framework AppKit")
        sys.exit(1)

    server = http.server.HTTPServer(("127.0.0.1", PORT), MathHandler)
    print(f"Math Scanner running at http://127.0.0.1:{PORT}")

    # Open browser after a short delay
    def open_browser():
        import time
        time.sleep(0.5)
        webbrowser.open(f"http://127.0.0.1:{PORT}")
    threading.Thread(target=open_browser, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
