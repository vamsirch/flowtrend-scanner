import streamlit as st
import pandas as pd
from polygon import RESTClient, WebSocketClient
from polygon.websocket.models import WebSocketMessage
import threading
import time
import asyncio
from datetime import datetime, timedelta
from collections import deque

# --- PAGE CONFIG ---
st.set_page_config(page_title="FlowTrend Pro Terminal", layout="wide")
st.title("🐋 FlowTrend Pro Terminal")
st.caption("Institutional Grade Options Analysis & Real-Time Flow")

# --- GLOBAL STATE (For Background Thread) ---
@st.cache_resource
class StreamState:
    def __init__(self):
        self.data = deque(maxlen=200) # Keep last 200 trades
        self.running = False
        self.thread = None

state = StreamState()

# --- SIDEBAR: GLOBAL SETTINGS ---
with st.sidebar:
    st.header("🔐 Authentication")
    api_key = st.text_input("Polygon API Key", type="password")
    
    st.divider()
    
    # Watchlist for Scanner
    st.header("📡 Scanner Config")
    tickers = st.multiselect(
        "Watchlist", 
        ["NVDA", "TSLA", "AAPL", "AMD", "AMZN", "MSFT", "META", "GOOGL", "SPY", "QQQ", "IWM", "COIN"],
        default=["NVDA", "TSLA", "AAPL", "AMD", "SPY", "QQQ"]
    )
    min_flow = st.number_input("Min Whale Premium ($)", value=25_000, step=5_000)
    
    col1, col2 = st.columns(2)
    start_btn = col1.button("🟢 Start Feed")
    stop_btn = col2.button("🔴 Stop Feed")
    
    st.divider()
    st.info("ℹ️ **Tab 1:** Deep dive into specific contracts.\n\nℹ️ **Tab 2:** Watch the live market for Sweeps.")

# --- WEBSOCKET THREAD LOGIC ---
def run_websocket(key, watchlist, threshold):
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except:
        pass

    def handle_msg(msgs: list[WebSocketMessage]):
        for m in msgs:
            if m.event_type == "T":
                try:
                    # Filter
                    found_ticker = next((t for t in watchlist if t in m.symbol), None)
                    if not found_ticker: continue

                    flow = m.price * m.size * 100
                    
                    if flow >= threshold:
                        side = "CALL" if "C" in m.symbol else "PUT"
                        
                        # SWEEP DETECTION (Condition 14)
                        conditions = m.conditions if hasattr(m, 'conditions') and m.conditions else []
                        is_sweep = 14 in conditions
                        tags = "🧹 SWEEP" if is_sweep else "🧱 BLOCK"
                        
                        state.data.appendleft({
                            "Time": time.strftime("%H:%M:%S"),
                            "Ticker": found_ticker,
                            "Tags": tags,
                            "Side": side,
                            "Price": m.price,
                            "Size": m.size,
                            "Flow": flow,
                            "Symbol": m.symbol
                        })
                except: continue

    client = WebSocketClient(api_key=key, feed="delayed.polygon.io", market="options", subscriptions=["T.*"], verbose=False)
    client.run(handle_msg)

# Handle Start/Stop
if start_btn and not state.running:
    if not api_key:
        st.error("Please enter API Key.")
    else:
        state.running = True
        state.thread = threading.Thread(target=run_websocket, args=(api_key, tickers, min_flow), daemon=True)
        state.thread.start()
        st.success("Background Scanner Started!")

if stop_btn:
    state.running = False
    st.warning("Scanner Paused.")


# ==================================================
# MAIN INTERFACE TABS
# ==================================================
tab1, tab2 = st.tabs(["🔍 Contract Inspector", "⚡ Live Whale Stream"])

# --- TAB 1: CONTRACT INSPECTOR (Your "Mag 7" Deep Dive) ---
with tab1:
    if not api_key:
        st.warning("Enter API Key in sidebar to use.")
    else:
        client = RESTClient(api_key)
        c1, c2 = st.columns([1, 3])
        
        with c1:
            st.subheader("1. Select Asset")
            target_ticker = st.selectbox("Ticker", tickers) # Uses sidebar watchlist
            
            # Smart Price Check
            try:
                stock = client.get_snapshot_ticker("stocks", target_ticker)
                cur_price = stock.last_trade.price if stock.last_trade else stock.day.close
                st.success(f"📍 {target_ticker}: ${cur_price:.2f}")
            except:
                cur_price = 0
                st.warning("Connecting...")

            st.subheader("2. Pick Option")
            expiry = st.date_input("Expiration", value=datetime.now().date())
            otype = st.radio("Side", ["Call", "Put"], horizontal=True)
            type_code = "call" if otype == "Call" else "put"
            
            # Smart Strike Fetcher
            st.write("---")
            try:
                contracts = client.list_options_contracts(target_ticker, expiry, type_code, limit=1000)
                valid_strikes = sorted(list(set([c.strike_price for c in contracts])))
                
                if valid_strikes:
                    def_ix = min(range(len(valid_strikes)), key=lambda i: abs(valid_strikes[i]-cur_price)) if cur_price > 0 else 0
                    strike = st.selectbox("Strike Price", valid_strikes, index=def_ix)
                    
                    # Build Symbol
                    d_str = expiry.strftime("%y%m%d")
                    t_char = "C" if otype == "Call" else "P"
                    s_str = f"{int(strike*1000):08d}"
                    final_symbol = f"O:{target_ticker}{d_str}{t_char}{s_str}"
                    
                    if st.button("Analyze This Contract", type="primary"):
                        st.session_state['active_symbol'] = final_symbol
                else:
                    st.error("No strikes found.")
            except Exception as e:
                st.error(f"Error fetching chain: {e}")

        with c2:
            if 'active_symbol' in st.session_state:
                sym = st.session_state['active_symbol']
                st.subheader(f"Analysis: {sym}")
                
                try:
                    snap = client.get_snapshot_option(target_ticker, sym)
                    if snap:
                        # Metrics
                        m1, m2, m3, m4 = st.columns(4)
                        p = snap.last_trade.price if snap.last_trade else (snap.day.close if snap.day else 0)
                        v = snap.day.volume if snap.day else 0
                        
                        m1.metric("💰 Premium", f"${p}", f"Vol: {v}")
                        if snap.greeks:
                            m2.metric("Delta", f"{snap.greeks.delta:.2f}")
                            m3.metric("Gamma", f"{snap.greeks.gamma:.2f}")
                            m4.metric("IV", f"{snap.implied_volatility:.2f}")
                        
                        # Charts
                        st.write("### ⚡ Intraday Action (5-Min)")
                        today = datetime.now().strftime("%Y-%m-%d")
                        aggs = client.get_aggs(sym, 5, "minute", today, today)
                        if aggs:
                            df = pd.DataFrame(aggs)
                            df['Time'] = pd.to_datetime(df['timestamp'], unit='ms')
                            st.area_chart(df.set_index('Time')['close'], color="#00FF00")
                        else:
                            st.info("No trades yet today.")
                except Exception as e:
                    st.error(f"Load failed: {e}")

# --- TAB 2: LIVE WHALE STREAM (The Scanner) ---
with tab2:
    st.subheader("🔥 Live Flow: Sweeps & Blocks")
    
    feed_placeholder = st.empty()
    
    def style_df(df):
        def color_rows(row):
            c = '#d4f7d4' if row['Side'] == 'CALL' else '#f7d4d4'
            if "SWEEP" in row['Tags']:
                return [f'background-color: {c}; font-weight: bold; border-left: 5px solid #ffcc00'] * len(row)
            return [f'background-color: {c}; color: black'] * len(row)
        return df.style.apply(color_rows, axis=1).format({"Flow": "${:,.0f}", "Price": "${:.2f}"})

    # Auto-Refresh Loop for Tab 2
    if state.running or len(state.data) > 0:
        if len(state.data) > 0:
            df = pd.DataFrame(list(state.data))
            with feed_placeholder.container():
                st.dataframe(
                    style_df(df), 
                    use_container_width=True, 
                    height=800,
                    column_config={
                        "Flow": st.column_config.ProgressColumn("Premium", format="$%f", min_value=0, max_value=max(df["Flow"].max(), 100_000)),
                        "Tags": st.column_config.TextColumn("Type", help="🧹 = Aggressive Sweep"),
                    },
                    hide_index=True
                )
        if state.running:
            time.sleep(1)
            st.rerun() # Keeps the data fresh
    else:
        st.info("Click 'Start Feed' in the sidebar to connect to the market.")
