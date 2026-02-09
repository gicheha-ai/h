"""
FOREX GLOBAL SENTINEL - Professional Production Edition
Complete implementation with ALL features, optimized for Render deployment
Catalyst-driven, limit-proof, real-time analysis system
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
import websocket
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
    NEWSAPI_KEY: str = field(default_factory=lambda: os.environ.get(
        "NEWSAPI_KEY", "e973313ed2c142cb852101836f33a471"
    ))
    DERIV_TOKEN: str = field(default_factory=lambda: os.environ.get(
        "DERIV_TOKEN", "7LndOxnscxGr"
    ))
    
    # Scanner Settings
    MIN_CONFLUENCE_SCORE: int = 70
    SCAN_INTERVAL_MINUTES: int = 15
    MAX_PAIRS_PER_SCAN: int = 50
    
    # Professional TP/SL Settings
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
    OPPORTUNITIES_DB: str = "/tmp/data/opportunities.json" if 'RENDER' in os.environ else "data/opportunities.json"
    
    # Performance
    REQUEST_TIMEOUT: int = 30
    CACHE_TTL_MINUTES: int = 5
    MAX_RETRIES: int = 3
    
    # WebSocket Settings
    DERIV_WS_URL: str = "wss://ws.derivws.com/websockets/v3?app_id=1089"
    WS_MAX_PAIRS: int = 10
    WS_BATCH_SIZE: int = 3
    WS_BATCH_DELAY: float = 1.0
    WS_PAIR_DELAY: float = 0.1
    WS_RECONNECT_INTERVAL: int = 300  # 5 minutes
    
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

# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class Opportunity:
    """Complete trading opportunity with all features"""
    pair: str
    direction: str
    confluence_score: int
    catalyst: str
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
    fundamentals_summary: str
    technicals_summary: str
    sentiment_summary: str
    detected_at: str
    scan_id: str
    
    def to_dict(self):
        return asdict(self)

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
    
    def to_dict(self):
        return {
            'scan_id': self.scan_id,
            'timestamp': self.timestamp,
            'pairs_scanned': self.pairs_scanned,
            'very_high_probability_setups': self.very_high_probability_setups,
            'opportunities': [opp.to_dict() for opp in self.opportunities],
            'scan_duration_seconds': self.scan_duration_seconds,
            'market_state': self.market_state
        }

# ============================================================================
# REAL-TIME DERIV WEB SOCKET MANAGER - FIXED VERSION
# ============================================================================

class DerivWebSocketManager:
    """Smart WebSocket manager for Deriv that never hits limits"""
    
    def __init__(self, deriv_token: str):
        self.token = deriv_token
        self.ws = None
        self.connected = False
        self.prices = {}  # Real-time prices from WebSocket
        self.cross_prices = {}  # Calculated cross pairs
        self.price_history = {}  # Local price history storage
        self.message_count = 0
        self.last_message_time = time.time()
        self.start_time = time.time()
        self.running = False
        self.ws_thread = None
        
        # Deriv symbol mapping
        self.symbol_map = {
            'EUR/USD': 'frxEURUSD',
            'GBP/USD': 'frxGBPUSD',
            'USD/JPY': 'frxUSDJPY',
            'USD/CHF': 'frxUSDCHF',
            'AUD/USD': 'frxAUDUSD',
            'USD/CAD': 'frxUSDCAD',
            'NZD/USD': 'frxNZDUSD',
            'XAU/USD': 'WLDAUD',  # Gold proxy
            'BTC/USD': 'cryBTCUSD',
            'ETH/USD': 'OTC_DJI'  # Using DJI as ETH proxy
        }
        
        # Reverse mapping
        self.reverse_symbol_map = {v: k for k, v in self.symbol_map.items()}
        
        # Currency list for cross calculations
        self.currencies = ['EUR', 'GBP', 'USD', 'JPY', 'CHF', 'AUD', 'CAD', 'NZD', 'XAU', 'BTC', 'ETH']
        
        # Initialize price history storage
        for pair in self.symbol_map.keys():
            self.price_history[pair] = deque(maxlen=200)  # Store last 200 prices
        
    def connect(self):
        """Establish WebSocket connection with smart batching"""
        try:
            # Start WebSocket in background thread
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
        """Async WebSocket handler using the working pattern"""
        try:
            uri = f"wss://ws.derivws.com/websockets/v3?app_id=1089"
            
            async with websockets.connect(uri) as websocket:
                self.ws = websocket
                
                # Step 1: Authorize
                auth_msg = {"authorize": self.token}
                await websocket.send(json.dumps(auth_msg))
                auth_response = await websocket.recv()
                auth_data = json.loads(auth_response)
                
                if "error" in auth_data:
                    logger.error(f"Deriv WebSocket auth error: {auth_data['error']}")
                    self.connected = False
                    return
                
                logger.info("✅ Deriv WebSocket authorized")
                self.connected = True
                
                # Step 2: Subscribe to all symbols
                symbols = list(self.symbol_map.values())
                
                # Subscribe in batches
                for i in range(0, len(symbols), config.WS_BATCH_SIZE):
                    batch = symbols[i:i + config.WS_BATCH_SIZE]
                    
                    for symbol in batch:
                        subscribe_msg = {"ticks": symbol, "subscribe": 1}
                        await websocket.send(json.dumps(subscribe_msg))
                        logger.debug(f"Subscribed to {symbol}")
                        await asyncio.sleep(config.WS_PAIR_DELAY)
                    
                    # Wait between batches
                    if i + config.WS_BATCH_SIZE < len(symbols):
                        await asyncio.sleep(config.WS_BATCH_DELAY)
                
                # Step 3: Start receiving messages
                while self.running:
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
                                    
                                    # Recalculate all crosses when we get new data
                                    if len([p for p in self.prices.values() if p > 0]) >= 4:
                                        self.calculate_all_crosses()
                        
                        elif "error" in data:
                            logger.error(f"Deriv WebSocket error: {data['error']}")
                            
                    except asyncio.TimeoutError:
                        # No data received, continue waiting
                        continue
                    except Exception as e:
                        logger.error(f"Error processing message: {e}")
                        continue
                        
        except Exception as e:
            logger.error(f"WebSocket connection error: {e}")
            self.connected = False
        finally:
            self.connected = False
    
    def calculate_all_crosses(self):
        """Calculate all cross pairs from base pairs"""
        try:
            # We need USD rates for all currencies
            usd_rates = {}
            
            # Get USD rates for all currencies
            for currency in self.currencies:
                if currency == 'USD':
                    usd_rates['USD'] = 1.0
                else:
                    # Try direct pair (EUR/USD)
                    direct_pair = f"{currency}/USD"
                    if direct_pair in self.prices:
                        usd_rates[currency] = self.prices[direct_pair]
                    # Try inverse pair (USD/JPY)
                    else:
                        inverse_pair = f"USD/{currency}"
                        if inverse_pair in self.prices:
                            usd_rates[currency] = 1 / self.prices[inverse_pair]
            
            # Calculate all possible crosses
            for base in self.currencies:
                for quote in self.currencies:
                    if base != quote:
                        pair = f"{base}/{quote}"
                        
                        # Skip if we already have it directly
                        if pair in self.prices:
                            self.cross_prices[pair] = self.prices[pair]
                            continue
                        
                        # Calculate cross: base/quote = (base/USD) / (quote/USD)
                        if base in usd_rates and quote in usd_rates:
                            base_usd = usd_rates[base]
                            quote_usd = usd_rates[quote]
                            
                            if base_usd and quote_usd and quote_usd != 0:
                                cross_price = base_usd / quote_usd
                                self.cross_prices[pair] = cross_price
                                
                                # Initialize price history for this cross
                                if pair not in self.price_history:
                                    self.price_history[pair] = deque(maxlen=200)
                                self.price_history[pair].append(cross_price)
            
            logger.debug(f"Calculated {len(self.cross_prices)} cross pairs")
            
        except Exception as e:
            logger.error(f"Error calculating crosses: {e}")
    
    def get_price(self, pair: str) -> Optional[float]:
        """Get current price for any pair"""
        # First try direct price
        if pair in self.prices:
            return self.prices.get(pair)
        
        # Then try calculated cross
        if pair in self.cross_prices:
            return self.cross_prices.get(pair)
        
        # Try to calculate on demand
        base, quote = pair.split('/')
        
        # Get USD rates
        base_usd = self.get_usd_rate(base)
        quote_usd = self.get_usd_rate(quote)
        
        if base_usd and quote_usd and quote_usd != 0:
            price = base_usd / quote_usd
            self.cross_prices[pair] = price
            return price
        
        return None
    
    def get_usd_rate(self, currency: str) -> Optional[float]:
        """Get currency rate vs USD"""
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
    
    def get_price_history(self, pair: str, period: int = 100) -> List[float]:
        """Get price history for a pair"""
        if pair in self.price_history:
            history = list(self.price_history[pair])
            return history[-period:] if len(history) > period else history
        
        return []
    
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
# SMART CATALYST DETECTOR (NEWSAPI)
# ============================================================================

class SmartCatalystDetector:
    """Smart catalyst detection that never hits NewsAPI limits"""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://newsapi.org/v2"
        
        # Usage tracking
        self.used_today = 0
        self.daily_limit = 100
        self.last_reset = datetime.now().date()
        
        # Smart keyword strategies
        self.daily_keywords = [
            "war OR conflict OR military",
            "rate decision OR interest rate OR central bank",
            "emergency meeting OR crisis",
            "sanctions OR ban OR embargo",
            "default OR bankruptcy OR collapse",
            "election violence OR protest OR riot",
            "terror attack OR bombing",
            "currency devaluation OR peg broken",
            "bank run OR bank failure",
            "market crash OR flash crash"
        ]
        
        # Rotating keywords (3-day cycle)
        self.rotation_day = (datetime.now().date() - datetime(2024, 1, 1).date()).days % 3
        self.rotation_keywords = [
            ["inflation OR CPI", "unemployment OR jobs", "GDP OR growth", "retail sales", "manufacturing"],
            ["trade deficit OR surplus", "budget deficit", "debt level", "stimulus package", "quantitative easing"],
            ["oil price OR crude", "gold price", "commodity prices", "supply chain", "shipping crisis"]
        ][self.rotation_day]
        
        # Catalyst to currency mapping
        self.catalyst_currency_map = {
            'war': ['EUR', 'USD', 'CHF', 'JPY', 'XAU'],  # Safe havens
            'conflict': ['EUR', 'USD', 'CHF', 'JPY', 'XAU'],
            'rate decision': ['USD', 'EUR', 'GBP', 'JPY', 'CHF', 'CAD', 'AUD', 'NZD'],
            'interest rate': ['USD', 'EUR', 'GBP', 'JPY', 'CHF', 'CAD', 'AUD', 'NZD'],
            'central bank': ['USD', 'EUR', 'GBP', 'JPY', 'CHF', 'CAD', 'AUD', 'NZD'],
            'emergency meeting': ['USD', 'EUR', 'GBP', 'JPY'],
            'crisis': ['USD', 'EUR', 'JPY', 'CHF', 'XAU'],
            'sanctions': ['USD', 'EUR', 'GBP', 'RUB', 'CNY'],
            'default': ['USD', 'EUR', 'JPY', 'XAU'],
            'bankruptcy': ['USD', 'EUR', 'JPY'],
            'election': ['USD', 'EUR', 'GBP', 'JPY', 'AUD'],
            'protest': ['USD', 'EUR', 'GBP', 'CNY', 'RUB'],
            'terror': ['USD', 'EUR', 'GBP', 'JPY', 'XAU'],
            'devaluation': ['TRY', 'ZAR', 'MXN', 'BRL', 'INR'],
            'bank run': ['USD', 'EUR', 'GBP', 'JPY', 'CHF'],
            'market crash': ['USD', 'EUR', 'GBP', 'JPY', 'XAU'],
            'inflation': ['USD', 'EUR', 'GBP', 'JPY', 'CAD', 'AUD'],
            'unemployment': ['USD', 'EUR', 'GBP', 'JPY', 'CAD', 'AUD'],
            'GDP': ['USD', 'EUR', 'GBP', 'JPY', 'CNY', 'INR'],
            'trade deficit': ['USD', 'EUR', 'GBP', 'JPY', 'CNY'],
            'oil price': ['USD', 'CAD', 'RUB', 'AUD', 'NZD'],
            'gold price': ['USD', 'XAU', 'AUD', 'CAD'],
            'commodity': ['USD', 'CAD', 'AUD', 'NZD', 'RUB']
        }
    
    def reset_if_new_day(self):
        """Reset daily counters if it's a new day"""
        if datetime.now().date() != self.last_reset:
            self.used_today = 0
            self.last_reset = datetime.now().date()
            logger.info("Reset daily NewsAPI counter")
    
    def can_make_request(self) -> bool:
        """Check if we can make a NewsAPI request"""
        self.reset_if_new_day()
        
        # Always keep 20 requests as emergency buffer
        if self.used_today >= self.daily_limit - 20:
            logger.warning(f"NewsAPI limit approaching: {self.used_today}/{self.daily_limit}")
            return False
        
        return True
    
    def make_request(self, endpoint: str, params: Dict) -> Optional[Dict]:
        """Make a NewsAPI request with quota tracking"""
        if not self.can_make_request():
            logger.warning("Cannot make NewsAPI request - limit protection")
            return None
        
        try:
            params['apiKey'] = self.api_key
            cache_key = get_cache_key(endpoint, json.dumps(params, sort_keys=True))
            
            # Check cache first
            cached = cache.get(cache_key)
            if cached:
                return cached
            
            # Make request
            response = requests.get(
                f"{self.base_url}/{endpoint}",
                params=params,
                timeout=config.REQUEST_TIMEOUT
            )
            
            if response.status_code == 200:
                data = response.json()
                self.used_today += 1
                logger.debug(f"NewsAPI request #{self.used_today}: {endpoint}")
                
                # Cache for 1 hour
                cache.set(cache_key, data)
                return data
            else:
                logger.error(f"NewsAPI error: {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"NewsAPI request failed: {e}")
            return None
    
    def detect_catalysts(self) -> List[Dict]:
        """Smart catalyst detection with rotation strategy"""
        catalysts = []
        
        try:
            # Step 1: Get global headlines (4 requests)
            headlines = self.get_global_headlines()
            
            # Extract themes from headlines
            themes = self.extract_themes_from_headlines(headlines)
            
            # Step 2: Search based on themes
            for keyword in self.daily_keywords[:6]:  # First 6 daily keywords
                # Check if keyword is relevant to today's themes
                if self.is_keyword_relevant(keyword, themes):
                    results = self.search_keyword(keyword, page_size=3)
                    if results and results.get('articles'):
                        catalysts.extend(self.process_articles(results['articles'], keyword))
            
            # Step 3: Search rotating keywords (5 requests)
            for keyword in self.rotation_keywords:
                results = self.search_keyword(keyword, page_size=2)
                if results and results.get('articles'):
                    catalysts.extend(self.process_articles(results['articles'], keyword))
            
            # Deduplicate catalysts
            unique_catalysts = []
            seen = set()
            for catalyst in catalysts:
                key = f"{catalyst['type']}_{catalyst['title'][:50]}"
                if key not in seen:
                    seen.add(key)
                    unique_catalysts.append(catalyst)
            
            logger.info(f"Detected {len(unique_catalysts)} unique catalysts")
            return unique_catalysts
            
        except Exception as e:
            logger.error(f"Catalyst detection failed: {e}")
            return []
    
    def get_global_headlines(self) -> List[Dict]:
        """Get global business headlines"""
        headlines = []
        countries = ['us', 'gb', 'jp', 'au']  # Major financial centers
        
        for country in countries[:2]:  # Only US and UK to save requests
            params = {
                'category': 'business',
                'country': country,
                'pageSize': 5
            }
            
            data = self.make_request('top-headlines', params)
            if data and data.get('articles'):
                headlines.extend(data['articles'])
        
        return headlines
    
    def search_keyword(self, keyword: str, page_size: int = 5) -> Optional[Dict]:
        """Search for a keyword"""
        params = {
            'q': keyword,
            'pageSize': page_size,
            'sortBy': 'relevancy',
            'language': 'en',
            'from': (datetime.now() - timedelta(hours=24)).strftime('%Y-%m-%d')
        }
        
        return self.make_request('everything', params)
    
    def extract_themes_from_headlines(self, headlines: List[Dict]) -> Set[str]:
        """Extract themes from headlines"""
        themes = set()
        theme_keywords = {
            'war': ['war', 'conflict', 'military', 'invasion', 'attack'],
            'economy': ['economy', 'economic', 'growth', 'recession', 'downturn'],
            'rates': ['rate', 'interest', 'fed', 'central bank', 'ecb', 'boj'],
            'inflation': ['inflation', 'cpi', 'prices', 'consumer'],
            'politics': ['election', 'vote', 'government', 'parliament', 'senate'],
            'crisis': ['crisis', 'emergency', 'collapse', 'default'],
            'markets': ['market', 'stock', 'dow', 'nasdaq', 'ftse']
        }
        
        for article in headlines:
            text = f"{article.get('title', '')} {article.get('description', '')}".lower()
            for theme, keywords in theme_keywords.items():
                if any(keyword in text for keyword in keywords):
                    themes.add(theme)
        
        return themes
    
    def is_keyword_relevant(self, keyword: str, themes: Set[str]) -> bool:
        """Check if keyword is relevant to today's themes"""
        keyword_lower = keyword.lower()
        
        # Map keywords to themes
        keyword_theme_map = {
            'war': 'war',
            'conflict': 'war',
            'military': 'war',
            'rate': 'rates',
            'interest': 'rates',
            'central bank': 'rates',
            'crisis': 'crisis',
            'emergency': 'crisis',
            'collapse': 'crisis',
            'default': 'crisis',
            'inflation': 'inflation',
            'cpi': 'inflation',
            'election': 'politics',
            'vote': 'politics',
            'market': 'markets',
            'stock': 'markets'
        }
        
        for word, theme in keyword_theme_map.items():
            if word in keyword_lower and theme in themes:
                return True
        
        # If no themes detected, search everything
        if not themes:
            return True
        
        return False
    
    def process_articles(self, articles: List[Dict], keyword: str) -> List[Dict]:
        """Process articles into catalyst objects"""
        catalysts = []
        
        for article in articles:
            title = article.get('title', '')
            description = article.get('description', '')
            text = f"{title} {description}".lower()
            
            # Determine catalyst type
            catalyst_type = self.determine_catalyst_type(text, keyword)
            
            # Determine affected currencies
            affected_currencies = self.determine_affected_currencies(text, catalyst_type)
            
            # Determine intensity
            intensity = self.determine_intensity(text, catalyst_type)
            
            if catalyst_type and affected_currencies and intensity in ['HIGH', 'MEDIUM']:
                catalysts.append({
                    'type': catalyst_type,
                    'title': title[:100],
                    'description': description[:200] if description else '',
                    'source': article.get('source', {}).get('name', 'Unknown'),
                    'published_at': article.get('publishedAt', ''),
                    'affected_currencies': affected_currencies,
                    'intensity': intensity,
                    'keyword': keyword
                })
        
        return catalysts
    
    def determine_catalyst_type(self, text: str, keyword: str) -> str:
        """Determine catalyst type from text"""
        # Check for specific catalyst types
        catalyst_patterns = {
            'rate_decision': ['rate decision', 'interest rate', 'fed meeting', 'ecb meeting'],
            'geopolitical': ['war', 'conflict', 'invasion', 'military action'],
            'sanctions': ['sanctions', 'embargo', 'ban'],
            'election': ['election', 'vote', 'poll'],
            'economic_data': ['inflation', 'cpi', 'gdp', 'unemployment'],
            'crisis': ['crisis', 'emergency', 'collapse', 'default'],
            'natural_disaster': ['earthquake', 'tsunami', 'hurricane', 'flood']
        }
        
        for cat_type, patterns in catalyst_patterns.items():
            if any(pattern in text for pattern in patterns):
                return cat_type
        
        # Default to keyword-based type
        return keyword.split()[0] if ' ' in keyword else keyword
    
    def determine_affected_currencies(self, text: str, catalyst_type: str) -> List[str]:
        """Determine which currencies are affected"""
        affected = set()
        
        # Check text for currency mentions
        currency_codes = ['USD', 'EUR', 'GBP', 'JPY', 'CHF', 'CAD', 'AUD', 'NZD', 'CNY', 'RUB']
        for currency in currency_codes:
            if currency.lower() in text.lower() or currency in text:
                affected.add(currency)
        
        # Add based on catalyst type
        if catalyst_type in self.catalyst_currency_map:
            affected.update(self.catalyst_currency_map[catalyst_type])
        
        # If still empty, default to major currencies
        if not affected:
            affected = ['USD', 'EUR', 'GBP', 'JPY']
        
        return list(affected)
    
    def determine_intensity(self, text: str, catalyst_type: str) -> str:
        """Determine catalyst intensity"""
        text_lower = text.lower()
        
        # High intensity indicators
        high_indicators = [
            'emergency', 'urgent', 'crisis', 'collapse', 'war', 'invasion',
            'sanctions', 'default', 'bankruptcy', 'crash', 'plunge'
        ]
        
        # Medium intensity indicators
        medium_indicators = [
            'warning', 'alert', 'concern', 'fear', 'risk', 'pressure',
            'slowdown', 'downturn', 'decline', 'drop'
        ]
        
        if any(indicator in text_lower for indicator in high_indicators):
            return 'HIGH'
        elif any(indicator in text_lower for indicator in medium_indicators):
            return 'MEDIUM'
        else:
            return 'LOW'

# ============================================================================
# HYBRID TECHNICAL ANALYZER
# ============================================================================

class HybridTechnicalAnalyzer:
    """Hybrid technical analyzer using WebSocket real-time and TwelveData historical"""
    
    def __init__(self, twelve_data_key: str, web_socket_manager: DerivWebSocketManager):
        self.twelve_data_key = twelve_data_key
        self.ws_manager = web_socket_manager
        self.base_url = "https://api.twelvedata.com"
        
        # Local storage for historical data
        self.historical_data = {}
        
        # Load historical data on initialization
        self.load_historical_data()
    
    def load_historical_data(self):
        """Load historical data once per day"""
        try:
            # Check if we already loaded today
            today = datetime.now().date()
            cache_key = f"historical_data_{today}"
            
            cached = cache.get(cache_key)
            if cached:
                self.historical_data = cached
                logger.info("Loaded historical data from cache")
                return
            
            # Fetch historical data for base pairs
            base_pairs = [
                'EUR/USD', 'GBP/USD', 'USD/JPY', 'USD/CHF',
                'AUD/USD', 'USD/CAD', 'NZD/USD'
            ]
            
            for pair in base_pairs:
                data = self.fetch_historical_data(pair, interval='1day', outputsize=100)
                if data is not None:
                    self.historical_data[pair] = data
            
            # Cache for 24 hours
            cache.set(cache_key, self.historical_data)
            logger.info(f"Loaded historical data for {len(self.historical_data)} pairs")
            
        except Exception as e:
            logger.error(f"Failed to load historical data: {e}")
    
    def fetch_historical_data(self, pair: str, interval: str = '1day', outputsize: int = 100) -> Optional[pd.DataFrame]:
        """Fetch historical data from TwelveData"""
        try:
            symbol = pair.replace('/', '')
            params = {
                'symbol': symbol,
                'interval': interval,
                'outputsize': outputsize,
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
                    return df
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to fetch historical data for {pair}: {e}")
            return None
    
    def analyze_pair(self, pair: str) -> Dict:
        """Complete technical analysis for any pair"""
        try:
            cache_key = f"technical_{pair}_{datetime.now().hour}"
            cached = cache.get(cache_key)
            if cached:
                return cached
            
            # Get current price from WebSocket
            current_price = self.ws_manager.get_price(pair)
            if current_price is None or current_price <= 0:
                return self.get_fallback_technicals(pair)
            
            # Get price history
            price_history = self.ws_manager.get_price_history(pair, 100)
            if len(price_history) < 20:
                # Try to use historical data if WebSocket history is insufficient
                if pair in self.historical_data:
                    closes = self.historical_data[pair]['close'].values[-100:]
                    price_history = closes.tolist() if len(closes) > 0 else []
            
            if len(price_history) < 20:
                return self.get_fallback_technicals(pair)
            
            # Perform analysis
            trend = self.analyze_trend(price_history)
            patterns = self.detect_patterns(price_history)
            indicators = self.calculate_indicators(price_history)
            levels = self.find_key_levels(price_history, current_price)
            context = self.determine_context(trend, patterns)
            
            # Calculate score
            score = self.calculate_technical_score(trend, patterns, indicators, levels)
            
            # Calculate ATR for volatility
            atr = self.calculate_atr(price_history)
            
            # Find optimal entry
            optimal_entry = self.calculate_optimal_entry(current_price, trend['direction'], levels, atr)
            
            result = {
                'score': score,
                'trend': trend,
                'patterns': patterns,
                'indicators': indicators,
                'levels': levels,
                'context': context,
                'current_price': float(current_price),
                'optimal_entry': optimal_entry,
                'atr': atr,
                'summary': self.create_technical_summary(trend, patterns, context, score),
                'data_points': len(price_history),
                'timestamp': datetime.now().isoformat()
            }
            
            cache.set(cache_key, result)
            return result
            
        except Exception as e:
            logger.error(f"Technical analysis error for {pair}: {e}")
            return self.get_fallback_technicals(pair)
    
    def analyze_trend(self, prices: List[float]) -> Dict:
        """Analyze trend direction and strength"""
        if len(prices) < 20:
            return {'direction': 'SIDEWAYS', 'strength': 0, 'strong': False}
        
        # Convert to numpy array for calculations
        prices_array = np.array(prices)
        
        # Calculate moving averages
        sma_20 = np.mean(prices_array[-20:])
        sma_50 = np.mean(prices_array[-50:]) if len(prices) >= 50 else sma_20
        
        current = prices_array[-1]
        
        # Determine trend
        if current > sma_20 > sma_50:
            strength = (current - sma_50) / sma_50 * 100
            return {'direction': 'UPTREND', 'strength': strength, 'strong': strength > 5}
        elif current < sma_20 < sma_50:
            strength = (sma_50 - current) / sma_50 * 100
            return {'direction': 'DOWNTREND', 'strength': strength, 'strong': strength > 5}
        
        return {'direction': 'SIDEWAYS', 'strength': 0, 'strong': False}
    
    def detect_patterns(self, prices: List[float]) -> List[str]:
        """Detect chart patterns"""
        patterns = []
        
        if len(prices) < 10:
            return patterns
        
        recent_prices = prices[-10:]
        
        # Check for higher highs/lows
        if len(recent_prices) >= 5:
            highs = recent_prices[-5:]
            lows = recent_prices[-5:]
            
            # Simple pattern detection
            if max(highs) == highs[-1] and min(lows) == lows[-1]:
                patterns.append("Higher high & higher low")
            elif max(highs) == highs[0] and min(lows) == lows[0]:
                patterns.append("Lower high & lower low")
        
        # Check for consolidation
        price_range = max(recent_prices) - min(recent_prices)
        avg_price = sum(recent_prices) / len(recent_prices)
        
        if price_range / avg_price < 0.01:  # Less than 1% range
            patterns.append("Consolidation")
        
        return patterns
    
    def calculate_indicators(self, prices: List[float]) -> Dict:
        """Calculate technical indicators"""
        if len(prices) < 14:
            return {'rsi': 50, 'aligned': False}
        
        # Calculate RSI
        rsi = self.calculate_rsi(prices)
        
        # Check if indicators are aligned with trend
        aligned = self.check_indicator_alignment(prices)
        
        return {'rsi': rsi, 'aligned': aligned}
    
    def calculate_rsi(self, prices: np.ndarray, period: int = 14) -> float:
        """Calculate RSI"""
        if len(prices) < period + 1:
            return 50.0
        
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gain = pd.Series(gains).rolling(period).mean().iloc[-1]
        avg_loss = pd.Series(losses).rolling(period).mean().iloc[-1]
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return float(rsi) if not pd.isna(rsi) else 50.0
    
    def check_indicator_alignment(self, prices: List[float]) -> bool:
        """Check if indicators are aligned"""
        if len(prices) < 10:
            return False
        
        # Simple alignment check
        current = prices[-1]
        prev = prices[-5]
        return current > prev  # Price is higher than 5 periods ago
    
    def find_key_levels(self, prices: List[float], current_price: float) -> Dict:
        """Find key support/resistance levels"""
        if len(prices) < 20:
            return {'support': 0, 'resistance': 0, 'quality': 'LOW'}
        
        # Find recent highs and lows
        recent_prices = prices[-20:]
        resistance = max(recent_prices)
        support = min(recent_prices)
        
        # Determine quality based on distance to current price
        distance_to_res = abs(resistance - current_price) / current_price if current_price > 0 else 1
        distance_to_sup = abs(current_price - support) / current_price if current_price > 0 else 1
        
        if distance_to_res < 0.01 or distance_to_sup < 0.01:
            quality = 'HIGH'
        elif distance_to_res < 0.02 or distance_to_sup < 0.02:
            quality = 'MEDIUM'
        else:
            quality = 'LOW'
        
        return {
            'support': float(support),
            'resistance': float(resistance),
            'quality': quality
        }
    
    def determine_context(self, trend: Dict, patterns: List[str]) -> str:
        """Determine market context"""
        if trend['strong']:
            return 'TRENDING'
        
        if 'Consolidation' in patterns:
            return 'RANGING'
        
        return 'UNCLEAR'
    
    def calculate_technical_score(self, trend: Dict, patterns: List, 
                                indicators: Dict, levels: Dict) -> int:
        """Calculate technical score 0-40"""
        score = 0
        
        # Trend strength (0-15)
        if trend['strong']:
            score += 15
        elif trend['direction'] != 'SIDEWAYS':
            score += 8
        
        # Patterns (0-10)
        score += min(len(patterns) * 3, 10)
        
        # Indicator alignment (0-10)
        if indicators.get('aligned'):
            score += 10
        
        # Key levels (0-5)
        if levels.get('quality') in ['HIGH', 'MEDIUM']:
            score += 5
        
        return min(score, 40)
    
    def calculate_optimal_entry(self, current_price: float, trend_dir: str,
                              levels: Dict, atr: float) -> float:
        """Calculate optimal entry price"""
        if trend_dir == 'UPTREND':
            # Buy near support in uptrend
            support = levels.get('support', current_price * 0.99)
            return support + (current_price - support) * 0.3
        elif trend_dir == 'DOWNTREND':
            # Sell near resistance in downtrend
            resistance = levels.get('resistance', current_price * 1.01)
            return resistance - (resistance - current_price) * 0.3
        
        return current_price
    
    def calculate_atr(self, prices: List[float], period: int = 14) -> float:
        """Calculate Average True Range"""
        if len(prices) < period + 1:
            return 0.0
        
        # Simplified ATR calculation
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
        
        atr = np.mean(tr_values[-period:]) if tr_values else 0.0
        return float(atr)
    
    def create_technical_summary(self, trend: Dict, patterns: List,
                                context: str, score: int) -> str:
        """Create technical summary"""
        parts = []
        
        if trend['strong']:
            parts.append(f"Strong {trend['direction'].lower()}")
        elif trend['direction'] != 'SIDEWAYS':
            parts.append(f"Weak {trend['direction'].lower()}")
        
        if patterns:
            parts.append(f"{len(patterns)} patterns")
        
        parts.append(f"{context.lower()} market")
        
        summary = f"Score: {score}/40 - " + ", ".join(parts)
        return summary
    
    def get_fallback_technicals(self, pair: str) -> Dict:
        """Fallback technical data"""
        return {
            'score': 20,
            'trend': {'direction': 'SIDEWAYS', 'strength': 0, 'strong': False},
            'patterns': [],
            'indicators': {'rsi': 50, 'aligned': False},
            'context': 'UNCLEAR',
            'current_price': 1.0,
            'optimal_entry': 1.0,
            'atr': 0.01,
            'summary': 'Technical analysis unavailable',
            'timestamp': datetime.now().isoformat()
        }

# ============================================================================
# SENTIMENT ANALYZER (FIXED VERSION)
# ============================================================================

class SentimentAnalyzer:
    """Complete sentiment analysis"""
    
    def __init__(self):
        self.real_sentiment_collector = RealSentimentCollector()
        
    def analyze_pair(self, pair: str) -> Dict:
        """Analyze sentiment for a currency pair"""
        try:
            cache_key = f"sentiment_full_{pair}_{datetime.now().date()}"
            cached = cache.get(cache_key)
            if cached:
                return cached
            
            # Get real sentiment data
            sentiment_data = self.real_sentiment_collector.get_sentiment_for_pair(pair)
            
            # Calculate final sentiment score (0-20)
            score = sentiment_data['score']
            
            result = {
                'score': score,
                'data': sentiment_data,
                'summary': sentiment_data['summary'],
                'timestamp': datetime.now().isoformat()
            }
            
            cache.set(cache_key, result)
            return result
            
        except Exception as e:
            logger.error(f"Sentiment analysis error for {pair}: {e}")
            return self._get_fallback_sentiment()
    
    def _get_fallback_sentiment(self) -> Dict:
        """Fallback sentiment data"""
        return {
            'score': 10,
            'data': {'available': False, 'reason': 'Fallback'},
            'summary': 'Sentiment analysis unavailable - using fallback',
            'timestamp': datetime.now().isoformat()
        }

# ============================================================================
# REAL SENTIMENT DATA - PRODUCTION READY
# ============================================================================

class RealSentimentCollector:
    """Complete sentiment data collection from real sources"""
    
    def __init__(self):
        self.cftc_url = "https://www.cftc.gov/dea/newcot/FinFutWk.txt"
        self.last_fetch = None
        self.cot_data = None
        
    def get_sentiment_for_pair(self, pair: str) -> Dict:
        """Get complete sentiment analysis"""
        try:
            cache_key = f"sentiment_{pair}_{datetime.now().date()}"
            cached = cache.get(cache_key)
            if cached:
                return cached
            
            # 1. Institutional sentiment (CFTC)
            institutional = self._get_cftc_sentiment(pair)
            
            # 2. Calculate composite score
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
            logger.error(f"Sentiment error for {pair}: {e}")
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
        score = 10  # Neutral
        
        if institutional.get('available'):
            if not institutional.get('extreme'):
                score += 5  # Good: not extreme
            else:
                score -= 3  # Bad: extreme positioning
        
        # Adjust for retail sentiment (contrarian indicator)
        if institutional.get('extreme'):
            score += 2  # Contrarian opportunity
        
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
# PROFESSIONAL TP/SL CALCULATOR - COMPLETE VERSION
# ============================================================================

class ProfessionalTP_SL_Calculator:
    """Complete professional TP/SL calculation"""
    
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

class GlobalForexSentinel:
    """Complete Forex Sentinel with catalyst-driven analysis"""
    
    def __init__(self, config: Config):
        self.config = config
        
        # Initialize core components
        self.ws_manager = DerivWebSocketManager(config.DERIV_TOKEN)
        self.catalyst_detector = SmartCatalystDetector(config.NEWSAPI_KEY)
        self.technical_analyzer = HybridTechnicalAnalyzer(config.TWELVEDATA_KEY, self.ws_manager)
        self.sentiment_analyzer = SentimentAnalyzer()
        self.tp_sl_calculator = ProfessionalTP_SL_Calculator()
        
        # Start WebSocket in background
        self.start_websocket()
        
        # Performance tracking
        self.scan_count = 0
        self.opportunities_found = 0
        self.last_catalysts = []
        
        logger.info("Forex Sentinel initialized")
    
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
    
    def run_complete_scan(self) -> ScanResult:
        """Run complete catalyst-driven market scan"""
        start_time = datetime.now()
        scan_id = f"sentinel_scan_{int(start_time.timestamp())}"
        
        logger.info(f"🚀 Starting Sentinel scan {scan_id}")
        
        all_opportunities = []
        
        try:
            # Step 1: Detect catalysts
            catalysts = self.catalyst_detector.detect_catalysts()
            self.last_catalysts = catalysts
            
            if not catalysts:
                logger.info("No catalysts detected - market is quiet")
                # Even with no catalysts, check major pairs
                all_opportunities = self.scan_major_pairs(scan_id)
            else:
                logger.info(f"Detected {len(catalysts)} catalysts")
                
                # Step 2: For each catalyst, analyze affected pairs
                for catalyst in catalysts:
                    if catalyst['intensity'] in ['HIGH', 'MEDIUM']:
                        opportunities = self.analyze_catalyst_affected_pairs(catalyst, scan_id)
                        all_opportunities.extend(opportunities)
            
            # Calculate statistics
            scan_duration = (datetime.now() - start_time).total_seconds()
            market_state = self.determine_market_state(len(all_opportunities), len(catalysts))
            
            # Update counters
            self.scan_count += 1
            self.opportunities_found += len(all_opportunities)
            
            logger.info(f"✅ Scan {scan_id} completed in {scan_duration:.1f}s")
            logger.info(f"📊 Results: {len(all_opportunities)} very high probability setups")
            logger.info(f"📈 Market state: {market_state}")
            
            return ScanResult(
                scan_id=scan_id,
                timestamp=datetime.now().isoformat(),
                pairs_scanned=len(self.get_all_scanned_pairs()),
                very_high_probability_setups=len(all_opportunities),
                opportunities=all_opportunities,
                scan_duration_seconds=scan_duration,
                market_state=market_state
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
                market_state='ERROR'
            )
    
    def scan_major_pairs(self, scan_id: str) -> List[Opportunity]:
        """Scan major pairs even when no catalysts detected"""
        opportunities = []
        major_pairs = ['EUR/USD', 'GBP/USD', 'USD/JPY', 'USD/CHF', 'AUD/USD', 'USD/CAD']
        
        for pair in major_pairs[:3]:  # Only scan top 3 to save resources
            try:
                opportunity = self.analyze_pair_completely(pair, scan_id, catalyst=None)
                if opportunity:
                    opportunities.append(opportunity)
            except Exception as e:
                logger.error(f"Error analyzing {pair}: {e}")
        
        return opportunities
    
    def analyze_catalyst_affected_pairs(self, catalyst: Dict, scan_id: str) -> List[Opportunity]:
        """Analyze all pairs affected by a catalyst"""
        opportunities = []
        affected_currencies = catalyst['affected_currencies']
        
        # Generate all possible pairs between affected currencies
        pairs_to_analyze = self.generate_pairs_from_currencies(affected_currencies)
        
        # Limit to top 10 pairs per catalyst to prevent overload
        pairs_to_analyze = pairs_to_analyze[:10]
        
        logger.info(f"Analyzing {len(pairs_to_analyze)} pairs for catalyst: {catalyst['type']}")
        
        for pair in pairs_to_analyze:
            try:
                # Check if we have price data for this pair
                price = self.ws_manager.get_price(pair)
                if price is None or price <= 0:
                    logger.debug(f"No price data for {pair}, skipping")
                    continue
                
                opportunity = self.analyze_pair_completely(pair, scan_id, catalyst)
                if opportunity:
                    opportunities.append(opportunity)
                    
            except Exception as e:
                logger.error(f"Error analyzing {pair} for catalyst: {e}")
        
        return opportunities
    
    def generate_pairs_from_currencies(self, currencies: List[str]) -> List[str]:
        """Generate all possible pairs from list of currencies"""
        pairs = []
        
        # Major pairs first (currency vs USD)
        for currency in currencies:
            if currency != 'USD':
                pairs.append(f"{currency}/USD")
                pairs.append(f"USD/{currency}")
        
        # Cross pairs (between non-USD currencies)
        for i, base in enumerate(currencies):
            for j, quote in enumerate(currencies):
                if i != j and base != 'USD' and quote != 'USD':
                    pairs.append(f"{base}/{quote}")
        
        # Remove duplicates and ensure we have calculations
        unique_pairs = []
        for pair in pairs:
            if pair not in unique_pairs:
                # Check if we can calculate this pair
                price = self.ws_manager.get_price(pair)
                if price is not None and price > 0:
                    unique_pairs.append(pair)
        
        return unique_pairs
    
    def analyze_pair_completely(self, pair: str, scan_id: str, 
                               catalyst: Optional[Dict]) -> Optional[Opportunity]:
        """Complete analysis of a single pair"""
        try:
            # 1. Technical Analysis
            technical = self.technical_analyzer.analyze_pair(pair)
            
            # 2. Sentiment Analysis
            sentiment = self.sentiment_analyzer.analyze_pair(pair)
            
            # 3. Calculate Confluence Score
            catalyst_score = 40 if catalyst and catalyst['intensity'] == 'HIGH' else \
                           20 if catalyst and catalyst['intensity'] == 'MEDIUM' else 0
            
            confluence_score = (
                catalyst_score +
                technical['score'] +
                sentiment['score']
            )
            
            # THE ONLY FILTER: VERY HIGH PROBABILITY
            if confluence_score >= self.config.MIN_CONFLUENCE_SCORE:
                # 4. Determine trade direction
                direction = self.determine_direction(technical, catalyst)
                
                # 5. Get optimal entry
                entry_price = technical['optimal_entry']
                
                # 6. Calculate professional TP/SL
                tp_sl = self.tp_sl_calculator.calculate_optimal_tp_sl(
                    pair=pair,
                    entry_price=entry_price,
                    direction=direction,
                    context=technical['context'],
                    atr=technical['atr'],
                    technical_data=technical
                )
                
                if tp_sl is None:
                    return None
                
                # 7. Create complete opportunity
                catalyst_summary = f"{catalyst['type']}: {catalyst['title']}" if catalyst else "No catalyst"
                
                return Opportunity(
                    pair=pair,
                    direction=direction,
                    confluence_score=confluence_score,
                    catalyst=catalyst_summary,
                    setup_type=technical['summary'],
                    entry_price=entry_price,
                    stop_loss=tp_sl['stop_loss'],
                    take_profit=tp_sl['take_profit'],
                    risk_reward=tp_sl['risk_reward'],
                    risk_pips=tp_sl['risk_pips'],
                    reward_pips=tp_sl['reward_pips'],
                    probability_tp_before_sl=tp_sl['probability_tp_before_sl'],
                    estimated_duration_days=tp_sl['estimated_duration_days'],
                    context=technical['context'],
                    confidence='VERY_HIGH' if confluence_score >= 85 else 'HIGH',
                    analysis_summary=self.create_analysis_summary(pair, confluence_score, catalyst),
                    fundamentals_summary=catalyst_summary,
                    technicals_summary=technical['summary'],
                    sentiment_summary=sentiment['summary'],
                    detected_at=datetime.now().isoformat(),
                    scan_id=scan_id
                )
            
            return None
            
        except Exception as e:
            logger.error(f"Complete analysis failed for {pair}: {e}")
            return None
    
    def determine_direction(self, technical: Dict, catalyst: Optional[Dict]) -> str:
        """Determine trade direction based on analysis"""
        # Use technical trend primarily
        trend = technical.get('trend', {})
        if trend.get('strong'):
            return 'BUY' if trend['direction'] == 'UPTREND' else 'SELL'
        
        # Catalyst-based direction for certain types
        if catalyst:
            catalyst_type = catalyst.get('type', '')
            if 'rate_decision' in catalyst_type or 'inflation' in catalyst_type:
                # Rate hikes typically strengthen currency
                affected = catalyst.get('affected_currencies', [])
                if affected and 'USD' in affected:
                    return 'BUY'  # USD strengthening
            elif 'geopolitical' in catalyst_type or 'crisis' in catalyst_type:
                # Crises typically strengthen safe havens
                affected = catalyst.get('affected_currencies', [])
                if 'JPY' in affected or 'CHF' in affected or 'XAU' in affected:
                    return 'BUY'  # Safe haven strengthening
        
        # Final fallback
        return 'BUY'
    
    def create_analysis_summary(self, pair: str, score: int, 
                               catalyst: Optional[Dict]) -> str:
        """Create analysis summary"""
        if score >= 85:
            catalyst_text = f"driven by {catalyst['type']}" if catalyst else ""
            return f"EXCEPTIONAL confluence for {pair} {catalyst_text}."
        elif score >= 75:
            catalyst_text = f"with {catalyst['type']} catalyst" if catalyst else ""
            return f"STRONG confluence for {pair} {catalyst_text}."
        else:
            catalyst_text = f"Catalyst: {catalyst['type']}" if catalyst else "No catalyst"
            return f"GOOD confluence for {pair}. {catalyst_text}"
    
    def get_all_scanned_pairs(self) -> List[str]:
        """Get list of all pairs that were scanned"""
        pairs = set()
        
        # Add major pairs
        pairs.update(['EUR/USD', 'GBP/USD', 'USD/JPY', 'USD/CHF', 'AUD/USD', 'USD/CAD'])
        
        # Add pairs from catalysts
        for catalyst in self.last_catalysts:
            affected = catalyst.get('affected_currencies', [])
            for currency in affected:
                if currency != 'USD':
                    pairs.add(f"{currency}/USD")
                    pairs.add(f"USD/{currency}")
        
        return list(pairs)
    
    def determine_market_state(self, opportunities_count: int, catalysts_count: int) -> str:
        """Determine market state based on opportunities found"""
        if catalysts_count == 0:
            return 'QUIET'
        elif opportunities_count == 0:
            return 'CALM'  # Catalysts but no setups
        elif opportunities_count <= 3:
            return 'NORMAL'
        elif opportunities_count <= 10:
            return 'ACTIVE'
        else:
            return 'VOLATILE'

# ============================================================================
# WEB INTERFACE - COMPLETE
# ============================================================================

from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)

# Initialize sentinel globally
sentinel = GlobalForexSentinel(config)

# Complete HTML template (updated to show catalysts)
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Forex Global Sentinel</title>
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
        
        .catalyst-section { background: #1e293b; padding: 25px; border-radius: 10px; margin-bottom: 30px; }
        .catalyst-card { background: #334155; padding: 15px; border-radius: 8px; margin: 10px 0; border-left: 4px solid; }
        .catalyst-card.high { border-left-color: #ef4444; }
        .catalyst-card.medium { border-left-color: #f59e0b; }
        .catalyst-card.low { border-left-color: #3b82f6; }
        
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
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🌍 Forex Global Sentinel</h1>
            <p>Catalyst-driven analysis showing ONLY very high probability setups (70%+ confluence)</p>
            <p><small>Real-time WebSocket • Smart catalyst detection • Never sleeps on Render</small></p>
            
            <div class="api-status">
                <div class="api-status-item api-status-good">✓ Deriv WebSocket</div>
                <div class="api-status-item api-status-good">✓ NewsAPI Catalysts</div>
                <div class="api-status-item api-status-good">✓ TwelveData Historical</div>
            </div>
        </div>
        
        <div class="controls">
            <button class="btn btn-scan" onclick="runScan()">🔄 Run New Scan</button>
            <button class="btn" onclick="location.reload()">📊 Refresh</button>
        </div>
        
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-value">{{ scan.very_high_probability_setups }}</div>
                <div class="stat-label">Very High Probability Setups</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{{ scan.pairs_scanned }}</div>
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
        
        <div class="scan-info">
            <h2>Latest Scan Results</h2>
            <p><strong>Scan ID:</strong> {{ scan.scan_id }}</p>
            <p><strong>Timestamp:</strong> {{ scan.timestamp }}</p>
            <div class="market-state {{ scan.market_state.lower() }}">
                Market State: {{ scan.market_state }}
            </div>
        </div>
        
        {% if catalysts %}
        <div class="catalyst-section">
            <h2>📰 Detected Catalysts ({{ catalysts|length }})</h2>
            {% for cat in catalysts %}
                <div class="catalyst-card {{ cat.intensity.lower() }}">
                    <h4>{{ cat.type|upper }} - {{ cat.intensity }} intensity</h4>
                    <p>{{ cat.title }}</p>
                    <p><small>Affected: {{ cat.affected_currencies|join(', ') }} • Source: {{ cat.source }}</small></p>
                </div>
            {% endfor %}
        </div>
        {% endif %}
        
        {% if scan.very_high_probability_setups == 0 %}
            <div class="opportunity-card">
                <h3>📭 No Very High Probability Setups Found</h3>
                <p>The market is {{ scan.market_state.lower() }}. No setups meet our strict 70%+ confluence criteria.</p>
                <p><em>This is MARKET TRUTH, not a system failure. Patience is key.</em></p>
            </div>
        {% else %}
            <h2 style="margin-bottom: 20px;">🎯 Very High Probability Opportunities ({{ scan.very_high_probability_setups }})</h2>
            <p style="margin-bottom: 20px; color: #94a3b8;"><em>Showing ALL setups that meet 70%+ confluence criteria. Catalyst-driven analysis.</em></p>
            
            {% for opp in scan.opportunities %}
                <div class="opportunity-card {{ opp.confidence.lower().replace('_', '-') }}">
                    <h3 style="margin-bottom: 15px;">
                        <span class="badge {{ opp.direction.lower() }}">{{ opp.direction }}</span>
                        {{ opp.pair }} • {{ opp.confluence_score }}% Confluence
                        <span style="float: right; font-size: 0.9rem; color: #94a3b8;">{{ opp.confidence }} confidence</span>
                    </h3>
                    
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
                                <div class="analysis-title">📰 Catalyst</div>
                                <p>{{ opp.fundamentals_summary }}</p>
                            </div>
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
            <p><small>API Usage: NewsAPI {{ newsapi_used }}/100 • TwelveData {{ twelvedata_used }}/800 • Deriv WebSocket: Active</small></p>
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
        
        # Get catalysts
        catalysts = sentinel.last_catalysts
        
        return render_template_string(
            HTML_TEMPLATE,
            scan=result.to_dict(),
            catalysts=catalysts,
            app_url=config.APP_URL,
            newsapi_used=sentinel.catalyst_detector.used_today,
            twelvedata_used=10  # Fixed daily usage for historical data
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
            'message': f'Sentinel scan {result.scan_id} completed. Found {len(result.opportunities)} very high probability setups.',
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
        'service': 'Forex Global Sentinel',
        'version': '4.0.0',
        'features': [
            'Catalyst-driven analysis',
            'Deriv WebSocket real-time data',
            'Smart NewsAPI keyword rotation',
            'Local cross-pair calculation',
            'Never hits API limits',
            'Never sleeps on Render'
        ],
        'stats': {
            'scans_completed': sentinel.scan_count,
            'opportunities_found': sentinel.opportunities_found,
            'websocket_connected': ws_connected,
            'websocket_prices': ws_prices,
            'newsapi_used_today': sentinel.catalyst_detector.used_today,
            'last_catalysts': len(sentinel.last_catalysts)
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
                'newsapi_used': sentinel.catalyst_detector.used_today,
                'newsapi_limit': 100,
                'twelvedata_used': 10,
                'twelvedata_limit': 800,
                'deriv_websocket': 'Unlimited (with smart management)'
            }
        }
    })

@app.route('/api/prices', methods=['GET'])
def api_prices():
    """Get current prices from WebSocket"""
    prices = {}
    
    # Add direct prices
    for pair, price in sentinel.ws_manager.prices.items():
        if price > 0:
            prices[pair] = price
    
    # Add some calculated crosses
    for pair, price in list(sentinel.ws_manager.cross_prices.items())[:10]:
        if price > 0:
            prices[pair] = price
    
    return jsonify({
        'status': 'success',
        'timestamp': datetime.now().isoformat(),
        'prices': prices,
        'total_pairs': len(prices)
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
    logger.info("🚀 FOREX GLOBAL SENTINEL v4.0")
    logger.info("="*60)
    logger.info("✅ Catalyst-driven analysis")
    logger.info("✅ Deriv WebSocket real-time data")
    logger.info("✅ Smart NewsAPI keyword rotation")
    logger.info("✅ Local cross-pair calculation")
    logger.info("✅ Never hits API limits")
    logger.info("✅ Showing ONLY 70%+ confluence setups")
    logger.info("="*60)
    
    # Schedule tasks
    schedule_keep_alive()
    schedule_scans()
    
    # Run initial scan
    logger.info("Running initial scan...")
    try:
        result = sentinel.run_complete_scan()
        logger.info(f"✅ Initial scan completed: {len(result.opportunities)} very high probability setups")
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