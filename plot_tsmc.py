import json
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

try:
    with open('tsmc_price.json', 'r') as f:
        data = json.load(f)
    if data.get('status') != 200:
        print(f"Error: {data.get('msg', 'Unknown error')}")
        exit()

    df = pd.DataFrame(data['data'])
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date')

    # Plotting
    fig, ax = plt.subplots(figsize=(12, 6))

    # Custom candlestick logic
    for i in range(len(df)):
        row = df.iloc[i]
        color = 'red' if row['close'] >= row['open'] else 'green'
        # Wick
        ax.plot([row['date'], row['date']], [row['min'], row['max']], color=color, linewidth=1)
        # Body
        body_bottom = min(row['open'], row['close'])
        body_height = abs(row['open'] - row['close'])
        if body_height == 0: body_height = 0.5 # Min height for flat days
        rect = plt.Rectangle((mdates.date2num(row['date']) - 0.3, body_bottom), 0.6, body_height, color=color)
        ax.add_patch(rect)

    ax.set_title('TSMC (2330) Candlestick Chart (Last Month)', fontsize=14)
    ax.set_xlabel('Date')
    ax.set_ylabel('Price (TWD)')
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
    plt.xticks(rotation=45)
    plt.grid(True, linestyle='--', alpha=0.6)
    plt.tight_layout()

    plt.savefig('tsmc_kline.png')
    print("Chart saved to tsmc_kline.png")
except Exception as e:
    print(f"Error: {e}")
