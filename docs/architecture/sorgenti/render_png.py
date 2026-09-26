import asyncio,sys,glob,os,re
from playwright.async_api import async_playwright
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch()
        for f in sorted(glob.glob(sys.argv[1])):
            w,h=map(int,re.search(r'viewBox="0 0 (\d+) (\d+)"',open(f).read()).groups())
            pg=await b.new_page(viewport={'width':w,'height':h})
            await pg.goto('file://'+os.path.abspath(f)); await pg.wait_for_timeout(300)
            await pg.screenshot(path=f.replace('.svg','.png')); await pg.close()
        await b.close()
asyncio.run(main())
