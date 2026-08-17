import asyncio
import httpx
from bs4 import BeautifulSoup
import urllib3
urllib3.disable_warnings()

api_key = '82dafe2655912ea0fd4b57ce1dd6e437838cdb2f'
proxy_url = f'http://{api_key}:premium_proxy=true@proxy.zenrows.com:8001'

async def main():
    async with httpx.AsyncClient(proxy=proxy_url, verify=False, timeout=60) as client:
        r = await client.get('https://verify.sos.ga.gov/Verification/Search.aspx')
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, 'html.parser')
            profession = soup.find('select', id='t_web_lookup__profession_name')
            if profession:
                print('Profession Options:')
                for opt in profession.find_all('option'):
                    if 'Contract' in opt.text or 'Builder' in opt.text:
                        print(f"'{opt.get('value')}' : '{opt.text}'")

asyncio.run(main())
