import urllib.request
import json
import urllib.error

req = urllib.request.Request(
    'http://127.0.0.1:8000/export',
    data=b'{"format":"csv","verified_only":false}',
    headers={'Content-Type': 'application/json'}
)
try:
    with urllib.request.urlopen(req) as response:
        print(response.status)
except urllib.error.HTTPError as e:
    print(f"HTTPError: {e.code}")
    print(e.read().decode())
