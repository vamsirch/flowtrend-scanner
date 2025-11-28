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


