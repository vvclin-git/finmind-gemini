import sys, json, pandas as pd
try:
    with open('tsmc_price.json', 'r') as f:
        data = json.load(f)
    if data.get('status') != 200:
        print(f"Error: {data.get('msg', 'Unknown error')}")
        sys.exit(0)
    data_list = data.get('data', [])
    if not data_list:
        print('No data found for 2330 in the last month.')
    else:
        df = pd.DataFrame(data_list)
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date')

        start_price = df.iloc[0]['close']
        end_price = df.iloc[-1]['close']
        max_price = df['max'].max()
        min_price = df['min'].min()
        change = ((end_price - start_price) / start_price) * 100

        print(f"Period: {df.iloc[0]['date'].strftime('%Y-%m-%d')} to {df.iloc[-1]['date'].strftime('%Y-%m-%d')}")
        print(f"Start Price: {start_price}")
        print(f"End Price: {end_price}")
        print(f"Change: {change:+.2f}%")
        print(f"Max Price: {max_price}")
        print(f"Min Price: {min_price}")
        print("\nLast 5 days:")
        print(df[['date', 'open', 'max', 'min', 'close', 'Trading_Volume']].tail(5).to_string(index=False))
except Exception as e:
    print(f"Error processing data: {e}")
