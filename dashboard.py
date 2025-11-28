import streamlit as st
import pandas as pd
from polygon import RESTClient, WebSocketClient
from polygon.websocket.models import WebSocketMessage
import threading
import time
import asyncio
from datetime import datetime
from collections import deque

# --- PAGE CONFIG ---
st.set_page_config(page_title="FlowTrend Pro Terminal", layout="wide")
st.title("🐋 FlowTrend Pro Terminal")

# --- STATE MANAGEMENT ---
@st.cache_resource
class StreamState:
    def __init__(self):
        self.data = deque(maxlen=1000) # Increased limit to hold more historical data
        self.running = False
        self.thread = None

state = StreamState()

# --- SIDEBAR ---
with st.sidebar:
    st.header("🔐 Authentication")
    api_key = st.text_input("Polygon API Key", type="password")
    
    st.divider()
    
    st.header("📡 Scanner Config")
    # The Mag 7 List
    default_tickers = ["NVDA", "TSLA", "AAPL", "AMD", "SPY", "QQQ", "AMZN", "MSFT", "META", "GOOGL"]
    tickers = st.multiselect("Watchlist", default_tickers, default=["NVDA", "TSLA", "AAPL", "AMD", "SPY", "QQQ"])
    
    # Lowered default to $5k to ensure you see data even on slow days
    min_flow = st.number_input("Min Dollar Amount ($)", value=5_000, step=5_000)
    
    col1, col2 = st.columns(2)
    start_btn = col1.button("🟢 Start Feed")
    stop_btn = col2.button("🔴 Stop Feed")

# --- BACKFILL FUNCTION (The Fix) ---
def run_backfill(key, watchlist, threshold):
    """Fetches the Day's Summary for all existing contracts."""
    client = RESTClient(key)
    count = 0
    
    # Status Container to show progress
    status = st.status("⏳ Downloading today's trade data...", expanded=True)
    
    for t in watchlist:
        try:
            status.write(f"📥 Fetching Top 50 Contracts for **{t}**...")
            
            # Get the 50 most active contracts for this stock today
            chain = client.list_snapshot_options_chain(
                t, 
                params={"limit": 50, "sort": "day_volume", "order": "desc"}
            )
            
            ticker_count = 0
            for c in chain:
                # Ensure data exists
                if c.day and c.day.volume and c.day.close:
                    # Calculate Dollar Amount
                    premium = c.day.close * c.day.volume * 100
                    
                    if premium >= threshold:
                        side = "CALL" if c.details.contract_type == "call" else "PUT"
                        
                        # Add to our list
                        state.data.append({
                            "Symbol": t,                   # Stock Symbol
                            "Strike": c.details.strike_price, # Strike Price
                            "Side": side,                  # Call or Put
                            "Volume": c.day.volume,        # Contract Volume
                            "Value": premium,              # Dollar Amount
                            "Time": "Day Sum",             # Time label
                            "Tags": "📊 HISTORICAL"
                        })
                        count += 1
                        ticker_count += 1
            
            # Debug output
            status.write(f"✅ Found {ticker_count} whales for {t}")
            
        except Exception as e:
            status.warning(f"Failed to fetch {t}: {e}")
            continue
            
    status.update(label=f"Download Complete! Loaded {count} trades.", state="complete", expanded=False)
    return count

# --- WEBSOCKET THREAD (Live Stream) ---
def run_websocket(key, watchlist, threshold):
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except:
        pass

    def handle_msg(msgs: list[WebSocketMessage]):
        for m in msgs:
            if m.event_type == "T":
                try:
                    found_ticker = next((t for t in watchlist if t in m.symbol), None)
                    if not found_ticker: continue

                    flow = m.price * m.size * 100
                    
                    if flow >= threshold:
                        side = "CALL" if "C" in m.symbol else "PUT"
                        
                        # SWEEP DETECTION
                        conditions = m.conditions if hasattr(m, 'conditions') and m.conditions else []
                        is_sweep = 14 in conditions
                        tags = "🧹 SWEEP" if is_sweep else "⚡ LIVE"
                        
                        state.data.appendleft({
                            "Symbol": found_ticker,
                            "Strike": "N/A", # Websocket doesn't send strike easily, we'd need to parse symbol
                            "Side": side,
                            "Volume": m.size,
                            "Value": flow,
                            "Time": time.strftime("%H:%M:%S"),
                            "Tags": tags
                        })
                except: continue

    client = WebSocketClient(api_key=key, feed="delayed.polygon.io", market="options", subscriptions=["T.*"], verbose=False)
    client.run(handle_msg)

# --- START BUTTON LOGIC ---
if start_btn:
    if not api_key:
        st.error("Please enter API Key.")
    else:
        # 1. RUN BACKFILL (This populates the table instantly)
        run_backfill(api_key, tickers, min_flow)
        
        # 2. START LISTENER (For new trades if market is open)
        if not state.running:
            state.running = True
            state.thread = threading.Thread(target=run_websocket, args=(api_key, tickers, min_flow), daemon=True)
            state.thread.start()

if stop_btn:
    state.running = False
    st.warning("Scanner Paused.")

# ==================================================
# TABS INTERFACE
# ==================================================
tab1, tab2 = st.tabs(["🔍 Contract Inspector", "⚡ Live Whale Stream"])

# --- TAB 1: INSPECTOR ---
with tab1:
    if not api_key:
        st.warning("Enter API Key to use.")
    else:
        client = RESTClient(api_key)
        c1, c2 = st.columns([1, 3])
        with c1:
            st.subheader("1. Setup")
            target_ticker = st.selectbox("Select Asset", tickers) 
            try:
                snap = client.get_snapshot_ticker("stocks", target_ticker)
                cur_price = snap.last_trade.price if snap.last_trade else snap.day.close
                st.info(f"📍 {target_ticker}: ${cur_price:.2f}")
            except:
                cur_price = 0
            
            expiry = st.date_input("Expiration", value=datetime.now().date())
            otype = st.radio("Side", ["Call", "Put"], horizontal=True)
            st.write("---")
            try:
                contracts = client.list_options_contracts(
                    underlying_ticker=target_ticker,
                    expiration_date=expiry.strftime("%Y-%m-%d"),
                    contract_type="call" if otype == "Call" else "put",
                    limit=1000
                )
                valid_strikes = sorted(list(set([c.strike_price for c in contracts])))
                if valid_strikes:
                    def_ix = min(range(len(valid_strikes)), key=lambda i: abs(valid_strikes[i]-cur_price)) if cur_price > 0 else 0
                    strike = st.selectbox("Strike Price", valid_strikes, index=def_ix)
                    d_str = expiry.strftime("%y%m%d")
                    t_char = "C" if otype == "Call" else "P"
                    s_str = f"{int(strike*1000):08d}"
                    final_symbol = f"O:{target_ticker}{d_str}{t_char}{s_str}"
                    if st.button("Analyze Contract", type="primary"):
                        st.session_state['active_sym'] = final_symbol
                else:
                    st.error(f"No strikes found.")
            except Exception as e:
                st.error(f"API Error: {e}")

        with c2:
            if 'active_sym' in st.session_state:
                sym = st.session_state['active_sym']
                st.subheader(f"Analysis: {sym}")
                try:
                    osnap = client.get_snapshot_option(target_ticker, sym)
                    if osnap:
                        m1, m2, m3, m4 = st.columns(4)
                        op = osnap.last_trade.price if osnap.last_trade else (osnap.day.close if osnap.day else 0)
                        ov = osnap.day.volume if osnap.day else 0
                        m1.metric("💰 Price", f"${op}", f"Vol: {ov}")
                        if osnap.greeks:
                            m2.metric("Delta", f"{osnap.greeks.delta:.2f}")
                            m3.metric("Gamma", f"{osnap.greeks.gamma:.2f}")
                        st.write("### ⚡ Intraday Chart (5-Min)")
                        today = datetime.now().strftime("%Y-%m-%d")
                        aggs = client.get_aggs(sym, 5, "minute", today, today)
                        if aggs:
                            df = pd.DataFrame(aggs)
                            df['Time'] = pd.to_datetime(df['timestamp'], unit='ms')
                            st.area_chart(df.set_index('Time')['close'], color="#00FF00")
                        else:
                            st.info("No trades yet today.")
                except Exception as e:
                    st.error(f"Data Load Error: {e}")

# --- TAB 2: LIVE STREAM (The Fix) ---
with tab2:
    st.subheader("🔥 Live Flow: Sweeps & Blocks")
    
    feed_placeholder = st.empty()
    
    # Custom Styling for the Table
    def style_df(df):
        def color_rows(row):
            c = '#d4f7d4' if row['Side'] == 'CALL' else '#f7d4d4'
            return [f'background-color: {c}; color: black'] * len(row)
        return df.style.apply(color_rows, axis=1).format({"Value": "${:,.0f}"})

    # Render Table
    if state.running or len(state.data) > 0:
        if len(state.data) > 0:
            df = pd.DataFrame(list(state.data))
            
            # Sort by Time (Newest First) or Value? 
            # Usually for a feed, we want Newest first, but for backfill summary, maybe Value.
            # Let's just show as is (Deque handles order).
            
            with feed_placeholder.container():
                st.dataframe(
                    style_df(df), 
                    use_container_width=True, 
                    height=800,
                    column_config={
                        "Value": st.column_config.ProgressColumn("Dollar Amount", format="$%f", min_value=0, max_value=max(df["Value"].max(), 100_000)),
                        "Volume": st.column_config.NumberColumn("Contract Vol", format="%d"),
                        "Tags": st.column_config.TextColumn("Type", help="🧹 = Aggressive Sweep"),
                    },
                    hide_index=True
                )
        if state.running:
            time.sleep(1)
            st.rerun()
    else:
        st.info("Click 'Start Feed' in the sidebar to load Data.")

