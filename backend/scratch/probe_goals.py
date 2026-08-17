import asyncio
from playwright.async_api import async_playwright
import sys

async def test_goals():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        print('Navigating to GOALS...')
        await page.goto('https://goals.sos.ga.gov/GASOSOneStop/s/licensee-search', wait_until='domcontentloaded')
        print('Page loaded. Checking for search fields...')
        
        try:
            await page.wait_for_selector('input', timeout=15000)
            print('Inputs found!')
            inputs = await page.eval_on_selector_all('input, select, button', '''elements => {
                return elements.map(e => {
                    const label = e.labels && e.labels.length ? Array.from(e.labels).map(l => l.innerText).join(' ') : (e.getAttribute('aria-label') || '');
                    return {tag: e.tagName, type: e.type, placeholder: e.placeholder, label: label, class: e.className, text: e.innerText};
                });
            }''')
            for i in inputs:
                print(i)
                
        except Exception as e:
            print('Error:', e)
        await browser.close()

asyncio.run(test_goals())
