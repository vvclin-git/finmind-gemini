import sys, json, pandas as pd
try:
    with open('taiex_data.json', 'r') as f:
        data = json.load(f)
    if data.get('status') != 200:
        print(f"Error: {data.get('msg', 'Unknown error')}")
        sys.exit(0)
    data_list = data.get('data', [])
    if not data_list:
        print('No data found for 2026-04-08.')
    else:
        df = pd.DataFrame(data_list)
        last_row = df.iloc[-1]
        print(f"Date: {last_row['date']}")
        print(f"TAIEX: {last_row['TAIEX']}")
except Exception as e:
    print(f"Error processing data: {e}")
