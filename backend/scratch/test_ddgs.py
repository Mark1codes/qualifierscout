import time
from ddgs import DDGS

def test():
    with DDGS() as ddgs:
        for i in range(5):
            print(f"Query {i+1}...")
            try:
                res = list(ddgs.text("test query", max_results=2))
                print(f"Got {len(res)} results")
            except Exception as e:
                print(f"Error: {e}")
            time.sleep(1)

if __name__ == "__main__":
    test()
