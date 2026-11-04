"""Optional headless UI acceptance check; uses an installed browser, no user profile."""
from pathlib import Path
import os
import sys
import threading
import time
import json

from playwright.sync_api import sync_playwright
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from diamond_mcp.engine import Actor, Config
from diamond_mcp.service import DiscoveryService as Engine
from diamond_mcp.store import uid
from diamond_mcp.web import create_web


def main():
    state = ROOT / ".diamond/ui" / uid()[:8]
    source = state / "source"
    source.mkdir(parents=True)
    (source / "schema.sql").write_text("CREATE TABLE agreement (C_ID INT, X17 DATE, active BOOLEAN);")
    (source / "dictionary.md").write_text("# Agreement\nX17 is the renewal deadline of a contract. C_ID identifies that contract.")
    engine = Engine(Config(state / "state", (source,)))
    actor = Actor("local-ai-client", frozenset({"local"}))
    app = engine.register(actor, "local", "Agreement discovery", source)["id"]
    job = engine.start(actor, "local", app, background=False)
    found = engine.search(actor, "local", app, job["id"], "X17", "exact")["findings"][0]
    engine.propose(actor, "local", app, job["id"], [{"subject":"X17", "meaning":"Renewal deadline", "evidence_ids":[found["id"]]}])
    token = "viewer-integration-test-token-no-real-credentials"
    server = uvicorn.Server(uvicorn.Config(create_web(engine, local_token=token), host="127.0.0.1", port=8767,
        log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(.03)
    output = ROOT / "artifacts/viewer"
    output.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as pw:
            executable = os.getenv("DIAMOND_TEST_BROWSER", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
            browser = pw.chromium.launch(executable_path=executable, headless=True)
            page = browser.new_page(viewport={"width":1440, "height":1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto("http://127.0.0.1:8767")
            page.locator("#token").fill(token)
            page.get_by_role("button", name="Connect", exact=True).click()
            page.locator("#application option[value='" + app + "']").wait_for(state="attached")
            page.locator("#application").select_option(app)
            page.locator("#graph svg rect").first.wait_for()
            page.get_by_text("Diagram source and extraction coverage",exact=True).click()
            page.get_by_role("button",name="Inspect coverage",exact=True).click()
            page.locator("#coverage p").first.wait_for()
            page.screenshot(path=str(output / "architecture.png"), full_page=True)
            page.get_by_role("button", name="Evidence & review", exact=True).click()
            page.locator("#query").fill("renewal deadline")
            page.get_by_role("button", name="Search", exact=True).click()
            page.locator("#findings article").first.wait_for()
            page.get_by_role("button", name="Accept interpretation", exact=True).click()
            page.locator("#suggestions .tag").filter(has_text="ACCEPTED").wait_for()
            page.screenshot(path=str(output / "evidence.png"), full_page=True)
            page.get_by_role("button", name="Lineage", exact=True).click()
            page.get_by_role("button",name="Find SQL in the application",exact=True).click()
            page.locator("#inventory article").first.wait_for()
            page.locator("#sql").fill("SELECT a.X17 AS expiry FROM agreement a WHERE a.active=TRUE")
            page.get_by_role("button", name="Explain static SQL", exact=True).click()
            page.locator("#sqlresult").get_by_text("Value sources", exact=True).wait_for()
            assert "expiry" in page.locator("#sqlresult").inner_text()
            assert "WHERE" in page.locator("#sqlresult").inner_text()
            page.screenshot(path=str(output / "lineage.png"), full_page=True)
            page.set_viewport_size({"width":390,"height":844})
            page.screenshot(path=str(output / "mobile.png"), full_page=True)
            assert not errors, errors
            browser.close()
        result = {"status":"PASSED", "checks":["connect", "application/snapshot selection", "service architecture", "coverage", "SQL inventory", "metadata search", "separate human acceptance", "value/predicate lineage", "mobile viewport", "no page errors"]}
        (output / "results.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
    finally:
        server.should_exit = True
        thread.join(5)


if __name__ == "__main__":
    main()
