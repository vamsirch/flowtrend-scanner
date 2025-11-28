import streamlit as st
from polygon import RESTClient
from datetime import datetime, timedelta
import pandas as pd
import time

# --- PAGE CONFIG ---
st.set_page_config(page_title="Mag 7 Options Command Center", layout="wide")
st.title("💎 Mag 7 Options Command Center")

# --- SIDEBAR: CONFIGURATION ---
st.sidebar.header("Configuration")
api_key = st.sidebar.text_input("Polygon API Key", type="password")

# Define the Mag 7 Tickers
MAG_7 = ["SPY", "QQQ", "NVDA", "TSLA", "AAPL", "AMD", "AMZN", "MSFT", "META", "GOOGL"]

if not api_key:
    st.info("Please enter your API Key in the sidebar to unlock the dashboard.")
    st.stop()

client = RESTClient(api_key)

# --- TABS LAYOUT ---
tab1, tab2 = st.tabs(["🔍 Contract Inspector", "📡 Mag 7 Live Flow"])

# ==========================================
# TAB 1: CONTRACT INSPECTOR (Specific Data)
# ==========================================
with tab1:
    col_config, col_main = st.columns([1, 3])
    
    with col_config:
        st.subheader("Select Contract")
        
        # 1. Asset Selection
        ticker = st.selectbox("Ticker", MAG_7)
        
        # Auto-fetch price
        try:
            stock_snap = client.get_snapshot_ticker("stocks", ticker)
            cur_price = stock_snap.last_trade.price
            st.success(f"Current Price: ${cur_price}")
        except:
            cur_price = 0
            st.warning("Connecting...")

        # 2. Date Selection
        default_date = datetime.now().date()
        expiration = st.date_input("Expiration", value=default_date)
        
        # 3. Type Selection
        option_type = st.radio("Side", ["Call", "Put"], horizontal=True)
        type_code = "call" if option_type == "Call" else "put"

        # 4. Smart Strike Picker
        st.write("---")
        try:
            contracts = client.list_options_contracts(
                underlying_ticker=ticker,
                expiration_date=expiration.strftime("%Y-%m-%d"),
                contract_type=type_code,
                limit=1000
            )
            valid_strikes = sorted(list(set([c.strike_price for c in contracts])))
            
            if valid_strikes:
                default_ix = min(range(len(valid_strikes)), key=lambda i: abs(valid_strikes[i]-cur_price)) if cur_price > 0 else 0
                selected_strike = st.selectbox("Strike Price", valid_strikes, index=default_ix)
                
                # Build Symbol
                date_str = expiration.strftime("%y%m%d")
                type_char = "C" if option_type == "Call" else "P"
                strike_str = f"{int(selected_strike*1000):08d}"
                contract_symbol = f"O:{ticker}{date_str}{type_char}{strike_str}"
                
                if st.button("Analyze Contract", type="primary"):
                    st.session_state['symbol_to_analyze'] = contract_symbol
            else:
                st.error("No strikes found for date.")
        except Exception as e:
            st.error(f"Error: {e}")

    with col_main:
        if 'symbol_to_analyze' in st.session_state:
            sym = st.session_state['symbol_to_analyze']
            st.subheader(f"Analysis: {sym}")
            
            try:
                snapshot = client.get_snapshot_option(ticker, sym)
                if snapshot:
                    # Metrics
                    m1, m2, m3, m4 = st.columns(4)
                    
                    # Price Logic
                    if snapshot.last_trade:
                        price = snapshot.last_trade.price
                        vol = snapshot.last_trade.size
                    elif snapshot.day:
                        price = snapshot.day.close
                        vol = snapshot.day.volume
                    else:
                        price = 0
                    
                    m1.metric("💰 Premium", f"${price}", f"Vol: {vol}")
                    m2.metric("Bid / Ask", f"${snapshot.last_quote.bid_price} / ${snapshot.last_quote.ask_price}" if snapshot.last_quote else "-")
                    
                    if snapshot.greeks:
                        m3.metric("Delta", f"{snapshot.greeks.delta:.2f}")
                        m4.metric("Gamma", f"{snapshot.greeks.gamma:.2f}")
                    
                    # 5-Min Intraday Chart
                    st.write("### ⚡ Today's 5-Min Interval Chart")
                    today_str = datetime.now().strftime("%Y-%m-%d")
                    intraday = client.get_aggs(sym, 5, "minute", today_str, today_str)
                    
                    if intraday:
                        df_chart = pd.DataFrame(intraday)
                        df_chart['Time'] = pd.to_datetime(df_chart['timestamp'], unit='ms')
                        st.area_chart(df_chart.set_index('Time')['close'], color="#00FF00")
                    else:
                        st.info("No trades in the last few hours.")
            except Exception as e:
                st.error(f"Could not load data: {e}")

# ==========================================
# TAB 2: MAG 7 LIVE SCANNER (Market Wide)
# ==========================================
with tab2:
    st.subheader("🔥 Mag 7 Options Scanner (High Volume)")
    
    col_scan1, col_scan2 = st.columns([1, 4])
    
    with col_scan1:
        min_prem = st.number_input("Min Premium ($)", value=50000, step=10000)
        run_scan = st.button("🔄 Scan Market Now")
    
    if run_scan:
        status = st.status("Scanning Mag 7 Tickers...", expanded=True)
        all_whales = []
        progress = status.progress(0)
        
        for i, t in enumerate(MAG_7):
            status.write(f"Scanning {t}...")
            progress.progress((i+1)/len(MAG_7))
            
            try:
                # Fetch the chain snapshot
                # This gets the summary of the day so far
                chain = client.list_snapshot_options_chain(t, params={"limit": 250})
                
                for c in chain:
                    if not c.day or not c.day.volume or not c.day.close:
                        continue
                    
                    # Filter for active flow
                    premium = c.day.close * c.day.volume * 100
                    
                    if premium >= min_prem:
                        # Determine Sentiment
                        side = "BULLISH 🐂" if c.details.contract_type == "call" else "BEARISH 🐻"
                        
                        all_whales.append({
                            "Ticker": t,
                            "Strike": f"${c.details.strike_price}",
                            "Expiry": c.details.expiration_date,
                            "Type": c.details.contract_type.upper(),
                            "Premium": premium,
                            "Price": f"${c.day.close}",
                            "Vol": c.day.volume,
                            "Sentiment": side
                        })
                # Sleep to respect rate limits
                time.sleep(0.1)
                
            except Exception as e:
                pass # Skip errors to keep scanning
        
        progress.progress(100)
        status.update(label="Scan Complete", state="complete", expanded=False)
        
        # Display Results
        if all_whales:
            df = pd.DataFrame(all_whales)
            df = df.sort_values(by="Premium", ascending=False)
            df["Premium"] = df["Premium"].apply(lambda x: f"${x:,.0f}")
            
            # Styling
            def color_row(row):
                color = '#d4f7d4' if 'BULLISH' in row['Sentiment'] else '#f7d4d4'
                return [f'background-color: {color}; color: black']*len(row)

            st.dataframe(
                df.style.apply(color_row, axis=1),
                use_container_width=True,
                height=800
            )
        else:
            st.warning("No significant flow found matching your criteria.")