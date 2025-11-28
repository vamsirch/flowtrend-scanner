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

# --- GLOBAL STATE ---
@st.cache_resource
class StreamState:
    def __init__(self):
        self.data = deque(maxlen=2000) 
        self.running = False
        self.thread = None

state = StreamState()

# ==========================================
# 1. SIDEBAR & SETTINGS
# ==========================================
with st.sidebar:
    st.header("🔐 Authentication")
    api_key = st.text_input("Polygon API Key", type="password")
    
    st.divider()
    
    st.header("📡 Scanner Config")
    default_tickers = ["NVDA", "TSLA", "AAPL", "AMD", "SPY", "QQQ", "AMZN", "MSFT", "META", "GOOGL"]
    tickers = st.multiselect("Watchlist", default_tickers, default=["NVDA", "TSLA", "AAPL", "AMD", "SPY", "QQQ"])
    
    min_flow = st.number_input("Min Whale Value ($)", value=10_000, step=5_000)
    
    col1, col2 = st.columns(2)
    start_btn = col1.button("🟢 Start Feed")
    stop_btn = col2.button("🔴 Stop Feed")

# ==========================================
# 2. BACKEND LOGIC (Backfill & WebSocket)
# ==========================================
def run_backfill(key, watchlist, threshold):
    """Downloads today's biggest trades to fill the table immediately."""
    client = RESTClient(key)
    count = 0
    status = st.status("⏳ Backfilling today's top trades...", expanded=True)
    
    for t in watchlist:
        try:
            status.write(f"Scanning {t}...")
            # Fetch Top 50 Active Options for this stock
            chain = client.list_snapshot_options_chain(
                t, 
                params={"limit": 50, "sort": "day_volume", "order": "desc"}
            )
            
            for c in chain:
                if c.day and c.day.volume and c.day.close:
                    flow = c.day.close * c.day.volume * 100
                    if flow >= threshold:
                        side = "CALL" if c.details.contract_type == "call" else "PUT"
                        state.data.append({
                            "Symbol": t,
                            "Strike": c.details.strike_price,
                            "Side": side,
                            "Volume": c.day.volume,
                            "Value": flow,
                            "Time": "Day Sum",
                            "Tags": "📊 HISTORICAL"
                        })
                        count += 1
        except:
            continue
            
    status.update(label=f"Backfill Complete: Loaded {count} trades.", state="complete", expanded=False)

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
                        # Detect Sweep (Code 14)
                        conds = m.conditions if hasattr(m, 'conditions') and m.conditions else []
                        tag = "🧹 SWEEP" if 14 in conds else "⚡ LIVE"
                        
                        state.data.appendleft({
                            "Symbol": found_ticker,
                            "Strike": "N/A", # Live stream raw symbol parsing is complex, N/A for speed
                            "Side": side,
                            "Volume": m.size,
                            "Value": flow,
                            "Time": time.strftime("%H:%M:%S"),
                            "Tags": tag
                        })
                except: continue

    client = WebSocketClient(api_key=key, feed="delayed.polygon.io", market="options", subscriptions=["T.*"], verbose=False)
    client.run(handle_msg)

# Handle Buttons
if start_btn:
    if not api_key:
        st.error("API Key Required")
    else:
        run_backfill(api_key, tickers, min_flow)
        if not state.running:
            state.running = True
            state.thread = threading.Thread(target=run_websocket, args=(api_key, tickers, min_flow), daemon=True)
            state.thread.start()

if stop_btn:
    state.running = False
    st.warning("Scanner Paused.")

# ==========================================
# 3. FRONTEND TABS (The Layout Fix)
# ==========================================

def render_inspector():
    """Logic for Tab 1: Single Contract Inspector"""
    if not api_key:
        st.warning("Please enter API Key.")
        return

    client = RESTClient(api_key)
    c1, c2 = st.columns([1, 3])
    
    with c1:
        st.subheader("1. Setup")
        target = st.selectbox("Ticker", tickers)
        
        # Price Check
        try:
            snap = client.get_snapshot_ticker("stocks", target)
            price = snap.last_trade.price if snap.last_trade else snap.day.close
            st.info(f"📍 {target}: ${price:.2f}")
        except:
            price = 0
            
        expiry = st.date_input("Expiration", value=datetime.now().date())
        side = st.radio("Side", ["Call", "Put"], horizontal=True)
        
        st.write("---")
        
        # Strike Logic
        try:
            contracts = client.list_options_contracts(
                underlying_ticker=target,
                expiration_date=expiry.strftime("%Y-%m-%d"),
                contract_type="call" if side == "Call" else "put",
                limit=1000
            )
            strikes = sorted(list(set([c.strike_price for c in contracts])))
            
            if strikes:
                def_ix = min(range(len(strikes)), key=lambda i: abs(strikes[i]-price)) if price > 0 else 0
                sel_strike = st.selectbox("Strike", strikes, index=def_ix)
                
                # Build Symbol
                d_str = expiry.strftime("%y%m%d")
                t_char = "C" if side == "Call" else "P"
                s_str = f"{int(sel_strike*1000):08d}"
                final_sym = f"O:{target}{d_str}{t_char}{s_str}"
                
                if st.button("Analyze", type="primary"):
                    st.session_state['active'] = final_sym
            else:
                st.error("No strikes found.")
        except Exception as e:
            st.error(f"Error: {e}")

    with c2:
        if 'active' in st.session_state:
            sym = st.session_state['active']
            st.subheader(f"Analysis: {sym}")
            try:
                snap = client.get_snapshot_option(target, sym)
                if snap:
                    m1, m2, m3, m4 = st.columns(4)
                    p = snap.last_trade.price if snap.last_trade else (snap.day.close if snap.day else 0)
                    v = snap.day.volume if snap.day else 0
                    m1.metric("💰 Price", f"${p}", f"Vol: {v}")
                    if snap.greeks:
                        m2.metric("Delta", f"{snap.greeks.delta:.2f}")
                        m3.metric("Gamma", f"{snap.greeks.gamma:.2f}")
                    
                    st.write("### ⚡ Intraday Chart")
                    today = datetime.now().strftime("%Y-%m-%d")
                    aggs = client.get_aggs(sym, 5, "minute", today, today)
                    if aggs:
                        df = pd.DataFrame(aggs)
                        df['Time'] = pd.to_datetime(df['timestamp'], unit='ms')
                        st.area_chart(df.set_index('Time')['close'], color="#00FF00")
                    else:
                        st.info("No trades today.")
            except:
                st.error("Data load failed.")

def render_scanner():
    """Logic for Tab 2: Live Feed"""
    st.subheader("🔥 Live Flow: Sweeps & Blocks")
    feed_spot = st.empty()
    
    # Custom Table Style
    def style_df(df):
        def color_rows(row):
            c = '#d4f7d4' if row['Side'] == 'CALL' else '#f7d4d4'
            return [f'background-color: {c}; color: black'] * len(row)
        return df.style.apply(color_rows, axis=1).format({"Value": "${:,.0f}"})

    if state.running or len(state.data) > 0:
        if len(state.data) > 0:
            df = pd.DataFrame(list(state.data))
            with feed_spot.container():
                st.dataframe(
                    style_df(df),
                    use_container_width=True,
                    height=800,
                    column_config={
                        "Value": st.column_config.ProgressColumn("Dollar Amount", format="$%f", min_value=0, max_value=max(df["Value"].max(), 100_000)),
                        "Volume": st.column_config.NumberColumn("Vol", format="%d"),
                    },
                    hide_index=True
                )
        if state.running:
            time.sleep(1)
            st.rerun()
    else:
        st.info("👈 Click 'Start Feed' in the sidebar.")

# --- MAIN LAYOUT ---
tab1, tab2 = st.tabs(["🔍 Contract Inspector", "⚡ Live Whale Stream"])

with tab1:
    render_inspector()

with tab2:
    render_scanner()

