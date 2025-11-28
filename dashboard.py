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
        self.data = deque(maxlen=200) # Keep last 200 trades
        self.running = False
        self.thread = None

state = StreamState()

# --- SIDEBAR ---
with st.sidebar:
    st.header("🔐 Authentication")
    api_key = st.text_input("Polygon API Key", type="password")
    
    st.divider()
    
    st.header("📡 Scanner Config")
    default_tickers = ["NVDA", "TSLA", "AAPL", "AMD", "SPY", "QQQ", "AMZN", "MSFT", "META", "GOOGL"]
    tickers = st.multiselect("Watchlist", default_tickers, default=["NVDA", "TSLA", "AAPL", "AMD", "SPY", "QQQ"])
    
    # Lower default threshold to ensure you see data immediately for testing
    min_flow = st.number_input("Min Whale Premium ($)", value=25_000, step=5_000)
    
    col1, col2 = st.columns(2)
    start_btn = col1.button("🟢 Start Feed")
    stop_btn = col2.button("🔴 Stop Feed")

# --- FIXED BACKFILL FUNCTION ---
def run_backfill(key, watchlist, threshold):
    """Fetches Today's Top Volume contracts so the table isn't empty on start."""
    try:
        client = RESTClient(key)
        count = 0
        
        # Create a progress bar in the sidebar so you know it's working
        prog_text = st.sidebar.empty()
        prog_bar = st.sidebar.progress(0)
        
        for i, t in enumerate(watchlist):
            prog_text.text(f"Backfilling {t}...")
            prog_bar.progress((i + 1) / len(watchlist))
            
            try:
                # CRITICAL FIX: Sort by 'day_volume' desc to get the REAL whales
                chain = client.list_snapshot_options_chain(
                    t, 
                    params={"limit": 30, "sort": "day_volume", "order": "desc"}
                )
                
                for c in chain:
                    # Validate data exists
                    if c.day and c.day.volume and c.day.close:
                        flow = c.day.close * c.day.volume * 100
                        
                        if flow >= threshold:
                            side = "CALL" if c.details.contract_type == "call" else "PUT"
                            
                            # Add to state
                            state.data.append({
                                "Time": "Today (Sum)", # Mark as Day Summary
                                "Ticker": t,
                                "Tags": "📊 DAY TOP", # Different tag for backfill
                                "Side": side,
                                "Price": c.day.close,
                                "Size": c.day.volume,
                                "Flow": flow,
                                "Symbol": c.details.ticker
                            })
                            count += 1
            except Exception as e:
                print(f"Skipping {t}: {e}")
                continue
        
        prog_text.empty()
        prog_bar.empty()
        return count
    except Exception as e:
        st.error(f"Backfill error: {e}")
        return 0

# --- WEBSOCKET THREAD ---
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

# --- START/STOP LOGIC ---
if start_btn:
    if not api_key:
        st.error("Please enter API Key.")
    elif not state.running:
        # 1. Run Backfill First (With Spinner)
        with st.spinner("⏳ Loading today's top whale activity..."):
            hits = run_backfill(api_key, tickers, min_flow)
            
        if hits > 0:
            st.toast(f"Backfill loaded {hits} whale contracts!", icon="✅")
        else:
            st.toast("Backfill found no trades > threshold.", icon="⚠️")
        
        # 2. Start Thread
        state.running = True
        state.thread = threading.Thread(target=run_websocket, args=(api_key, tickers, min_flow), daemon=True)
        state.thread.start()
        st.success("Live Scanner Active! Listening for new Sweeps...")

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
                    st.error(f"No strikes found for {target_ticker} on {expiry}.")
            
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

# --- TAB 2: LIVE STREAM ---
with tab2:
    st.subheader("🔥 Live Flow: Sweeps & Blocks")
    
    feed_placeholder = st.empty()
    
    def style_df(df):
        def color_rows(row):
            c = '#d4f7d4' if row['Side'] == 'CALL' else '#f7d4d4'
            if "SWEEP" in row['Tags']:
                return [f'background-color: {c}; font-weight: bold; border-left: 5px solid #ffcc00'] * len(row)
            if "DAY TOP" in row['Tags']:
                return [f'background-color: {c}; opacity: 0.8'] * len(row)
            return [f'background-color: {c}; color: black'] * len(row)
        return df.style.apply(color_rows, axis=1).format({"Flow": "${:,.0f}", "Price": "${:.2f}"})

    if state.running or len(state.data) > 0:
        if len(state.data) > 0:
            df = pd.DataFrame(list(state.data))
            
            # Sort so newest (or biggest backfill) is top
            # We sort by Time (descending) roughly, or just display as is (deque is already sorted by insert)
            
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
            st.rerun()
    else:
        st.info("Click 'Start Feed' in the sidebar to load Backfill & Live Data.")
