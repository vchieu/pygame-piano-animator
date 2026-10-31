"""
OSMD Rendering Module - Render MusicXML using OpenSheetMusicDisplay via Playwright.

This module provides functions to render sheet music from MusicXML files using
the OSMD library (JavaScript) running in a headless browser, and return the
rendered image for display in Pygame.
"""
import io
import queue
import threading
import time
from pathlib import Path
from typing import Optional

import pygame
from playwright.sync_api import sync_playwright

# Cursor color for highlighting (matching the old staff.py)
CURSOR_COLOR = (220, 40, 60)


def _escape_xml_for_js(xml: str) -> str:
    """Escape XML content for embedding in JavaScript template literal."""
    return xml.replace('\\', '\\\\').replace('`', '\\`').replace('${', '\\${')


def _create_render_html(xml_content: str, width: int = 1400) -> str:
    """
    Create an HTML page that loads OSMD and renders the given MusicXML
    with generous vertical spacing between systems.
    """
    escaped_xml = _escape_xml_for_js(xml_content)
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <script src="https://cdn.jsdelivr.net/npm/opensheetmusicdisplay@1.8.9/build/opensheetmusicdisplay.min.js"></script>
  <style>
    body {{ margin: 0; padding: 0; background: #ffffff; }}
    #score {{ width: {width}px; }}
  </style>
</head>
<body>
  <div id="score"></div>
  <script>
    const xml = `{escaped_xml}`;
    const blob = new Blob([xml], {{ type: 'application/vnd.recordare.musicxml+xml' }});
    const url = URL.createObjectURL(blob);
    
    const OSMD = opensheetmusicdisplay.OpenSheetMusicDisplay || opensheetmusicdisplay.OSMD;
    window.osmd = new OSMD("score", {{
      followCursor: false,
      autoResize: false,
      backend: "svg",
      drawTitle: false,
      drawComposer: false,
      drawMetronome: false,
      drawPartNames: false,
    }});
    
    // Generous system spacing to eliminate note clipping and prevent systems from leaking into each other
    window.osmd.rules.MinimumDistanceBetweenSystems = 12.0;
    window.osmd.rules.MinSkyBottomDistBetweenSystems = 10.0;
    
    window.osmd.load(url).then(() => {{
      window.osmd.render();
      document.title = "READY";
    }}).catch(e => {{
      document.title = "ERROR:" + e.message;
    }});
  </script>
</body>
</html>"""


class OSMDRenderer:
    """
    Manages a single dedicated background thread that owns the Playwright browser.
    This guarantees greenlet/asyncio loop never switches threads, eliminating greenlet.error.
    """
    
    def __init__(self):
        self._task_queue = queue.Queue()
        self._thread = None
        self._stopped = False
        self._ready_event = threading.Event()
        self._start_worker()

    def _start_worker(self):
        if self._thread and self._thread.is_alive():
            return
        self._stopped = False
        self._ready_event.clear()
        self._thread = threading.Thread(target=self._worker_main, daemon=True, name="OSMD-Playwright-Worker")
        self._thread.start()
        self._ready_event.wait(timeout=12.0)

    def _worker_main(self):
        playwright = None
        browser = None
        page = None
        try:
            print("Starting OSMD renderer...")
            playwright = sync_playwright().start()
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1400, "height": 800})
            page = context.new_page()
            print("OSMD renderer ready")
            self._ready_event.set()

            while not self._stopped:
                try:
                    task = self._task_queue.get(timeout=0.2)
                except queue.Empty:
                    continue
                if task is None:
                    break

                xml_content, width, resp_queue = task
                try:
                    res = self._render_in_thread(page, xml_content, width)
                    resp_queue.put((True, res))
                except Exception as e:
                    resp_queue.put((False, e))
        except Exception as e:
            print(f"[OSMD] Worker initialization failed: {e}")
            self._ready_event.set()
        finally:
            try:
                if browser:
                    browser.close()
            except Exception:
                pass
            try:
                if playwright:
                    playwright.stop()
            except Exception:
                pass

    def _render_in_thread(self, page, xml_content: str, width: int = 1400):
        page.set_viewport_size({"width": width, "height": 800})
        html_content = _create_render_html(xml_content, width)
        page.goto("about:blank")
        page.set_content(html_content, wait_until="networkidle", timeout=15000)

        try:
            page.wait_for_function("document.title === 'READY'", timeout=15000)
        except Exception as e:
            try:
                title = page.title()
                if title.startswith("ERROR:"):
                    raise RuntimeError(title[6:])
            except Exception:
                pass
            raise RuntimeError(f"OSMD render failed: {e}")

        box = page.eval_on_selector('#score', 'el => ({ width: el.scrollWidth, height: el.scrollHeight })')
        score_h = int(box.get('height', 800)) + 60
        page.set_viewport_size({"width": width, "height": score_h})
        page.wait_for_timeout(100)

        systems = page.evaluate('''() => {
            const osmd = window.osmd;
            const result = [];
            if (!osmd || !osmd.GraphicSheet) return result;
            const pages = osmd.GraphicSheet.MusicPages;
            for (let pIdx = 0; pIdx < pages.length; pIdx++) {
                const page = pages[pIdx];
                for (let sIdx = 0; sIdx < page.MusicSystems.length; sIdx++) {
                    const sys = page.MusicSystems[sIdx];
                    const b = sys.PositionAndShape;
                    const staffLines = sys.StaffLines;
                    let minY = 99999;
                    let maxY = -99999;
                    if (staffLines && staffLines.length > 0) {
                        for (let sl of staffLines) {
                            const slBox = sl.PositionAndShape;
                            minY = Math.min(minY, slBox.AbsolutePosition.y * 10);
                            maxY = Math.max(maxY, (slBox.AbsolutePosition.y + slBox.Size.height) * 10);
                        }
                    }
                    let startMeasure = 9999;
                    let endMeasure = -1;
                    const measures = [];
                    if (staffLines && staffLines.length > 0) {
                        const sysMeasures = staffLines[0].Measures;
                        for (let m of sysMeasures) {
                            if (m.MeasureNumber) {
                                startMeasure = Math.min(startMeasure, m.MeasureNumber);
                                endMeasure = Math.max(endMeasure, m.MeasureNumber);
                                const mBox = m.PositionAndShape;
                                measures.push({
                                    number: m.MeasureNumber,
                                    x: mBox ? mBox.AbsolutePosition.x * 10 : 0,
                                    w: mBox ? mBox.Size.width * 10 : 0
                                });
                            }
                        }
                    }
                    result.push({
                        systemIndex: sIdx,
                        y: b.AbsolutePosition.y * 10,
                        h: b.Size.height * 10,
                        w: b.Size.width * 10,
                        staffLinesMinY: minY,
                        staffLinesMaxY: maxY,
                        startMeasure: startMeasure,
                        endMeasure: endMeasure,
                        measures: measures
                    });
                }
            }
            return result;
        }''')

        screenshot_bytes = page.screenshot(type="png", full_page=True)
        img = pygame.image.load(io.BytesIO(screenshot_bytes))
        return img, systems

    def render_full_score(self, xml_content: str, width: int = 1400) -> tuple:
        """Render full MusicXML score. Thread-safe: can be called from any thread."""
        if self._stopped or self._thread is None or not self._thread.is_alive():
            self._start_worker()

        resp_q = queue.Queue()
        self._task_queue.put((xml_content, width, resp_q))
        ok, res = resp_q.get(timeout=30.0)
        if not ok:
            raise res
        return res

    def render_musicxml(self, xml_content: str, width: int = 1400, height: int = 400) -> pygame.Surface:
        full_surf, _ = self.render_full_score(xml_content, width)
        return full_surf

    def stop(self):
        """Stop the worker thread and browser."""
        self._stopped = True
        self._task_queue.put(None)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._thread = None


def _create_render_xml_html(xml_content: str) -> str:
    """Alias for backward compatibility."""
    return _create_render_html(xml_content)


# Global renderer instance
_renderer: Optional[OSMDRenderer] = None


def get_renderer() -> OSMDRenderer:
    """Get or create the global OSMD renderer."""
    global _renderer
    if _renderer is None:
        _renderer = OSMDRenderer()
    return _renderer


def start_renderer():
    """Start the OSMD renderer."""
    get_renderer().start()


def stop_renderer():
    """Stop the OSMD renderer."""
    global _renderer
    if _renderer:
        _renderer.stop()
        _renderer = None


def render_to_surface(xml_content: str, width: int = 1400, height: int = 400) -> pygame.Surface:
    """
    Convenience function to render MusicXML to a Pygame surface.
    """
    return get_renderer().render_musicxml(xml_content, width, height)


def render_full_score(xml_content: str, width: int = 1400) -> tuple:
    """
    Convenience function to render full MusicXML score and extract system bounds.
    """
    return get_renderer().render_full_score(xml_content, width)


if __name__ == "__main__":
    # Test with a sample
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python osmd_render.py <musicxml_file>")
        sys.exit(1)
    
    xml_file = sys.argv[1]
    with open(xml_file, 'r', encoding='utf-8') as f:
        xml_content = f.read()
    
    print(f"Rendering {xml_file}...")
    surface = render_to_surface(xml_content)
    print(f"Rendered: {surface.get_size()}")
    
    # Save for verification
    pygame.image.save(surface, "osmd_output.png")
    print("Saved to osmd_output.png")
    
    stop_renderer()