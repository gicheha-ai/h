"""
FOREX TECHNICAL SENTINEL - Pure Technical Analysis Edition
Complete 90-pair analysis with professional 80-point technical scoring
No fundamental analysis, only technical (80) + sentiment (20) = 100 total
Shows ALL pairs with score ≥ 70 - NO OTHER FILTERS
"""

import requests
import pandas as pd
import numpy as np
import json
import schedule
import time
import threading
import os
import sys
import re
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict, field
from typing import List, Dict, Optional, Tuple, Set, Deque, Any
import logging
from pathlib import Path
import hashlib
from functools import lru_cache
from collections import deque
import math
import asyncio
import websockets

# ============================================================================
# CONFIGURATION & SETUP - OPTIMIZED FOR RENDER
# ============================================================================

# Configure logging for Render
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

@dataclass
class Config:
    """Production configuration optimized for Render"""
    # API Keys from environment with defaults
    TWELVEDATA_KEY: str = field(default_factory=lambda: os.environ.get(
        "TWELVEDATA_KEY", "2664b95fd52c490bb422607ef142e61f"
    ))
    DERIV_TOKEN: str = field(default_factory=lambda: os.environ.get(
        "DERIV_TOKEN", "7LndOxnscxGr"
    ))
    
    # Scanner Settings
    MIN_CONFLUENCE_SCORE: int = 70  # THE ONLY FILTER - NO OTHER FILTERS
    SCAN_INTERVAL_MINUTES: int = 15
    TOTAL_PAIRS: int = 90  # ALL 90 pairs will be analyzed
    
    # Professional TP/SL Settings (SAME AS BEFORE)
    MIN_RISK_REWARD: float = 1.5
    MIN_SUCCESS_PROBABILITY: float = 0.6
    MAX_TRADE_DURATION_DAYS: int = 30
    
    # Render Optimization
    SELF_PING_INTERVAL_MINUTES: int = 10
    APP_URL: str = field(default_factory=lambda: os.environ.get(
        "RENDER_APP_URL", 
        os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:5000")
    ))
    
    # Data Storage
    DATA_DIR: str = "/tmp/data" if 'RENDER' in os.environ else "data"
    
    # Performance
    REQUEST_TIMEOUT: int = 30
    CACHE_TTL_MINUTES: int = 15  # Cache indicator calculations
    MAX_RETRIES: int = 3
    
    # WebSocket Settings
    DERIV_WS_URL: str = "wss://ws.derivws.com/websockets/v3?app_id=1089"
    WS_BATCH_SIZE: int = 3
    WS_BATCH_DELAY: float = 1.0
    WS_PAIR_DELAY: float = 0.1
    WS_RECONNECT_INTERVAL: int = 300  # 5 minutes
    
    # Technical Analysis Settings
    HISTORICAL_PERIODS: int = 500  # Enough for all indicator calculations
    PRICE_HISTORY_LENGTH: int = 1000  # Store 1000 periods locally
    
    # 10 Base Currencies (all pairs formed from these)
    BASE_CURRENCIES: List[str] = field(default_factory=lambda: [
        'EUR', 'GBP', 'USD', 'JPY', 'CHF', 
        'AUD', 'CAD', 'NZD', 'XAU', 'BTC', 'ETH'
    ])
    
    def __post_init__(self):
        """Initialize for Render"""
        try:
            os.makedirs(self.DATA_DIR, exist_ok=True)
            logger.info(f"Data directory: {self.DATA_DIR}")
        except:
            pass

# Initialize config globally
config = Config()

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def get_cache_key(*args):
    """Generate cache key from arguments"""
    key_string = ":".join(str(arg) for arg in args)
    return hashlib.md5(key_string.encode()).hexdigest()

class MemoryCache:
    """Simple in-memory cache for Render"""
    def __init__(self, ttl_minutes=5):
        self.cache = {}
        self.ttl = ttl_minutes * 60
        
    def get(self, key):
        """Get from cache"""
        if key in self.cache:
            data, timestamp = self.cache[key]
            if time.time() - timestamp < self.ttl:
                return data
            else:
                del self.cache[key]
        return None
    
    def set(self, key, data):
        """Set to cache"""
        self.cache[key] = (data, time.time())
        # Limit cache size
        if len(self.cache) > 1000:
            # Remove oldest 100 entries
            keys = sorted(self.cache.keys(), 
                         key=lambda k: self.cache[k][1])[:100]
            for k in keys:
                del self.cache[k]
    
    def clear(self):
        """Clear cache"""
        self.cache.clear()

cache = MemoryCache(ttl_minutes=config.CACHE_TTL_MINUTES)

def retry_request(func, max_retries=3, delay=1):
    """Retry decorator for API requests"""
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            logger.warning(f"Attempt {attempt + 1} failed: {e}")
            time.sleep(delay * (attempt + 1))
    return None

def generate_all_pairs(currencies: List[str]) -> List[str]:
    """Generate ALL possible pairs from currencies (90 pairs total)"""
    pairs = []
    for i, base in enumerate(currencies):
        for j, quote in enumerate(currencies):
            if i != j:  # Skip same currency pairs
                pairs.append(f"{base}/{quote}")
    return pairs

ALL_PAIRS = generate_all_pairs(config.BASE_CURRENCIES)
logger.info(f"Generated {len(ALL_PAIRS)} pairs for analysis")

# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class Opportunity:
    """Complete trading opportunity with all features"""
    pair: str
    direction: str
    confluence_score: int
    catalyst: str = "Pure Technical Analysis"
    setup_type: str
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    risk_pips: float
    reward_pips: float
    probability_tp_before_sl: float
    estimated_duration_days: int
    context: str
    confidence: str
    analysis_summary: str
    fundamentals_summary: str = "Technical Analysis Only - No News Catalysts"
    technicals_summary: str
    sentiment_summary: str
    detected_at: str
    scan_id: str
    
    # Technical score breakdown
    technical_breakdown: Dict[str, int]
    
    def to_dict(self):
        data = asdict(self)
        return data

@dataclass
class ScanResult:
    """Complete scan results"""
    scan_id: str
    timestamp: str
    pairs_scanned: int
    very_high_probability_setups: int
    opportunities: List[Opportunity]
    scan_duration_seconds: float
    market_state: str
    score_distribution: Dict[str, int]  # Count of pairs by score range
    
    def to_dict(self):
        return {
            'scan_id': self.scan_id,
            'timestamp': self.timestamp,
            'pairs_scanned': self.pairs_scanned,
            'very_high_probability_setups': self.very_high_probability_setups,
            'opportunities': [opp.to_dict() for opp in self.opportunities],
            'scan_duration_seconds': self.scan_duration_seconds,
            'market_state': self.market_state,
            'score_distribution': self.score_distribution
        }

# ============================================================================
# REAL-TIME PRICE MANAGER FOR 10 CURRENCIES
# ============================================================================

class DerivWebSocketManager:
    """Smart WebSocket manager for Deriv that never hits limits"""
    
    def __init__(self, deriv_token: str):
        self.token = deriv_token
        self.ws = None
        self.connected = False
        self.prices = {}  # Real-time prices from WebSocket for 10 currencies
        self.price_history = {}  # Local price history storage for ALL 90 pairs
        self.message_count = 0
        self.last_message_time = time.time()
        self.start_time = time.time()
        self.running = False
        self.ws_thread = None
        
        # Deriv symbol mapping - 10 CURRENCIES ONLY
        self.symbol_map = {
            'EUR/USD': 'frxEURUSD',
            'GBP/USD': 'frxGBPUSD',
            'USD/JPY': 'frxUSDJPY',
            'USD/CHF': 'frxUSDCHF',
            'AUD/USD': 'frxAUDUSD',
            'USD/CAD': 'frxUSDCAD',
            'NZD/USD': 'frxNZDUSD',
            'XAU/USD': 'WLDAUD',      # Gold proxy
            'BTC/USD': 'cryBTCUSD',   # Bitcoin
            'ETH/USD': 'OTC_DJI'      # ETH proxy (DJI)
        }
        
        # Reverse mapping
        self.reverse_symbol_map = {v: k for k, v in self.symbol_map.items()}
        
        # Initialize price history storage for ALL 90 pairs
        self.initialize_all_pairs_history()
        
    def initialize_all_pairs_history(self):
        """Initialize price history for ALL 90 possible pairs"""
        for pair in ALL_PAIRS:
            self.price_history[pair] = deque(maxlen=config.PRICE_HISTORY_LENGTH)
        
        logger.info(f"Initialized price history for {len(self.price_history)} pairs")
    
    def connect(self):
        """Establish WebSocket connection for 10 currencies"""
        try:
            self.running = True
            self.ws_thread = threading.Thread(target=self._run_websocket, daemon=True)
            self.ws_thread.start()
            
            # Wait for connection
            for _ in range(10):
                if self.connected:
                    break
                time.sleep(0.5)
            
            return self.connected
            
        except Exception as e:
            logger.error(f"Failed to connect to Deriv WebSocket: {e}")
            return False
    
    def _run_websocket(self):
        """Run WebSocket in a thread"""
        asyncio.run(self._async_websocket_handler())
    
    async def _async_websocket_handler(self):
        """Async WebSocket handler"""
        try:
            uri = "wss://ws.derivws.com/websockets/v3?app_id=1089"
            
            async with websockets.connect(uri) as websocket:
                self.ws = websocket
                
                # Authorize
                auth_msg = {"authorize": self.token}
                await websocket.send(json.dumps(auth_msg))
                auth_response = await websocket.recv()
                auth_data = json.loads(auth_response)
                
                if "error" in auth_data:
                    logger.error(f"Deriv WebSocket auth error: {auth_data['error']}")
                    self.connected = False
                    return
                
                logger.info("✅ Deriv WebSocket authorized - Subscribing to 10 currencies")
                self.connected = True
                
                # Subscribe to all 10 symbols
                symbols = list(self.symbol_map.values())
                
                # Subscribe in batches
                for i in range(0, len(symbols), config.WS_BATCH_SIZE):
                    batch = symbols[i:i + config.WS_BATCH_SIZE]
                    
                    for symbol in batch:
                        try:
                            subscribe_msg = {"ticks": symbol, "subscribe": 1}
                            await websocket.send(json.dumps(subscribe_msg))
                            logger.debug(f"Subscribed to {symbol}")
                            await asyncio.sleep(config.WS_PAIR_DELAY)
                        except:
                            pass
                    
                    # Wait between batches
                    if i + config.WS_BATCH_SIZE < len(symbols):
                        await asyncio.sleep(config.WS_BATCH_DELAY)
                
                # Start receiving messages
                while self.running and self.connected:
                    try:
                        response = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                        self.message_count += 1
                        self.last_message_time = time.time()
                        
                        data = json.loads(response)
                        
                        if "tick" in data:
                            tick = data["tick"]
                            symbol = tick.get("symbol")
                            quote = tick.get("bid") or tick.get("quote") or 0
                            
                            if symbol and quote:
                                # Convert Deriv symbol to standard pair name
                                standard_pair = self.reverse_symbol_map.get(symbol)
                                if standard_pair:
                                    price = float(quote)
                                    self.prices[standard_pair] = price
                                    self.price_history[standard_pair].append(price)
                        
                    except asyncio.TimeoutError:
                        continue
                    except Exception as e:
                        logger.debug(f"WebSocket message error: {e}")
                        continue
                        
        except Exception as e:
            logger.error(f"WebSocket connection error: {e}")
            self.connected = False
        finally:
            self.connected = False
    
    def get_usd_rate(self, currency: str) -> Optional[float]:
        """Get currency rate vs USD from current prices"""
        if currency == 'USD':
            return 1.0
        
        # Try direct pair
        direct_pair = f"{currency}/USD"
        if direct_pair in self.prices:
            return self.prices[direct_pair]
        
        # Try inverse pair
        inverse_pair = f"USD/{currency}"
        if inverse_pair in self.prices:
            price = self.prices[inverse_pair]
            return 1 / price if price != 0 else None
        
        return None
    
    def calculate_all_cross_prices(self) -> Dict[str, float]:
        """Calculate prices for ALL 90 pairs from USD rates"""
        calculated_prices = {}
        
        # Get USD rates for all currencies
        usd_rates = {}
        for currency in config.BASE_CURRENCIES:
            if currency == 'USD':
                usd_rates['USD'] = 1.0
            else:
                rate = self.get_usd_rate(currency)
                if rate:
                    usd_rates[currency] = rate
        
        # Calculate all 90 pairs
        for pair in ALL_PAIRS:
            base, quote = pair.split('/')
            
            # Skip if we already have it directly from WS
            if pair in self.prices:
                calculated_prices[pair] = self.prices[pair]
                continue
            
            # Calculate cross: base/quote = (base/USD) / (quote/USD)
            if base in usd_rates and quote in usd_rates:
                base_usd = usd_rates[base]
                quote_usd = usd_rates[quote]
                
                if base_usd and quote_usd and quote_usd != 0:
                    cross_price = base_usd / quote_usd
                    calculated_prices[pair] = cross_price
        
        return calculated_prices
    
    def get_all_prices(self) -> Dict[str, float]:
        """Get prices for ALL 90 pairs"""
        # Start with direct WebSocket prices
        all_prices = self.prices.copy()
        
        # Add calculated cross prices
        calculated = self.calculate_all_cross_prices()
        all_prices.update(calculated)
        
        # Update price history for ALL pairs
        for pair, price in all_prices.items():
            if pair in self.price_history and price > 0:
                self.price_history[pair].append(price)
        
        return all_prices
    
    def get_price_history(self, pair: str, period: int = 100) -> List[float]:
        """Get price history for any of the 90 pairs"""
        if pair in self.price_history:
            history = list(self.price_history[pair])
            return history[-period:] if len(history) > period else history
        return []
    
    def has_sufficient_history(self, pair: str, min_periods: int = 100) -> bool:
        """Check if we have sufficient price history for analysis"""
        if pair in self.price_history:
            return len(self.price_history[pair]) >= min_periods
        return False
    
    def reconnect(self):
        """Reconnect WebSocket"""
        logger.info("Reconnecting Deriv WebSocket...")
        self.running = False
        if self.ws_thread:
            self.ws_thread.join(timeout=2)
        time.sleep(2)
        self.connect()
    
    def monitor_and_maintain(self):
        """Monitor WebSocket health and maintain connection"""
        while True:
            try:
                # Check if we should restart (every 5 minutes)
                if time.time() - self.start_time > config.WS_RECONNECT_INTERVAL:
                    logger.info("Restarting WebSocket for maintenance")
                    self.reconnect()
                
                # Check message rate
                if self.last_message_time and time.time() - self.last_message_time > 30:
                    logger.warning("No messages for 30 seconds, reconnecting...")
                    self.reconnect()
                
                time.sleep(10)
                
            except Exception as e:
                logger.error(f"WebSocket monitor error: {e}")
                time.sleep(30)

# ============================================================================
# HISTORICAL DATA MANAGER (TWELVEDATA)
# ============================================================================

class HistoricalDataManager:
    """Manage historical data for ALL 90 pairs from 10 currencies"""
    
    def __init__(self, twelve_data_key: str, ws_manager: DerivWebSocketManager):
        self.twelve_data_key = twelve_data_key
        self.ws_manager = ws_manager
        self.base_url = "https://api.twelvedata.com"
        
        # Historical data for 10 USD pairs
        self.historical_data = {}
        
        # Last fetch date
        self.last_fetch_date = None
        
    def fetch_initial_historical_data(self):
        """Fetch historical data for 10 USD pairs (once per day)"""
        try:
            today = datetime.now().date()
            
            # Check if we already fetched today
            if self.last_fetch_date == today:
                logger.info("Historical data already fetched today")
                return True
            
            logger.info("Fetching historical data for 10 USD pairs...")
            
            # 10 USD pairs to fetch
            usd_pairs = [
                'EUR/USD', 'GBP/USD', 'USD/JPY', 'USD/CHF',
                'AUD/USD', 'USD/CAD', 'NZD/USD', 'XAU/USD',
                'BTC/USD', 'ETH/USD'
            ]
            
            successful_fetches = 0
            
            for pair in usd_pairs:
                try:
                    symbol = pair.replace('/', '')
                    params = {
                        'symbol': symbol,
                        'interval': '1day',
                        'outputsize': config.HISTORICAL_PERIODS,
                        'apikey': self.twelve_data_key
                    }
                    
                    response = requests.get(
                        f"{self.base_url}/time_series",
                        params=params,
                        timeout=config.REQUEST_TIMEOUT
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        if 'values' in data:
                            df = pd.DataFrame(data['values'])
                            df['datetime'] = pd.to_datetime(df['datetime'])
                            df.set_index('datetime', inplace=True)
                            
                            # Convert numeric columns
                            for col in ['open', 'high', 'low', 'close']:
                                if col in df.columns:
                                    df[col] = pd.to_numeric(df[col], errors='coerce')
                            
                            df = df.dropna()
                            self.historical_data[pair] = df
                            successful_fetches += 1
                            
                            # Initialize WebSocket history with this data
                            if pair in self.ws_manager.price_history:
                                closes = df['close'].values.tolist()
                                for price in closes:
                                    self.ws_manager.price_history[pair].append(price)
                            
                            logger.info(f"Fetched {len(df)} periods for {pair}")
                        else:
                            logger.warning(f"No data for {pair}")
                    else:
                        logger.warning(f"Failed to fetch {pair}: {response.status_code}")
                    
                    # Be nice to the API
                    time.sleep(1)
                    
                except Exception as e:
                    logger.error(f"Error fetching {pair}: {e}")
            
            self.last_fetch_date = today
            logger.info(f"Historical data fetch complete: {successful_fetches}/10 pairs")
            return successful_fetches >= 5  # At least 5 successful
            
        except Exception as e:
            logger.error(f"Historical data fetch failed: {e}")
            return False
    
    def get_historical_prices(self, pair: str) -> Optional[np.ndarray]:
        """Get historical prices for a pair"""
        # If we have direct historical data
        if pair in self.historical_data:
            return self.historical_data[pair]['close'].values
        
        # For cross pairs, we need to calculate from USD rates
        base, quote = pair.split('/')
        
        # Try to get base/USD and quote/USD
        base_usd_pair = f"{base}/USD"
        quote_usd_pair = f"{quote}/USD"
        
        base_prices = None
        quote_prices = None
        
        if base_usd_pair in self.historical_data:
            base_prices = self.historical_data[base_usd_pair]['close'].values
        
        if quote_usd_pair in self.historical_data:
            quote_prices = self.historical_data[quote_usd_pair]['close'].values
        
        # Also check inverse pairs
        if base_prices is None and base != 'USD':
            usd_base_pair = f"USD/{base}"
            if usd_base_pair in self.historical_data:
                base_prices = 1 / self.historical_data[usd_base_pair]['close'].values
        
        if quote_prices is None and quote != 'USD':
            usd_quote_pair = f"USD/{quote}"
            if usd_quote_pair in self.historical_data:
                quote_prices = 1 / self.historical_data[usd_quote_pair]['close'].values
        
        # If we have both, calculate cross prices
        if base_prices is not None and quote_prices is not None:
            # Align lengths
            min_len = min(len(base_prices), len(quote_prices))
            cross_prices = base_prices[:min_len] / quote_prices[:min_len]
            return cross_prices
        
        return None

# ============================================================================
# PROFESSIONAL TECHNICAL ANALYZER (80 POINTS)
# ============================================================================

class ProfessionalTechnicalAnalyzer:
    """Complete technical analysis for ALL 90 pairs (80 points total)"""
    
    def __init__(self, ws_manager: DerivWebSocketManager, historical_manager: HistoricalDataManager):
        self.ws_manager = ws_manager
        self.historical_manager = historical_manager
        
    def analyze_pair(self, pair: str) -> Dict:
        """Complete technical analysis for a pair - returns 0-80 score with breakdown"""
        try:
            cache_key = f"technical_{pair}_{datetime.now().hour}"
            cached = cache.get(cache_key)
            if cached:
                return cached
            
            # Get price history
            price_history = self.ws_manager.get_price_history(pair, 200)
            if len(price_history) < 100:
                # Try to get from historical data
                historical = self.historical_manager.get_historical_prices(pair)
                if historical is not None and len(historical) > 0:
                    price_history = historical.tolist()[-200:]
            
            if len(price_history) < 50:
                return self.get_fallback_analysis(pair)
            
            prices = np.array(price_history)
            
            # PART A: MARKET STRUCTURE (30 points)
            structure_score, structure_details = self.analyze_market_structure(prices)
            
            # PART B: MOMENTUM (25 points)
            momentum_score, momentum_details = self.analyze_momentum(prices)
            
            # PART C: KEY LEVELS (15 points)
            levels_score, levels_details = self.analyze_key_levels(pair, prices)
            
            # PART D: VOLATILITY/ENTRY (10 points)
            volatility_score, volatility_details = self.analyze_volatility_entry(pair, prices)
            
            # Total technical score (0-80)
            total_score = structure_score + momentum_score + levels_score + volatility_score
            
            # Determine direction
            direction = self.determine_direction(prices, structure_details)
            
            # Find optimal entry
            current_price = prices[-1] if len(prices) > 0 else 0
            optimal_entry = self.find_optimal_entry(pair, current_price, direction, structure_details, levels_details)
            
            # Create summary
            summary = self.create_technical_summary(total_score, structure_details, momentum_details, levels_details)
            
            result = {
                'total_score': total_score,
                'structure_score': structure_score,
                'momentum_score': momentum_score,
                'levels_score': levels_score,
                'volatility_score': volatility_score,
                'structure_details': structure_details,
                'momentum_details': momentum_details,
                'levels_details': levels_details,
                'volatility_details': volatility_details,
                'direction': direction,
                'current_price': float(current_price),
                'optimal_entry': optimal_entry,
                'atr': volatility_details.get('atr', 0.001),
                'summary': summary,
                'prices_analyzed': len(prices),
                'timestamp': datetime.now().isoformat()
            }
            
            cache.set(cache_key, result)
            return result
            
        except Exception as e:
            logger.error(f"Technical analysis error for {pair}: {e}")
            return self.get_fallback_analysis(pair)
    
    def analyze_market_structure(self, prices: np.ndarray) -> Tuple[int, Dict]:
        """Analyze market structure - 30 points total"""
        if len(prices) < 50:
            return 0, {'error': 'Insufficient data'}
        
        score = 0
        details = {}
        
        # 1. Multi-timeframe alignment (15 points)
        m1_trend = self.analyze_trend(prices[-20:])  # Short-term
        m2_trend = self.analyze_trend(prices[-50:])  # Medium-term
        m3_trend = self.analyze_trend(prices[-100:]) # Long-term
        
        trends = [m1_trend['direction'], m2_trend['direction'], m3_trend['direction']]
        
        if all(t == 'UPTREND' for t in trends):
            details['mtf_alignment'] = 'Perfect bullish alignment'
            score += 15
        elif all(t == 'DOWNTREND' for t in trends):
            details['mtf_alignment'] = 'Perfect bearish alignment'
            score += 15
        elif trends.count('UPTREND') >= 2:
            details['mtf_alignment'] = 'Majority bullish'
            score += 10
        elif trends.count('DOWNTREND') >= 2:
            details['mtf_alignment'] = 'Majority bearish'
            score += 10
        else:
            details['mtf_alignment'] = 'Mixed/no alignment'
            score += 5
        
        # 2. Clean structure (10 points)
        structure = self.analyze_price_structure(prices)
        details['structure'] = structure
        
        if structure['quality'] == 'EXCELLENT':
            score += 10
        elif structure['quality'] == 'GOOD':
            score += 7
        elif structure['quality'] == 'FAIR':
            score += 4
        else:
            score += 1
        
        # 3. MA alignment (5 points)
        ma_alignment = self.analyze_ma_alignment(prices)
        details['ma_alignment'] = ma_alignment
        
        if ma_alignment['aligned']:
            score += 5
        elif ma_alignment['partially_aligned']:
            score += 3
        else:
            score += 1
        
        details['total_structure_score'] = score
        return score, details
    
    def analyze_momentum(self, prices: np.ndarray) -> Tuple[int, Dict]:
        """Analyze momentum - 25 points total"""
        if len(prices) < 14:
            return 0, {'error': 'Insufficient data'}
        
        score = 0
        details = {}
        
        # 1. RSI divergence (8 points)
        rsi_analysis = self.analyze_rsi(prices)
        details['rsi'] = rsi_analysis
        
        if rsi_analysis['divergence'] == 'BULLISH' and rsi_analysis['value'] > 50:
            score += 8
        elif rsi_analysis['divergence'] == 'BEARISH' and rsi_analysis['value'] < 50:
            score += 8
        elif rsi_analysis['value'] >= 30 and rsi_analysis['value'] <= 70:
            score += 5
        elif rsi_analysis['value'] < 30 or rsi_analysis['value'] > 70:
            score += 3
        else:
            score += 1
        
        # 2. MACD alignment (7 points)
        macd_analysis = self.analyze_macd(prices)
        details['macd'] = macd_analysis
        
        if macd_analysis['signal'] == 'STRONG_BULLISH':
            score += 7
        elif macd_analysis['signal'] == 'STRONG_BEARISH':
            score += 7
        elif macd_analysis['signal'] == 'BULLISH':
            score += 5
        elif macd_analysis['signal'] == 'BEARISH':
            score += 5
        elif macd_analysis['signal'] == 'NEUTRAL':
            score += 3
        else:
            score += 1
        
        # 3. ADX strength (5 points)
        adx_analysis = self.analyze_adx(prices)
        details['adx'] = adx_analysis
        
        if adx_analysis['strength'] == 'STRONG_TREND':
            score += 5
        elif adx_analysis['strength'] == 'MODERATE_TREND':
            score += 4
        elif adx_analysis['strength'] == 'WEAK_TREND':
            score += 3
        else:
            score += 2
        
        # 4. Stochastic direction (5 points)
        stoch_analysis = self.analyze_stochastic(prices)
        details['stochastic'] = stoch_analysis
        
        if stoch_analysis['signal'] == 'BULLISH':
            score += 5
        elif stoch_analysis['signal'] == 'BEARISH':
            score += 5
        elif stoch_analysis['signal'] == 'NEUTRAL':
            score += 3
        else:
            score += 2
        
        details['total_momentum_score'] = score
        return score, details
    
    def analyze_key_levels(self, pair: str, prices: np.ndarray) -> Tuple[int, Dict]:
        """Analyze key levels - 15 points total"""
        if len(prices) < 20:
            return 0, {'error': 'Insufficient data'}
        
        score = 0
        details = {}
        current_price = prices[-1]
        
        # 1. Fibonacci confluence (6 points)
        fib_levels = self.calculate_fibonacci_levels(prices)
        details['fibonacci'] = fib_levels
        
        confluence_count = 0
        for level in fib_levels['levels']:
            if abs(current_price - level['price']) / current_price < 0.005:  # Within 0.5%
                confluence_count += 1
        
        if confluence_count >= 2:
            score += 6
            details['fib_confluence'] = f'{confluence_count} levels'
        elif confluence_count == 1:
            score += 4
            details['fib_confluence'] = '1 level'
        else:
            score += 2
        
        # 2. Support/Resistance cluster (5 points)
        sr_cluster = self.find_sr_cluster(prices)
        details['sr_cluster'] = sr_cluster
        
        if sr_cluster['has_cluster']:
            distance_to_cluster = min([abs(current_price - level) for level in sr_cluster['cluster_levels']])
            relative_distance = distance_to_cluster / current_price
            
            if relative_distance < 0.01:  # Within 1%
                score += 5
                details['cluster_proximity'] = 'Very close'
            elif relative_distance < 0.02:  # Within 2%
                score += 4
                details['cluster_proximity'] = 'Close'
            elif relative_distance < 0.03:  # Within 3%
                score += 3
                details['cluster_proximity'] = 'Moderate'
            else:
                score += 2
        else:
            score += 1
        
        # 3. Psychological level (4 points)
        psychological = self.check_psychological_level(pair, current_price)
        details['psychological'] = psychological
        
        if psychological['is_psychological']:
            if psychological['proximity'] == 'AT_LEVEL':
                score += 4
            elif psychological['proximity'] == 'NEAR_LEVEL':
                score += 3
            else:
                score += 2
        else:
            score += 1
        
        details['total_levels_score'] = score
        return score, details
    
    def analyze_volatility_entry(self, pair: str, prices: np.ndarray) -> Tuple[int, Dict]:
        """Analyze volatility and entry - 10 points total"""
        if len(prices) < 14:
            return 0, {'error': 'Insufficient data'}
        
        score = 0
        details = {}
        
        # 1. ATR-based optimal entry (6 points)
        atr = self.calculate_atr(prices)
        details['atr'] = atr
        
        current_price = prices[-1]
        recent_low = np.min(prices[-20:])
        recent_high = np.max(prices[-20:])
        
        # Calculate optimal entry zone
        if current_price > np.mean(prices[-50:]):  # In uptrend
            optimal_zone_low = recent_low + (atr * 0.3)
            optimal_zone_high = recent_low + (atr * 0.7)
        else:  # In downtrend
            optimal_zone_low = recent_high - (atr * 0.7)
            optimal_zone_high = recent_high - (atr * 0.3)
        
        if optimal_zone_low <= current_price <= optimal_zone_high:
            details['entry_zone'] = 'In optimal zone'
            score += 6
        elif abs(current_price - optimal_zone_low) <= atr * 0.5:
            details['entry_zone'] = 'Near optimal zone'
            score += 4
        else:
            details['entry_zone'] = 'Outside optimal zone'
            score += 2
        
        # 2. Volume confirmation (4 points) - using volatility as proxy
        volatility = np.std(prices[-20:]) / np.mean(prices[-20:]) if np.mean(prices[-20:]) > 0 else 0
        
        if volatility > 0.02:  # High volatility
            details['volatility'] = 'High - good for trending'
            score += 4
        elif volatility > 0.01:  # Medium volatility
            details['volatility'] = 'Medium - normal conditions'
            score += 3
        else:  # Low volatility
            details['volatility'] = 'Low - ranging market'
            score += 2
        
        details['total_volatility_score'] = score
        return score, details
    
    # ========== HELPER FUNCTIONS ==========
    
    def analyze_trend(self, prices: np.ndarray) -> Dict:
        """Analyze trend direction and strength"""
        if len(prices) < 10:
            return {'direction': 'SIDEWAYS', 'strength': 0}
        
        # Simple linear regression
        x = np.arange(len(prices))
        slope, intercept = np.polyfit(x, prices, 1)
        
        # Calculate R-squared
        y_pred = slope * x + intercept
        ss_res = np.sum((prices - y_pred) ** 2)
        ss_tot = np.sum((prices - np.mean(prices)) ** 2)
        r_squared = 1 - (ss_res / ss_tot) if ss_tot != 0 else 0
        
        if slope > 0 and r_squared > 0.3:
            return {'direction': 'UPTREND', 'strength': r_squared}
        elif slope < 0 and r_squared > 0.3:
            return {'direction': 'DOWNTREND', 'strength': r_squared}
        else:
            return {'direction': 'SIDEWAYS', 'strength': r_squared}
    
    def analyze_price_structure(self, prices: np.ndarray) -> Dict:
        """Analyze price structure (HH/HL, LH/LL)"""
        if len(prices) < 20:
            return {'quality': 'POOR', 'structure': 'INSUFFICIENT_DATA'}
        
        # Find swing highs and lows
        highs = []
        lows = []
        
        for i in range(5, len(prices) - 5):
            if prices[i] == max(prices[i-5:i+6]):
                highs.append((i, prices[i]))
            if prices[i] == min(prices[i-5:i+6]):
                lows.append((i, prices[i]))
        
        if len(highs) < 3 or len(lows) < 3:
            return {'quality': 'FAIR', 'structure': 'INSUFFICIENT_SWINGS'}
        
        # Check for HH/HL (uptrend) or LH/LL (downtrend)
        recent_highs = [h[1] for h in highs[-3:]]
        recent_lows = [l[1] for l in lows[-3:]]
        
        is_hh = all(recent_highs[i] > recent_highs[i-1] for i in range(1, len(recent_highs)))
        is_hl = all(recent_lows[i] > recent_lows[i-1] for i in range(1, len(recent_lows)))
        is_lh = all(recent_highs[i] < recent_highs[i-1] for i in range(1, len(recent_highs)))
        is_ll = all(recent_lows[i] < recent_lows[i-1] for i in range(1, len(recent_lows)))
        
        if is_hh and is_hl:
            return {'quality': 'EXCELLENT', 'structure': 'HH_HL_UPTREND'}
        elif is_lh and is_ll:
            return {'quality': 'EXCELLENT', 'structure': 'LH_LL_DOWNTREND'}
        elif is_hh or is_hl:
            return {'quality': 'GOOD', 'structure': 'PARTIAL_UPTREND'}
        elif is_lh or is_ll:
            return {'quality': 'GOOD', 'structure': 'PARTIAL_DOWNTREND'}
        else:
            return {'quality': 'FAIR', 'structure': 'RANGING'}
    
    def analyze_ma_alignment(self, prices: np.ndarray) -> Dict:
        """Check MA alignment"""
        if len(prices) < 50:
            return {'aligned': False, 'partially_aligned': False}
        
        # Calculate MAs
        ma_20 = np.mean(prices[-20:])
        ma_50 = np.mean(prices[-50:])
        
        # Simple alignment check
        current_price = prices[-1]
        
        if current_price > ma_20 > ma_50:
            return {'aligned': True, 'partially_aligned': True, 'alignment': 'BULLISH'}
        elif current_price < ma_20 < ma_50:
            return {'aligned': True, 'partially_aligned': True, 'alignment': 'BEARISH'}
        elif (current_price > ma_20 and ma_20 > ma_50) or (current_price < ma_20 and ma_20 < ma_50):
            return {'aligned': False, 'partially_aligned': True, 'alignment': 'PARTIAL'}
        else:
            return {'aligned': False, 'partially_aligned': False, 'alignment': 'MIXED'}
    
    def analyze_rsi(self, prices: np.ndarray, period: int = 14) -> Dict:
        """Calculate RSI and look for divergence"""
        if len(prices) < period + 1:
            return {'value': 50, 'divergence': 'NONE'}
        
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gain = pd.Series(gains).rolling(period).mean().iloc[-1]
        avg_loss = pd.Series(losses).rolling(period).mean().iloc[-1]
        
        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
        
        rsi = float(rsi) if not pd.isna(rsi) else 50.0
        
        # Simple divergence detection
        divergence = 'NONE'
        if len(prices) > 30:
            # Check for bullish divergence (price makes lower low, RSI makes higher low)
            price_low_1 = min(prices[-30:-15])
            price_low_2 = min(prices[-15:])
            rsi_1 = 50  # Placeholder - would need actual RSI values
            rsi_2 = 50
            
            if price_low_2 < price_low_1 and rsi_2 > rsi_1:
                divergence = 'BULLISH'
            elif price_low_2 > price_low_1 and rsi_2 < rsi_1:
                divergence = 'BEARISH'
        
        return {'value': rsi, 'divergence': divergence}
    
    def analyze_macd(self, prices: np.ndarray) -> Dict:
        """Calculate MACD"""
        if len(prices) < 26:
            return {'signal': 'NEUTRAL', 'histogram': 0}
        
        # Simplified MACD calculation
        ema_12 = pd.Series(prices).ewm(span=12, adjust=False).mean().iloc[-1]
        ema_26 = pd.Series(prices).ewm(span=26, adjust=False).mean().iloc[-1]
        
        macd_line = ema_12 - ema_26
        signal_line = pd.Series([macd_line]).ewm(span=9, adjust=False).mean().iloc[-1]
        histogram = macd_line - signal_line
        
        # Determine signal
        if macd_line > signal_line and histogram > 0:
            signal = 'STRONG_BULLISH' if histogram > abs(macd_line * 0.1) else 'BULLISH'
        elif macd_line < signal_line and histogram < 0:
            signal = 'STRONG_BEARISH' if abs(histogram) > abs(macd_line * 0.1) else 'BEARISH'
        else:
            signal = 'NEUTRAL'
        
        return {'signal': signal, 'histogram': float(histogram), 'macd_line': float(macd_line)}
    
    def analyze_adx(self, prices: np.ndarray, period: int = 14) -> Dict:
        """Calculate ADX (simplified)"""
        if len(prices) < period * 2:
            return {'value': 0, 'strength': 'NO_TREND'}
        
        # Simplified ADX calculation
        high_low = np.max(prices[-period:]) - np.min(prices[-period:])
        avg_range = np.mean([abs(prices[i] - prices[i-1]) for i in range(-period, 0)])
        
        if avg_range == 0:
            dx = 0
        else:
            dx = (high_low / avg_range) * 100
        
        # Classify strength
        if dx > 25:
            strength = 'STRONG_TREND'
        elif dx > 20:
            strength = 'MODERATE_TREND'
        elif dx > 15:
            strength = 'WEAK_TREND'
        else:
            strength = 'NO_TREND'
        
        return {'value': float(dx), 'strength': strength}
    
    def analyze_stochastic(self, prices: np.ndarray, k_period: int = 14, d_period: int = 3) -> Dict:
        """Calculate Stochastic oscillator"""
        if len(prices) < k_period:
            return {'signal': 'NEUTRAL', 'k': 50, 'd': 50}
        
        # Simplified Stochastic
        recent_low = min(prices[-k_period:])
        recent_high = max(prices[-k_period:])
        
        if recent_high - recent_low == 0:
            k = 50
        else:
            k = ((prices[-1] - recent_low) / (recent_high - recent_low)) * 100
        
        # Simple D line (smoothed K)
        if len(prices) >= k_period + d_period:
            d = np.mean([k] * d_period)  # Simplified
        else:
            d = k
        
        # Determine signal
        if k > 80 and d > 80:
            signal = 'BEARISH' if k < d else 'NEUTRAL'
        elif k < 20 and d < 20:
            signal = 'BULLISH' if k > d else 'NEUTRAL'
        elif k > d:
            signal = 'BULLISH'
        elif k < d:
            signal = 'BEARISH'
        else:
            signal = 'NEUTRAL'
        
        return {'signal': signal, 'k': float(k), 'd': float(d)}
    
    def calculate_fibonacci_levels(self, prices: np.ndarray) -> Dict:
        """Calculate Fibonacci retracement levels"""
        if len(prices) < 20:
            return {'levels': []}
        
        # Find swing high and low
        swing_high = max(prices[-20:])
        swing_low = min(prices[-20:])
        
        fib_levels = [0.236, 0.382, 0.5, 0.618, 0.786]
        levels = []
        
        diff = swing_high - swing_low
        
        for level in fib_levels:
            price_level = swing_high - (diff * level)
            levels.append({
                'level': level,
                'price': float(price_level),
                'type': 'RETRACEMENT'
            })
        
        # Extension levels
        ext_levels = [1.272, 1.414, 1.618]
        for level in ext_levels:
            price_level = swing_high + (diff * (level - 1))
            levels.append({
                'level': level,
                'price': float(price_level),
                'type': 'EXTENSION'
            })
        
        return {'levels': levels, 'swing_high': float(swing_high), 'swing_low': float(swing_low)}
    
    def find_sr_cluster(self, prices: np.ndarray, lookback: int = 50) -> Dict:
        """Find support/resistance clusters"""
        if len(prices) < lookback:
            return {'has_cluster': False, 'cluster_levels': []}
        
        # Find price levels where price reversed
        reversal_levels = []
        
        for i in range(10, len(prices) - 10):
            # Check for local highs (resistance)
            if prices[i] == max(prices[i-10:i+11]):
                # Price reversed down from this level
                if prices[i+1] < prices[i] and prices[i+2] < prices[i]:
                    reversal_levels.append(prices[i])
            
            # Check for local lows (support)
            if prices[i] == min(prices[i-10:i+11]):
                # Price reversed up from this level
                if prices[i+1] > prices[i] and prices[i+2] > prices[i]:
                    reversal_levels.append(prices[i])
        
        # Find clusters (levels close to each other)
        clusters = []
        if reversal_levels:
            reversal_levels = sorted(reversal_levels)
            
            current_cluster = [reversal_levels[0]]
            for level in reversal_levels[1:]:
                if abs(level - current_cluster[-1]) / current_cluster[-1] < 0.005:  # Within 0.5%
                    current_cluster.append(level)
                else:
                    if len(current_cluster) >= 2:
                        clusters.append(np.mean(current_cluster))
                    current_cluster = [level]
            
            if len(current_cluster) >= 2:
                clusters.append(np.mean(current_cluster))
        
        return {
            'has_cluster': len(clusters) > 0,
            'cluster_levels': [float(c) for c in clusters],
            'total_reversals': len(reversal_levels)
        }
    
    def check_psychological_level(self, pair: str, price: float) -> Dict:
        """Check if price is at/near psychological level"""
        # Determine appropriate rounding based on pair
        if 'JPY' in pair:
            round_to = 0.5  # 50 pip levels for JPY pairs
        else:
            round_to = 0.005  # 50 pip levels for other pairs
        
        # Find nearest psychological level
        rounded = round(price / round_to) * round_to
        
        distance = abs(price - rounded)
        relative_distance = distance / price if price > 0 else 1
        
        if relative_distance < 0.0005:  # Within 5 pips
            proximity = 'AT_LEVEL'
        elif relative_distance < 0.001:  # Within 10 pips
            proximity = 'NEAR_LEVEL'
        else:
            proximity = 'FAR_FROM_LEVEL'
        
        return {
            'is_psychological': True,
            'level': float(rounded),
            'distance_pips': distance * (100 if 'JPY' in pair else 10000),
            'proximity': proximity
        }
    
    def calculate_atr(self, prices: np.ndarray, period: int = 14) -> float:
        """Calculate Average True Range"""
        if len(prices) < period + 1:
            return 0.001
        
        tr_values = []
        for i in range(1, len(prices)):
            high = prices[i]
            low = prices[i]
            prev_close = prices[i-1]
            
            hl = high - low
            hc = abs(high - prev_close)
            lc = abs(low - prev_close)
            tr = max(hl, hc, lc)
            tr_values.append(tr)
        
        atr = np.mean(tr_values[-period:]) if tr_values else 0.001
        return float(atr)
    
    def determine_direction(self, prices: np.ndarray, structure_details: Dict) -> str:
        """Determine trade direction based on analysis"""
        # Use structure alignment first
        if 'mtf_alignment' in structure_details:
            if 'bullish' in structure_details['mtf_alignment'].lower():
                return 'BUY'
            elif 'bearish' in structure_details['mtf_alignment'].lower():
                return 'SELL'
        
        # Fallback to price trend
        if len(prices) >= 10:
            recent_trend = self.analyze_trend(prices[-10:])
            if recent_trend['direction'] == 'UPTREND':
                return 'BUY'
            elif recent_trend['direction'] == 'DOWNTREND':
                return 'SELL'
        
        # Final fallback
        return 'BUY'
    
    def find_optimal_entry(self, pair: str, current_price: float, direction: str, 
                          structure_details: Dict, levels_details: Dict) -> float:
        """Find optimal entry price"""
        if direction == 'BUY':
            # Look for support levels
            support_levels = []
            
            # Check Fibonacci levels
            if 'fibonacci' in levels_details:
                for level in levels_details['fibonacci'].get('levels', []):
                    if level['price'] < current_price * 0.99:  # Below current price
                        support_levels.append(level['price'])
            
            # Check S/R clusters
            if 'sr_cluster' in levels_details and levels_details['sr_cluster']['has_cluster']:
                for level in levels_details['sr_cluster']['cluster_levels']:
                    if level < current_price * 0.99:
                        support_levels.append(level)
            
            if support_levels:
                # Use highest support level (closest to current price but below)
                valid_supports = [s for s in support_levels if s < current_price]
                if valid_supports:
                    optimal = max(valid_supports)
                    # Add small buffer
                    optimal = optimal * 1.001
                    return round(optimal, 5 if 'JPY' not in pair else 3)
            
            # Fallback: 0.5% below current price
            return round(current_price * 0.995, 5 if 'JPY' not in pair else 3)
        
        else:  # SELL
            # Look for resistance levels
            resistance_levels = []
            
            # Check Fibonacci levels
            if 'fibonacci' in levels_details:
                for level in levels_details['fibonacci'].get('levels', []):
                    if level['price'] > current_price * 1.01:  # Above current price
                        resistance_levels.append(level['price'])
            
            # Check S/R clusters
            if 'sr_cluster' in levels_details and levels_details['sr_cluster']['has_cluster']:
                for level in levels_details['sr_cluster']['cluster_levels']:
                    if level > current_price * 1.01:
                        resistance_levels.append(level)
            
            if resistance_levels:
                # Use lowest resistance level (closest to current price but above)
                valid_resistances = [r for r in resistance_levels if r > current_price]
                if valid_resistances:
                    optimal = min(valid_resistances)
                    # Subtract small buffer
                    optimal = optimal * 0.999
                    return round(optimal, 5 if 'JPY' not in pair else 3)
            
            # Fallback: 0.5% above current price
            return round(current_price * 1.005, 5 if 'JPY' not in pair else 3)
    
    def create_technical_summary(self, total_score: int, structure_details: Dict,
                                momentum_details: Dict, levels_details: Dict) -> str:
        """Create technical summary"""
        parts = []
        
        # Structure
        if 'mtf_alignment' in structure_details:
            parts.append(structure_details['mtf_alignment'])
        
        # Momentum highlights
        if 'rsi' in momentum_details and momentum_details['rsi']['divergence'] != 'NONE':
            parts.append(f"RSI {momentum_details['rsi']['divergence'].lower()} divergence")
        
        if 'macd' in momentum_details and momentum_details['macd']['signal'] != 'NEUTRAL':
            parts.append(f"MACD {momentum_details['macd']['signal'].lower()}")
        
        # Key levels
        if 'fib_confluence' in levels_details:
            parts.append(f"Fib confluence: {levels_details['fib_confluence']}")
        
        summary = f"Score: {total_score}/80 - " + ", ".join(parts[:3])
        return summary
    
    def get_fallback_analysis(self, pair: str) -> Dict:
        """Fallback analysis when data is insufficient"""
        return {
            'total_score': 20,
            'structure_score': 5,
            'momentum_score': 5,
            'levels_score': 5,
            'volatility_score': 5,
            'structure_details': {'error': 'Insufficient data'},
            'momentum_details': {'error': 'Insufficient data'},
            'levels_details': {'error': 'Insufficient data'},
            'volatility_details': {'error': 'Insufficient data'},
            'direction': 'BUY',
            'current_price': 1.0,
            'optimal_entry': 1.0,
            'atr': 0.001,
            'summary': 'Insufficient data for analysis',
            'prices_analyzed': 0,
            'timestamp': datetime.now().isoformat()
        }

# ============================================================================
# SENTIMENT ANALYZER (20 POINTS) - SAME AS BEFORE
# ============================================================================

class SentimentAnalyzer:
    """Complete sentiment analysis (20 points total)"""
    
    def __init__(self):
        self.cftc_url = "https://www.cftc.gov/dea/newcot/FinFutWk.txt"
        self.last_fetch = None
        self.cot_data = None
        
    def analyze_pair(self, pair: str) -> Dict:
        """Analyze sentiment for a currency pair - returns 0-20 score"""
        try:
            cache_key = f"sentiment_{pair}_{datetime.now().date()}"
            cached = cache.get(cache_key)
            if cached:
                return cached
            
            # Get institutional sentiment (CFTC)
            institutional = self._get_cftc_sentiment(pair)
            
            # Calculate sentiment score (0-20)
            score = self._calculate_sentiment_score(institutional)
            
            result = {
                'score': score,
                'institutional': institutional,
                'summary': self._create_sentiment_summary(institutional, score),
                'timestamp': datetime.now().isoformat()
            }
            
            cache.set(cache_key, result)
            return result
            
        except Exception as e:
            logger.error(f"Sentiment analysis error for {pair}: {e}")
            return self._get_fallback_sentiment()
    
    def _get_cftc_sentiment(self, pair: str) -> Dict:
        """Get CFTC COT data for pair"""
        try:
            # Map pairs to CFTC symbols
            cot_mapping = {
                'EUR/USD': 'EURO FX',
                'GBP/USD': 'BRITISH POUND',
                'USD/JPY': 'JAPANESE YEN',
                'USD/CHF': 'SWISS FRANC',
                'AUD/USD': 'AUSTRALIAN DOLLAR',
                'USD/CAD': 'CANADIAN DOLLAR',
                'NZD/USD': 'NEW ZEALAND DOLLAR',
            }
            
            cot_symbol = cot_mapping.get(pair)
            if not cot_symbol:
                return {'available': False, 'reason': 'Not in CFTC report'}
            
            # Fetch COT data if needed
            if (self.last_fetch is None or 
                (datetime.now() - self.last_fetch).seconds > 3600):
                self._fetch_cot_data()
            
            if self.cot_data and cot_symbol in self.cot_data:
                data = self.cot_data[cot_symbol]
                
                # Calculate positioning
                net = data['long'] - data['short']
                total = data['long'] + data['short']
                net_percentage = (net / total * 100) if total > 0 else 0
                
                return {
                    'available': True,
                    'long': data['long'],
                    'short': data['short'],
                    'net': net,
                    'net_percentage': net_percentage,
                    'extreme': abs(net_percentage) > 70,
                    'bias': 'LONG' if net > 0 else 'SHORT',
                    'source': 'CFTC'
                }
            
            return {'available': False, 'reason': 'Data not found'}
            
        except Exception as e:
            logger.error(f"CFTC error: {e}")
            return {'available': False, 'reason': str(e)}
    
    def _fetch_cot_data(self):
        """Fetch and parse COT data"""
        try:
            response = requests.get(self.cftc_url, timeout=15)
            if response.status_code == 200:
                self.cot_data = self._parse_cot_data(response.text)
                self.last_fetch = datetime.now()
                logger.info("CFTC data fetched successfully")
        except Exception as e:
            logger.error(f"Failed to fetch COT data: {e}")
    
    def _parse_cot_data(self, text: str) -> Dict:
        """Parse COT data text"""
        data = {}
        lines = text.strip().split('\n')
        
        for line in lines:
            if 'EURO FX' in line:
                data['EURO FX'] = self._parse_cot_line(line)
            elif 'BRITISH POUND' in line:
                data['BRITISH POUND'] = self._parse_cot_line(line)
            elif 'JAPANESE YEN' in line:
                data['JAPANESE YEN'] = self._parse_cot_line(line)
            elif 'SWISS FRANC' in line:
                data['SWISS FRANC'] = self._parse_cot_line(line)
            elif 'AUSTRALIAN DOLLAR' in line:
                data['AUSTRALIAN DOLLAR'] = self._parse_cot_line(line)
            elif 'CANADIAN DOLLAR' in line:
                data['CANADIAN DOLLAR'] = self._parse_cot_line(line)
        
        return data
    
    def _parse_cot_line(self, line: str) -> Dict:
        """Parse a single COT line"""
        try:
            parts = line.split(',')
            if len(parts) > 10:
                return {
                    'long': int(parts[7].strip() or 0),
                    'short': int(parts[8].strip() or 0)
                }
        except:
            pass
        return {'long': 0, 'short': 0}
    
    def _calculate_sentiment_score(self, institutional: Dict) -> int:
        """Calculate sentiment score 0-20"""
        score = 10  # Neutral baseline
        
        if institutional.get('available'):
            net_pct = institutional.get('net_percentage', 0)
            extreme = institutional.get('extreme', False)
            
            if not extreme:
                # Not extreme = better for trading (less crowded)
                if 40 <= abs(net_pct) <= 60:
                    score += 5  # Optimal range
                elif 30 <= abs(net_pct) < 40 or 60 < abs(net_pct) <= 70:
                    score += 3  # Good range
                else:
                    score += 1  # Neutral range
            else:
                # Extreme = contrarian opportunity but risky
                score += 2  # Small positive for contrarian setups
        
        return max(0, min(score, 20))
    
    def _create_sentiment_summary(self, institutional: Dict, score: int) -> str:
        """Create sentiment summary"""
        if institutional.get('available'):
            bias = institutional.get('bias', 'NEUTRAL')
            net_pct = institutional.get('net_percentage', 0)
            extreme = institutional.get('extreme', False)
            
            if extreme:
                return f"Institutions EXTREME {bias} ({net_pct:.1f}%) - Score: {score}/20"
            else:
                return f"Institutions {bias} ({net_pct:.1f}%) - Score: {score}/20"
        
        return f"Score: {score}/20 - Limited institutional data"
    
    def _get_fallback_sentiment(self) -> Dict:
        """Fallback sentiment when primary sources fail"""
        return {
            'score': 10,
            'institutional': {'available': False, 'reason': 'Fallback'},
            'summary': 'Using fallback sentiment data',
            'timestamp': datetime.now().isoformat()
        }

# ============================================================================
# PROFESSIONAL TP/SL CALCULATOR - SAME AS BEFORE
# ============================================================================

class ProfessionalTP_SL_Calculator:
    """Complete professional TP/SL calculation (same as previous app.py)"""
    
    def calculate_optimal_tp_sl(self, pair: str, entry_price: float, 
                                direction: str, context: str, atr: float,
                                technical_data: Dict) -> Optional[Dict]:
        """Calculate optimal TP and SL with all professional methods"""
        try:
            # 1. Calculate stop loss (where thesis breaks)
            stop_loss = self._calculate_stop_loss(
                entry_price, direction, context, atr, technical_data
            )
            
            if stop_loss is None:
                return None
            
            # 2. Calculate risk in pips
            risk_pips = self._calculate_pips(pair, entry_price, stop_loss, direction)
            
            # 3. Calculate take profit with professional methods
            take_profit = self._calculate_take_profit(
                pair, entry_price, direction, context, atr, risk_pips, technical_data
            )
            
            if take_profit is None:
                return None
            
            # 4. Calculate reward in pips
            reward_pips = self._calculate_pips(pair, entry_price, take_profit, direction)
            
            # 5. Calculate risk/reward ratio
            if risk_pips <= 0:
                return None
            
            risk_reward = reward_pips / risk_pips
            
            # 6. Calculate probability
            probability = self._calculate_probability(
                pair, context, risk_reward, technical_data
            )
            
            # 7. Estimate duration
            duration = self._estimate_duration(
                pair, entry_price, take_profit, direction, context, atr
            )
            
            # 8. Professional validation
            if not self._validate_tp_sl(risk_pips, reward_pips, risk_reward, probability, duration):
                return None
            
            return {
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'risk_pips': risk_pips,
                'reward_pips': reward_pips,
                'risk_reward': risk_reward,
                'probability_tp_before_sl': probability,
                'estimated_duration_days': duration,
                'method': 'PROFESSIONAL_CONFLUENCE'
            }
            
        except Exception as e:
            logger.error(f"TP/SL calculation error for {pair}: {e}")
            return None
    
    def _calculate_stop_loss(self, entry: float, direction: str, context: str,
                            atr: float, technical_data: Dict) -> float:
        """Professional stop loss calculation"""
        # ATR-based with context adjustment
        multipliers = {
            'TRENDING': 1.0,
            'RANGING': 0.7,
            'BREAKOUT_UP': 1.2,
            'BREAKOUT_DOWN': 1.2,
            'UNCLEAR': 1.0
        }
        
        multiplier = multipliers.get(context, 1.0)
        atr_distance = atr * multiplier
        
        if direction == 'BUY':
            stop_loss = entry - atr_distance
            # Ensure minimum distance
            min_distance = atr * 0.5
            if (entry - stop_loss) < min_distance:
                stop_loss = entry - min_distance
        else:  # SELL
            stop_loss = entry + atr_distance
            min_distance = atr * 0.5
            if (stop_loss - entry) < min_distance:
                stop_loss = entry + min_distance
        
        # Adjust to psychological level
        stop_loss = self._adjust_to_level(stop_loss, direction == 'BUY')
        
        return stop_loss
    
    def _calculate_take_profit(self, pair: str, entry: float, direction: str, 
                              context: str, atr: float, risk_pips: float,
                              technical_data: Dict) -> float:
        """Professional take profit calculation"""
        # Minimum risk/reward
        min_rr = config.MIN_RISK_REWARD
        min_target_pips = risk_pips * min_rr
        
        # Convert to price
        pip_value = self._get_pip_value(pair)
        min_target_price = entry + (min_target_pips * pip_value) if direction == 'BUY' else entry - (min_target_pips * pip_value)
        
        # ATR extension for trending markets
        if context == 'TRENDING':
            atr_multiplier = 3.0
            atr_target = atr * atr_multiplier
            atr_target_price = entry + atr_target if direction == 'BUY' else entry - atr_target
            
            # Use the better target
            if direction == 'BUY':
                take_profit = max(min_target_price, atr_target_price)
            else:
                take_profit = min(min_target_price, atr_target_price)
        else:
            take_profit = min_target_price
        
        # Adjust to psychological level
        take_profit = self._adjust_to_level(take_profit, direction == 'BUY')
        
        # Ensure not too ambitious
        max_rr = 5.0
        max_target_pips = risk_pips * max_rr
        max_target_price = entry + (max_target_pips * pip_value) if direction == 'BUY' else entry - (max_target_pips * pip_value)
        
        if direction == 'BUY':
            take_profit = min(take_profit, max_target_price)
        else:
            take_profit = max(take_profit, max_target_price)
        
        return take_profit
    
    def _calculate_pips(self, pair: str, price1: float, price2: float, 
                       direction: str) -> float:
        """Calculate pips between two prices"""
        if direction == 'BUY':
            difference = abs(price1 - price2)
        else:
            difference = abs(price2 - price1)
        
        # Adjust for JPY pairs
        if 'JPY' in pair:
            pips = difference * 100
        else:
            pips = difference * 10000
        
        return max(pips, 0.1)
    
    def _get_pip_value(self, pair: str) -> float:
        """Get pip value for pair"""
        return 0.0001 if 'JPY' not in pair else 0.01
    
    def _calculate_probability(self, pair: str, context: str, rr_ratio: float,
                              technical_data: Dict) -> float:
        """Calculate probability of success"""
        # Base probabilities by context
        base_probs = {
            'TRENDING': 0.65,
            'RANGING': 0.55,
            'BREAKOUT_UP': 0.60,
            'BREAKOUT_DOWN': 0.60,
            'UNCLEAR': 0.50
        }
        
        probability = base_probs.get(context, 0.50)
        
        # Adjust for risk/reward (higher RR = lower win rate)
        rr_adjustments = {
            1.0: 0.0,
            1.5: -0.05,
            2.0: -0.10,
            3.0: -0.15,
            4.0: -0.20,
            5.0: -0.25
        }
        
        # Find closest RR for adjustment
        closest_rr = min(rr_adjustments.keys(), key=lambda x: abs(x - rr_ratio))
        probability += rr_adjustments[closest_rr]
        
        # Ensure reasonable bounds
        return max(0.30, min(probability, 0.85))
    
    def _estimate_duration(self, pair: str, entry: float, tp: float,
                          direction: str, context: str, atr: float) -> int:
        """Estimate trade duration in days"""
        distance = abs(tp - entry)
        
        if atr > 0:
            daily_atr = atr * 6  # Convert 4h ATR to daily
            if daily_atr > 0:
                estimated_days = distance / daily_atr
            else:
                estimated_days = 7
        else:
            estimated_days = 7
        
        # Context adjustments
        adjustments = {
            'TRENDING': 0.8,
            'RANGING': 1.5,
            'BREAKOUT_UP': 0.7,
            'BREAKOUT_DOWN': 0.7,
            'UNCLEAR': 1.2
        }
        
        estimated_days *= adjustments.get(context, 1.0)
        estimated_days = max(1, min(estimated_days, 60))
        
        return int(round(estimated_days))
    
    def _adjust_to_level(self, price: float, is_buy: bool) -> float:
        """Adjust price to psychological level"""
        # For most pairs, round to 0.00005
        if is_buy:
            rounded = round(price * 20000) / 20000
            if rounded < price:
                rounded += 0.00005
        else:
            rounded = round(price * 20000) / 20000
            if rounded > price:
                rounded -= 0.00005
        
        return rounded
    
    def _validate_tp_sl(self, risk_pips: float, reward_pips: float,
                       risk_reward: float, probability: float,
                       duration: int) -> bool:
        """Validate TP/SL meets professional criteria"""
        validations = [
            risk_pips >= 10,
            reward_pips >= 15,
            risk_reward >= config.MIN_RISK_REWARD,
            probability >= config.MIN_SUCCESS_PROBABILITY,
            duration <= config.MAX_TRADE_DURATION_DAYS,
            risk_reward <= 5.0
        ]
        
        return all(validations)

# ============================================================================
# COMPLETE SCANNER ENGINE
# ============================================================================

class ForexTechnicalSentinel:
    """Complete Forex Sentinel with pure technical analysis"""
    
    def __init__(self, config: Config):
        self.config = config
        
        # Initialize core components
        self.ws_manager = DerivWebSocketManager(config.DERIV_TOKEN)
        self.historical_manager = HistoricalDataManager(config.TWELVEDATA_KEY, self.ws_manager)
        self.technical_analyzer = ProfessionalTechnicalAnalyzer(self.ws_manager, self.historical_manager)
        self.sentiment_analyzer = SentimentAnalyzer()
        self.tp_sl_calculator = ProfessionalTP_SL_Calculator()
        
        # Start WebSocket in background
        self.start_websocket()
        
        # Fetch initial historical data
        self.fetch_historical_data()
        
        # Performance tracking
        self.scan_count = 0
        self.opportunities_found = 0
        
        logger.info("Forex Technical Sentinel initialized")
    
    def start_websocket(self):
        """Start WebSocket connection"""
        try:
            connected = self.ws_manager.connect()
            if connected:
                # Start monitor thread
                monitor_thread = threading.Thread(target=self.ws_manager.monitor_and_maintain)
                monitor_thread.daemon = True
                monitor_thread.start()
                logger.info("Deriv WebSocket started successfully")
            else:
                logger.warning("Failed to connect to Deriv WebSocket")
        except Exception as e:
            logger.error(f"Failed to start WebSocket: {e}")
    
    def fetch_historical_data(self):
        """Fetch initial historical data"""
        try:
            success = self.historical_manager.fetch_initial_historical_data()
            if success:
                logger.info("Historical data loaded successfully")
            else:
                logger.warning("Partial or no historical data loaded")
        except Exception as e:
            logger.error(f"Failed to load historical data: {e}")
    
    def run_complete_scan(self) -> ScanResult:
        """Run complete scan of ALL 90 pairs"""
        start_time = datetime.now()
        scan_id = f"tech_scan_{int(start_time.timestamp())}"
        
        logger.info(f"🚀 Starting Technical scan {scan_id} for {len(ALL_PAIRS)} pairs")
        
        all_opportunities = []
        score_distribution = {
            '90-100': 0,
            '80-89': 0,
            '70-79': 0,
            '60-69': 0,
            '50-59': 0,
            '40-49': 0,
            '30-39': 0,
            '20-29': 0,
            '10-19': 0,
            '0-9': 0
        }
        
        pairs_analyzed = 0
        
        try:
            # Get current prices for all pairs
            all_prices = self.ws_manager.get_all_prices()
            
            # Analyze ALL 90 pairs
            for pair in ALL_PAIRS:
                try:
                    # Skip if no price data
                    if pair not in all_prices or all_prices[pair] <= 0:
                        continue
                    
                    # 1. Technical Analysis (80 points)
                    technical = self.technical_analyzer.analyze_pair(pair)
                    
                    # 2. Sentiment Analysis (20 points)
                    sentiment = self.sentiment_analyzer.analyze_pair(pair)
                    
                    # 3. Calculate Total Score (0-100)
                    total_score = technical['total_score'] + sentiment['score']
                    
                    # Track score distribution
                    if total_score >= 90:
                        score_distribution['90-100'] += 1
                    elif total_score >= 80:
                        score_distribution['80-89'] += 1
                    elif total_score >= 70:
                        score_distribution['70-79'] += 1
                    elif total_score >= 60:
                        score_distribution['60-69'] += 1
                    elif total_score >= 50:
                        score_distribution['50-59'] += 1
                    elif total_score >= 40:
                        score_distribution['40-49'] += 1
                    elif total_score >= 30:
                        score_distribution['30-39'] += 1
                    elif total_score >= 20:
                        score_distribution['20-29'] += 1
                    elif total_score >= 10:
                        score_distribution['10-19'] += 1
                    else:
                        score_distribution['0-9'] += 1
                    
                    # THE ONLY FILTER: Score ≥ 70
                    if total_score >= config.MIN_CONFLUENCE_SCORE:
                        # 4. Determine trade direction
                        direction = technical['direction']
                        
                        # 5. Get optimal entry
                        entry_price = technical['optimal_entry']
                        
                        # 6. Calculate professional TP/SL
                        tp_sl = self.tp_sl_calculator.calculate_optimal_tp_sl(
                            pair=pair,
                            entry_price=entry_price,
                            direction=direction,
                            context='TRENDING' if technical['structure_details'].get('mtf_alignment', '').startswith('Perfect') else 'UNCLEAR',
                            atr=technical['atr'],
                            technical_data=technical
                        )
                        
                        if tp_sl is not None:
                            # 7. Create complete opportunity
                            technical_breakdown = {
                                'structure': technical['structure_score'],
                                'momentum': technical['momentum_score'],
                                'levels': technical['levels_score'],
                                'volatility': technical['volatility_score']
                            }
                            
                            opportunity = Opportunity(
                                pair=pair,
                                direction=direction,
                                confluence_score=total_score,
                                catalyst="Pure Technical Analysis",
                                setup_type=technical['summary'],
                                entry_price=entry_price,
                                stop_loss=tp_sl['stop_loss'],
                                take_profit=tp_sl['take_profit'],
                                risk_reward=tp_sl['risk_reward'],
                                risk_pips=tp_sl['risk_pips'],
                                reward_pips=tp_sl['reward_pips'],
                                probability_tp_before_sl=tp_sl['probability_tp_before_sl'],
                                estimated_duration_days=tp_sl['estimated_duration_days'],
                                context='TRENDING' if 'bullish' in technical['structure_details'].get('mtf_alignment', '').lower() or 'bearish' in technical['structure_details'].get('mtf_alignment', '').lower() else 'UNCLEAR',
                                confidence='VERY_HIGH' if total_score >= 85 else 'HIGH',
                                analysis_summary=f"Technical score: {technical['total_score']}/80 + Sentiment: {sentiment['score']}/20 = {total_score}/100",
                                fundamentals_summary="Technical Analysis Only - No News Catalysts",
                                technicals_summary=technical['summary'],
                                sentiment_summary=sentiment['summary'],
                                detected_at=datetime.now().isoformat(),
                                scan_id=scan_id,
                                technical_breakdown=technical_breakdown
                            )
                            
                            all_opportunities.append(opportunity)
                    
                    pairs_analyzed += 1
                    
                    # Log progress every 10 pairs
                    if pairs_analyzed % 10 == 0:
                        logger.info(f"Analyzed {pairs_analyzed}/{len(ALL_PAIRS)} pairs...")
                        
                except Exception as e:
                    logger.error(f"Error analyzing {pair}: {e}")
            
            # Calculate statistics
            scan_duration = (datetime.now() - start_time).total_seconds()
            market_state = self.determine_market_state(len(all_opportunities), score_distribution)
            
            # Update counters
            self.scan_count += 1
            self.opportunities_found += len(all_opportunities)
            
            logger.info(f"✅ Scan {scan_id} completed in {scan_duration:.1f}s")
            logger.info(f"📊 Results: {len(all_opportunities)} pairs with score ≥ 70")
            logger.info(f"📈 Score distribution: {score_distribution}")
            logger.info(f"🌍 Market state: {market_state}")
            
            return ScanResult(
                scan_id=scan_id,
                timestamp=datetime.now().isoformat(),
                pairs_scanned=pairs_analyzed,
                very_high_probability_setups=len(all_opportunities),
                opportunities=all_opportunities,
                scan_duration_seconds=scan_duration,
                market_state=market_state,
                score_distribution=score_distribution
            )
            
        except Exception as e:
            logger.error(f"Scan failed: {e}")
            # Return empty result
            return ScanResult(
                scan_id=scan_id,
                timestamp=datetime.now().isoformat(),
                pairs_scanned=0,
                very_high_probability_setups=0,
                opportunities=[],
                scan_duration_seconds=(datetime.now() - start_time).total_seconds(),
                market_state='ERROR',
                score_distribution=score_distribution
            )
    
    def determine_market_state(self, opportunities_count: int, score_distribution: Dict) -> str:
        """Determine market state based on opportunities found"""
        high_quality_pairs = score_distribution.get('80-89', 0) + score_distribution.get('90-100', 0)
        
        if opportunities_count == 0:
            return 'QUIET'
        elif opportunities_count <= 3:
            return 'CALM'
        elif opportunities_count <= 10:
            return 'NORMAL'
        elif opportunities_count <= 20:
            return 'ACTIVE'
        elif high_quality_pairs >= 10:
            return 'TRENDING'
        else:
            return 'VOLATILE'

# ============================================================================
# WEB INTERFACE
# ============================================================================

from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)

# Initialize sentinel globally
sentinel = ForexTechnicalSentinel(config)

# Complete HTML template
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Forex Technical Sentinel</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #0f172a; color: #f8fafc; }
        .container { max-width: 1400px; margin: 0 auto; padding: 20px; }
        
        .header { background: linear-gradient(135deg, #1e40af 0%, #3b82f6 100%); padding: 30px; border-radius: 12px; margin-bottom: 30px; }
        .header h1 { font-size: 2.5rem; margin-bottom: 10px; }
        .header p { font-size: 1.1rem; opacity: 0.9; }
        
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; margin-bottom: 30px; }
        .stat-card { background: #1e293b; padding: 25px; border-radius: 10px; border-left: 4px solid #3b82f6; }
        .stat-value { font-size: 2rem; font-weight: bold; color: #60a5fa; }
        .stat-label { font-size: 0.9rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 1px; }
        
        .score-distribution { background: #1e293b; padding: 25px; border-radius: 10px; margin-bottom: 30px; }
        .score-bar { height: 20px; background: #334155; border-radius: 10px; margin: 10px 0; overflow: hidden; }
        .score-fill { height: 100%; background: linear-gradient(90deg, #10b981 0%, #3b82f6 100%); border-radius: 10px; }
        
        .scan-info { background: #1e293b; padding: 25px; border-radius: 10px; margin-bottom: 30px; }
        .market-state { display: inline-block; padding: 8px 16px; border-radius: 20px; font-weight: bold; margin-top: 10px; }
        .market-state.quiet { background: #475569; }
        .market-state.calm { background: #3b82f6; }
        .market-state.normal { background: #10b981; }
        .market-state.active { background: #f59e0b; }
        .market-state.volatile { background: #ef4444; }
        .market-state.error { background: #dc2626; }
        
        .opportunity-card { background: #1e293b; padding: 25px; border-radius: 10px; margin-bottom: 20px; border-left: 4px solid; }
        .opportunity-card.high { border-left-color: #10b981; }
        .opportunity-card.very-high { border-left-color: #ef4444; }
        
        .badge { display: inline-block; padding: 6px 12px; border-radius: 6px; font-size: 0.9rem; font-weight: bold; margin-right: 10px; }
        .badge.buy { background: #10b981; color: white; }
        .badge.sell { background: #ef4444; color: white; }
        
        .details-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-top: 15px; }
        .detail-item { background: #334155; padding: 12px; border-radius: 6px; }
        .detail-label { font-size: 0.8rem; color: #94a3b8; margin-bottom: 5px; }
        .detail-value { font-size: 1.1rem; font-weight: bold; }
        
        .score-breakdown { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-top: 15px; }
        .score-item { text-align: center; padding: 10px; background: #334155; border-radius: 6px; }
        .score-category { font-size: 0.8rem; color: #94a3b8; }
        .score-points { font-size: 1.2rem; font-weight: bold; color: #60a5fa; }
        
        .analysis-section { background: #334155; padding: 20px; border-radius: 8px; margin-top: 15px; }
        .analysis-title { color: #60a5fa; margin-bottom: 10px; }
        
        .controls { display: flex; gap: 15px; margin-bottom: 30px; }
        .btn { background: #3b82f6; color: white; padding: 12px 24px; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; }
        .btn:hover { background: #2563eb; }
        .btn-scan { background: #10b981; }
        .btn-scan:hover { background: #059669; }
        
        .footer { text-align: center; margin-top: 40px; padding-top: 20px; border-top: 1px solid #334155; color: #94a3b8; }
        
        .api-status { display: flex; gap: 10px; margin-top: 10px; }
        .api-status-item { padding: 5px 10px; border-radius: 4px; font-size: 0.8rem; }
        .api-status-good { background: #10b981; }
        .api-status-warning { background: #f59e0b; }
        .api-status-error { background: #ef4444; }
        
        @media (max-width: 768px) {
            .container { padding: 10px; }
            .header { padding: 20px; }
            .header h1 { font-size: 1.8rem; }
            .stats-grid { grid-template-columns: 1fr; }
            .score-breakdown { grid-template-columns: repeat(2, 1fr); }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📊 Forex Technical Sentinel</h1>
            <p>Pure Technical Analysis: 80 points technical + 20 points sentiment = 100 total</p>
            <p><small>Analyzing ALL 90 pairs • Showing ALL pairs with score ≥ 70 • NO other filters</small></p>
            
            <div class="api-status">
                <div class="api-status-item api-status-good">✓ Deriv WebSocket</div>
                <div class="api-status-item api-status-good">✓ TwelveData Historical</div>
                <div class="api-status-item api-status-good">✓ CFTC Sentiment</div>
            </div>
        </div>
        
        <div class="controls">
            <button class="btn btn-scan" onclick="runScan()">🔄 Run New Scan</button>
            <button class="btn" onclick="location.reload()">📊 Refresh</button>
        </div>
        
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-value">{{ scan.very_high_probability_setups }}</div>
                <div class="stat-label">Pairs with Score ≥ 70</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{{ scan.pairs_scanned }}/90</div>
                <div class="stat-label">Pairs Analyzed</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{{ "%.1f"|format(scan.scan_duration_seconds) }}s</div>
                <div class="stat-label">Scan Duration</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{{ scan.market_state }}</div>
                <div class="stat-label">Market State</div>
            </div>
        </div>
        
        <div class="score-distribution">
            <h2>📈 Score Distribution ({{ scan.pairs_scanned }} pairs)</h2>
            {% for range, count in scan.score_distribution.items() %}
                <div>
                    <div style="display: flex; justify-content: space-between; margin: 5px 0;">
                        <span>{{ range }}:</span>
                        <span>{{ count }} pairs</span>
                    </div>
                    <div class="score-bar">
                        <div class="score-fill" style="width: {{ (count / scan.pairs_scanned * 100) if scan.pairs_scanned > 0 else 0 }}%;"></div>
                    </div>
                </div>
            {% endfor %}
        </div>
        
        <div class="scan-info">
            <h2>Latest Scan Results</h2>
            <p><strong>Scan ID:</strong> {{ scan.scan_id }}</p>
            <p><strong>Timestamp:</strong> {{ scan.timestamp }}</p>
            <p><strong>Pairs Analyzed:</strong> {{ scan.pairs_scanned }}/90</p>
            <div class="market-state {{ scan.market_state.lower() }}">
                Market State: {{ scan.market_state }}
            </div>
        </div>
        
        {% if scan.very_high_probability_setups == 0 %}
            <div class="opportunity-card">
                <h3>📭 No High Probability Setups Found</h3>
                <p>The market is {{ scan.market_state.lower() }}. No pairs meet our strict 70+ score criteria.</p>
                <p><em>This is MARKET TRUTH, not a system failure. Patience is key.</em></p>
            </div>
        {% else %}
            <h2 style="margin-bottom: 20px;">🎯 High Probability Opportunities ({{ scan.very_high_probability_setups }})</h2>
            <p style="margin-bottom: 20px; color: #94a3b8;"><em>Showing ALL pairs with score ≥ 70. Pure technical analysis only.</em></p>
            
            {% for opp in scan.opportunities %}
                <div class="opportunity-card {{ opp.confidence.lower().replace('_', '-') }}">
                    <h3 style="margin-bottom: 15px;">
                        <span class="badge {{ opp.direction.lower() }}">{{ opp.direction }}</span>
                        {{ opp.pair }} • {{ opp.confluence_score }}/100
                        <span style="float: right; font-size: 0.9rem; color: #94a3b8;">{{ opp.confidence }} confidence</span>
                    </h3>
                    
                    <div class="score-breakdown">
                        <div class="score-item">
                            <div class="score-category">Structure</div>
                            <div class="score-points">{{ opp.technical_breakdown.structure }}/30</div>
                        </div>
                        <div class="score-item">
                            <div class="score-category">Momentum</div>
                            <div class="score-points">{{ opp.technical_breakdown.momentum }}/25</div>
                        </div>
                        <div class="score-item">
                            <div class="score-category">Levels</div>
                            <div class="score-points">{{ opp.technical_breakdown.levels }}/15</div>
                        </div>
                        <div class="score-item">
                            <div class="score-category">Volatility</div>
                            <div class="score-points">{{ opp.technical_breakdown.volatility }}/10</div>
                        </div>
                    </div>
                    
                    <div class="details-grid">
                        <div class="detail-item">
                            <div class="detail-label">Entry</div>
                            <div class="detail-value">{{ "%.5f"|format(opp.entry_price) }}</div>
                        </div>
                        <div class="detail-item">
                            <div class="detail-label">Stop Loss</div>
                            <div class="detail-value">{{ "%.5f"|format(opp.stop_loss) }}</div>
                        </div>
                        <div class="detail-item">
                            <div class="detail-label">Take Profit</div>
                            <div class="detail-value">{{ "%.5f"|format(opp.take_profit) }}</div>
                        </div>
                        <div class="detail-item">
                            <div class="detail-label">Risk/Reward</div>
                            <div class="detail-value">1:{{ "%.2f"|format(opp.risk_reward) }}</div>
                        </div>
                    </div>
                    
                    <div class="details-grid" style="margin-top: 10px;">
                        <div class="detail-item">
                            <div class="detail-label">Risk</div>
                            <div class="detail-value">{{ "%.1f"|format(opp.risk_pips) }} pips</div>
                        </div>
                        <div class="detail-item">
                            <div class="detail-label">Reward</div>
                            <div class="detail-value">{{ "%.1f"|format(opp.reward_pips) }} pips</div>
                        </div>
                        <div class="detail-item">
                            <div class="detail-label">Probability</div>
                            <div class="detail-value">{{ "%.0f"|format(opp.probability_tp_before_sl * 100) }}%</div>
                        </div>
                        <div class="detail-item">
                            <div class="detail-label">Duration</div>
                            <div class="detail-value">{{ opp.estimated_duration_days }} days</div>
                        </div>
                    </div>
                    
                    <div class="analysis-section">
                        <div class="analysis-title">📊 Analysis Summary</div>
                        <p>{{ opp.analysis_summary }}</p>
                        
                        <div style="margin-top: 15px; display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 15px;">
                            <div>
                                <div class="analysis-title">📈 Technicals</div>
                                <p>{{ opp.technicals_summary }}</p>
                            </div>
                            <div>
                                <div class="analysis-title">😊 Sentiment</div>
                                <p>{{ opp.sentiment_summary }}</p>
                            </div>
                        </div>
                    </div>
                    
                    <div style="margin-top: 15px; font-size: 0.9rem; color: #94a3b8;">
                        <strong>Context:</strong> {{ opp.context }} • 
                        <strong>Detected:</strong> {{ opp.detected_at[:16].replace('T', ' ') }}
                    </div>
                </div>
            {% endfor %}
        {% endif %}
        
        <div class="footer">
            <p>System Status: <strong style="color: #10b981;">ACTIVE</strong> • 
               Next scan in: <span id="countdown">15:00</span> • 
               Keep-alive: Every 10 minutes</p>
            <p>App URL: {{ app_url }} • Render Free Tier • Never sleeps</p>
            <p><small>API Usage: TwelveData 10/day • Deriv WebSocket: Active • CFTC: 24/day</small></p>
        </div>
    </div>
    
    <script>
        // Countdown timer
        let minutes = 15;
        let seconds = 0;
        
        function updateCountdown() {
            if (seconds === 0) {
                if (minutes === 0) {
                    window.location.reload();
                    return;
                }
                minutes--;
                seconds = 59;
            } else {
                seconds--;
            }
            
            document.getElementById('countdown').textContent = 
                minutes.toString().padStart(2, '0') + ':' + 
                seconds.toString().padStart(2, '0');
        }
        
        setInterval(updateCountdown, 1000);
        updateCountdown();
        
        // Run scan function
        function runScan() {
            fetch('/api/scan')
                .then(response => response.json())
                .then(data => {
                    alert(data.message);
                    setTimeout(() => location.reload(), 2000);
                })
                .catch(error => {
                    alert('Scan failed: ' + error);
                });
        }
    </script>
</body>
</html>
'''

@app.route('/')
def home():
    """Main web interface"""
    try:
        # Run a scan
        result = sentinel.run_complete_scan()
        
        return render_template_string(
            HTML_TEMPLATE,
            scan=result.to_dict(),
            app_url=config.APP_URL
        )
    except Exception as e:
        return f"Error: {str(e)}"

@app.route('/api/scan', methods=['GET'])
def api_scan():
    """API endpoint to trigger a scan"""
    try:
        result = sentinel.run_complete_scan()
        return jsonify({
            'status': 'success',
            'message': f'Technical scan {result.scan_id} completed. Found {len(result.opportunities)} pairs with score ≥ 70.',
            'data': result.to_dict()
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/health', methods=['GET'])
def api_health():
    """Health check endpoint"""
    ws_connected = sentinel.ws_manager.connected
    ws_prices = len(sentinel.ws_manager.prices)
    
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'service': 'Forex Technical Sentinel',
        'version': '5.0.0',
        'features': [
            'Pure technical analysis (80 points)',
            'Sentiment analysis (20 points)',
            'ALL 90 pairs analyzed',
            'Score ≥ 70 only filter',
            'Professional TP/SL calculation',
            'Deriv WebSocket real-time data',
            'TwelveData historical',
            'CFTC institutional sentiment'
        ],
        'stats': {
            'scans_completed': sentinel.scan_count,
            'opportunities_found': sentinel.opportunities_found,
            'websocket_connected': ws_connected,
            'websocket_prices': ws_prices,
            'total_pairs': len(ALL_PAIRS),
            'min_score_threshold': config.MIN_CONFLUENCE_SCORE
        }
    })

@app.route('/api/stats', methods=['GET'])
def api_stats():
    """Get sentinel statistics"""
    return jsonify({
        'status': 'success',
        'statistics': {
            'total_scans': sentinel.scan_count,
            'total_opportunities': sentinel.opportunities_found,
            'websocket_status': 'Connected' if sentinel.ws_manager.connected else 'Disconnected',
            'websocket_prices': len(sentinel.ws_manager.prices),
            'min_confluence_score': config.MIN_CONFLUENCE_SCORE,
            'scan_interval_minutes': config.SCAN_INTERVAL_MINUTES,
            'keep_alive_interval': config.SELF_PING_INTERVAL_MINUTES,
            'app_url': config.APP_URL,
            'api_limits': {
                'twelvedata_calls': '10/day (once per currency)',
                'deriv_websocket': 'Unlimited (with smart management)',
                'cftc_calls': '24/day (once per hour)'
            },
            'scoring_system': {
                'technical_analysis': '80 points total',
                'sentiment_analysis': '20 points total',
                'total_score': '100 points maximum',
                'display_threshold': '70+ points'
            }
        }
    })

@app.route('/api/prices', methods=['GET'])
def api_prices():
    """Get current prices from WebSocket"""
    prices = sentinel.ws_manager.get_all_prices()
    
    # Count how many pairs have prices
    pairs_with_prices = sum(1 for price in prices.values() if price > 0)
    
    return jsonify({
        'status': 'success',
        'timestamp': datetime.now().isoformat(),
        'total_pairs': len(ALL_PAIRS),
        'pairs_with_prices': pairs_with_prices,
        'sample_prices': dict(list(prices.items())[:10])  # Show first 10
    })

# ============================================================================
# KEEP-ALIVE SYSTEM - PRODUCTION READY
# ============================================================================

def self_ping():
    """Keep app awake on Render"""
    try:
        if config.APP_URL and not config.APP_URL.startswith('http://localhost'):
            url = f"{config.APP_URL.rstrip('/')}/api/health"
            try:
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    logger.info(f"✅ Keep-alive ping successful")
                else:
                    logger.warning(f"⚠ Keep-alive ping failed: {response.status_code}")
            except Exception as e:
                logger.debug(f"Keep-alive ping error: {e}")
    except Exception as e:
        logger.error(f"Keep-alive system error: {e}")

def schedule_keep_alive():
    """Schedule keep-alive pings"""
    schedule.every(config.SELF_PING_INTERVAL_MINUTES).minutes.do(self_ping)
    logger.info(f"✅ Keep-alive scheduled every {config.SELF_PING_INTERVAL_MINUTES} minutes")

def schedule_scans():
    """Schedule automatic scans"""
    schedule.every(config.SCAN_INTERVAL_MINUTES).minutes.do(run_scheduled_scan)
    logger.info(f"✅ Scans scheduled every {config.SCAN_INTERVAL_MINUTES} minutes")

def run_scheduled_scan():
    """Run scheduled scan"""
    try:
        logger.info("🔄 Running scheduled scan...")
        sentinel.run_complete_scan()
        logger.info("✅ Scheduled scan completed")
    except Exception as e:
        logger.error(f"❌ Scheduled scan failed: {e}")

# ============================================================================
# MAIN APPLICATION - PRODUCTION READY
# ============================================================================

def main():
    """Main application - Production ready"""
    logger.info("\n" + "="*60)
    logger.info("🚀 FOREX TECHNICAL SENTINEL v5.0")
    logger.info("="*60)
    logger.info("✅ Pure technical analysis (80 points)")
    logger.info("✅ Sentiment analysis (20 points)")
    logger.info("✅ ALL 90 pairs analyzed")
    logger.info("✅ ONLY filter: Score ≥ 70")
    logger.info("✅ Shows ALL qualifying pairs (no other filters)")
    logger.info("✅ Professional TP/SL calculation")
    logger.info("="*60)
    
    # Schedule tasks
    schedule_keep_alive()
    schedule_scans()
    
    # Run initial scan
    logger.info("Running initial scan...")
    try:
        result = sentinel.run_complete_scan()
        logger.info(f"✅ Initial scan completed: {len(result.opportunities)} pairs with score ≥ 70")
    except Exception as e:
        logger.error(f"⚠ Initial scan failed: {e}")
    
    # Start scheduler thread
    def run_scheduler():
        while True:
            try:
                schedule.run_pending()
                time.sleep(1)
            except Exception as e:
                logger.error(f"Scheduler error: {e}")
                time.sleep(5)
    
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    
    # Start Flask app
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"🌐 Web server starting on port {port}")
    logger.info(f"📊 Access at: {config.APP_URL}")
    logger.info("="*60)
    
    app.run(host='0.0.0.0', port=port, debug=False)

if __name__ == "__main__":
    main()